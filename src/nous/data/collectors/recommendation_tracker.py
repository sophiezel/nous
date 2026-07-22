#!/usr/bin/env python3
"""recommendation_tracker — 每日盘后追踪推荐标的 P&L

在收市后 (15:30+) 运行, 执行:
  1. 读取 realtime_pool (pool_source='recommend' AND active=1)
  2. 同步未在 history 中的 active 标的 → INSERT recommendation_history
  3. 标记 history 中 active 但池中不再存在的 → UPDATE status='closed'
  4. 计算已平仓推荐标的的 P&L

数据库:
  - 读: realtime_pool, recommendation_history, sim_trades, stock_daily
  - 写: recommendation_history (pnl, pnl_pct, status, exit_date)

运行方式:
    python -m src.collectors.recommendation_tracker
    python -m src.collectors.recommendation_tracker --dry-run
"""

import sys
import os
import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ── 确保可以从 src 导入 ─────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from nous.data.storage import get_db
from nous.data.collectors import heartbeat


# ══════════════════════════════════════════════════════
# DDL 表结构 (若不存在则自动创建)
# ══════════════════════════════════════════════════════

DDL_RECOMMEND_HISTORY = """
CREATE TABLE IF NOT EXISTS recommendation_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT NOT NULL,
    name              TEXT,
    market            TEXT,
    strategy_type     TEXT,
    entry_date        TEXT NOT NULL,
    exit_date         TEXT,
    status            TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','closed','stopped_out','time_exit','take_profit')),
    recommendation_date TEXT,
    source_report     TEXT,
    score             REAL,
    pnl               REAL,
    pnl_pct           REAL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rec_history_symbol ON recommendation_history(symbol);
CREATE INDEX IF NOT EXISTS idx_rec_history_status ON recommendation_history(status);
"""


# ══════════════════════════════════════════════════════
# 核心功能
# ══════════════════════════════════════════════════════

def _get_active_recommend_symbols(conn) -> set[str]:
    """读取 realtime_pool 中所有 active recommend 标的"""
    rows = conn.execute(
        "SELECT symbol FROM realtime_pool WHERE pool_source='recommend' AND active=1"
    ).fetchall()
    return {r["symbol"] for r in rows}


def _get_active_history_symbols(conn) -> dict[str, dict]:
    """读取 recommendation_history 中所有 status='active' 的记录"""
    rows = conn.execute(
        "SELECT * FROM recommendation_history WHERE status='active'"
    ).fetchall()
    result = {}
    for r in rows:
        result[r["symbol"]] = dict(r)
    return result


def _get_name(conn, symbol: str) -> str:
    """从 stock_basic 获取股票名称"""
    row = conn.execute(
        "SELECT name FROM stock_basic WHERE symbol=?", (symbol,)
    ).fetchone()
    if row and row["name"]:
        return row["name"]
    return symbol


def _get_market(symbol: str) -> str:
    """推断市场: A / H"""
    if symbol.startswith(("HK", "hk")):
        return "H"
    if symbol.startswith("0") and len(symbol) == 5:
        return "H"
    return "A"


def _sync_active_recommendations(
    conn,
    pool_symbols: set[str],
    history_symbols: dict[str, dict],
    today_str: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    """同步 active 推荐:
    
    1. 池中有但 history 中没有 → INSERT
    2. history 中 active 但池中没有 → UPDATE status='closed'

    Returns:
        (inserted_count, closed_count)
    """
    inserted = 0
    closed = 0

    # 1. 新入池
    new_symbols = pool_symbols - set(history_symbols.keys())
    for sym in sorted(new_symbols):
        name = _get_name(conn, sym)
        market = _get_market(sym)

        # 尝试从 realtime_pool 获取 strategy_type
        row = conn.execute(
            "SELECT strategy_type FROM realtime_pool WHERE symbol=? AND pool_source='recommend'",
            (sym,),
        ).fetchone()
        strategy_type = row["strategy_type"] if row and row["strategy_type"] else "long_term"

        if not dry_run:
            conn.execute(
                """INSERT INTO recommendation_history
                   (symbol, name, market, strategy_type, entry_date, status, recommendation_date)
                   VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                (sym, name, market, strategy_type, today_str, today_str),
            )
        inserted += 1
        print(f"  [tracker] + 新入池: {sym} {name} ({strategy_type})")

    # 2. 出池
    removed_symbols = set(history_symbols.keys()) - pool_symbols
    for sym in sorted(removed_symbols):
        if not dry_run:
            conn.execute(
                "UPDATE recommendation_history SET exit_date=?, status='closed' WHERE symbol=? AND status='active'",
                (today_str, sym),
            )
        closed += 1
        info = history_symbols.get(sym, {})
        print(f"  [tracker] - 出池: {sym} {info.get('name', '')}")

    return inserted, closed


def _calc_pnl_for_closed(
    conn,
    today_str: str,
    dry_run: bool = False,
) -> int:
    """计算已平仓推荐标的的 P&L
    
    读取 recommendation_history 中 status='closed' 但 pnl IS NULL 的记录,
    从 sim_trades 汇总该标的在 entry_date~exit_date 之间的买卖盈亏,
    更新到 recommendation_history.pnl 和 pnl_pct.
    
    Returns:
        更新的行数
    """
    closed_no_pnl = conn.execute(
        "SELECT * FROM recommendation_history WHERE status='closed' AND pnl IS NULL"
    ).fetchall()

    if not closed_no_pnl:
        return 0

    updated = 0
    for rec in closed_no_pnl:
        sym = rec["symbol"]
        entry_date = rec["entry_date"]
        exit_date = rec["exit_date"] or today_str

        # 方案1: 从 sim_trades 汇总买卖盈亏
        # 查询 entry_date 到 exit_date 之间该标的的所有交易
        try:
            trades = conn.execute(
                """SELECT action, shares, price, amount, pnl_amount, pnl_pct
                   FROM sim_trades
                   WHERE symbol=?
                   AND trade_date >= ?
                   AND trade_date <= ?
                   ORDER BY trade_time ASC""",
                (sym, entry_date, exit_date),
            ).fetchall()
        except Exception:
            # 如果 sim_trades 没有 trade_date 列, 用 trade_time 替代
            trades = conn.execute(
                """SELECT action, shares, price, amount, pnl_amount, pnl_pct
                   FROM sim_trades
                   WHERE symbol=?
                   AND substr(trade_time, 1, 10) >= ?
                   AND substr(trade_time, 1, 10) <= ?
                   ORDER BY trade_time ASC""",
                (sym, entry_date, exit_date),
            ).fetchall()

        if not trades:
            # 方案2: 从 stock_daily 近似计算
            # 取 entry_date 作为买入价, exit_date (或最近) 作为卖出价
            daily_rows = conn.execute(
                """SELECT trade_date, close
                   FROM stock_daily
                   WHERE symbol=?
                   AND trade_date >= ?
                   AND trade_date <= ?
                   ORDER BY trade_date ASC""",
                (sym, entry_date, exit_date),
            ).fetchall()

            if len(daily_rows) >= 2:
                buy_price = daily_rows[0]["close"]
                sell_price = daily_rows[-1]["close"]
                pnl_pct = round((sell_price - buy_price) / buy_price * 100, 2)

                if not dry_run:
                    conn.execute(
                        "UPDATE recommendation_history SET pnl=?, pnl_pct=? WHERE id=?",
                        (sell_price - buy_price, pnl_pct, rec["id"]),
                    )
                updated += 1
                print(f"  [tracker] P&L({sym}): 近似 {pnl_pct}% (日线)")
            else:
                print(f"  [tracker] P&L({sym}): 缺少日线数据, 跳过")
            continue

        # 计算实际盈亏
        total_buy_amount = 0.0
        total_buy_shares = 0
        total_sell_amount = 0.0
        total_sell_shares = 0
        direct_pnl_pct = None

        for t in trades:
            action = t["action"]
            shares = abs(t["shares"] or 0)
            price = t["price"] or 0
            amount = abs(t["amount"] or 0)

            if action == "buy":
                total_buy_amount += amount
                total_buy_shares += shares
            elif action == "sell":
                total_sell_amount += amount
                total_sell_shares += shares

            # 如果有直接 P&L 字段
            if t["pnl_pct"] is not None and direct_pnl_pct is None:
                direct_pnl_pct = t["pnl_pct"]

        if total_buy_amount > 0 and total_sell_amount > 0:
            pnl_amount = total_sell_amount - total_buy_amount
            pnl_pct = round(pnl_amount / total_buy_amount * 100, 2)

            if not dry_run:
                conn.execute(
                    "UPDATE recommendation_history SET pnl=?, pnl_pct=? WHERE id=?",
                    (pnl_amount, pnl_pct, rec["id"]),
                )
            updated += 1
            print(f"  [tracker] P&L({sym}): {pnl_pct}% (sim_trades)")
        elif direct_pnl_pct is not None:
            # 用 sim_trades 中记录的 P&L (例如卖出时的 pnl_pct)
            if not dry_run:
                conn.execute(
                    "UPDATE recommendation_history SET pnl_pct=? WHERE id=?",
                    (direct_pnl_pct, rec["id"]),
                )
            updated += 1
            print(f"  [tracker] P&L({sym}): {direct_pnl_pct}% (sim_trades 字段)")

    return updated


# ══════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════

def run_tracker(dry_run: bool = False) -> dict:
    """运行 recommendation tracker
    
    Args:
        dry_run: 如果为 True, 则不实际写入数据库

    Returns:
        {
            "active_pool": int,
            "active_history": int,
            "inserted": int,
            "closed": int,
            "pnl_updated": int,
        }
    """
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    print("=" * 60)
    print(f" recommendation_tracker — {today}" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)

    conn = get_db(write=not dry_run)
    try:
        # 确保 DDL
        conn.executescript(DDL_RECOMMEND_HISTORY)

        # 1. 读取当前池
        pool_symbols = _get_active_recommend_symbols(conn)
        print(f"\n  [tracker] 活跃 recommend 池: {len(pool_symbols)} 只")

        # 2. 读取历史
        history_symbols = _get_active_history_symbols(conn)
        print(f"  [tracker] 活跃 history: {len(history_symbols)} 只")

        # 3. 同步
        inserted, closed = _sync_active_recommendations(
            conn, pool_symbols, history_symbols, today_str, dry_run
        )
        print(f"\n  [tracker] 同步: +{inserted} 入池, -{closed} 出池")

        # 4. 计算 P&L
        pnl_updated = _calc_pnl_for_closed(conn, today_str, dry_run)
        print(f"  [tracker] P&L 更新: {pnl_updated} 条")

        if not dry_run:
            conn.commit()
            print(f"\n  [tracker] ✅ 已写入数据库")

        return {
            "active_pool": len(pool_symbols),
            "active_history": len(history_symbols),
            "inserted": inserted,
            "closed": closed,
            "pnl_updated": pnl_updated,
        }

    finally:
        if not dry_run:
            conn.close()
        else:
            conn.rollback()
            conn.close()


def main():
    """CLI 入口"""
    parser = argparse.ArgumentParser(description="每日推荐 P&L 追踪器")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览, 不写入数据库",
    )
    args = parser.parse_args()

    result = run_tracker(dry_run=args.dry_run)
    heartbeat("recommendation_tracker")
    print(f"\n  recommendation_tracker 完成")
    print(f"    活跃池: {result['active_pool']} 只")
    print(f"    活跃历史: {result['active_history']} 只")
    print(f"    新入池: {result['inserted']} 只")
    print(f"    出池: {result['closed']} 只")
    print(f"    P&L 更新: {result['pnl_updated']} 条")


if __name__ == "__main__":
    main()
