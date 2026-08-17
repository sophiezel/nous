"""sim_pnl_updater — 随 minute_collector 每60s运行，计算浮动盈亏

对每个 sim_position 中的持仓，使用当前价计算浮动盈亏，
写入 sim_pnl_snapshot 表。
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from nous.core.paths import screener_db

DB_PATH = screener_db()

DDL = """
CREATE TABLE IF NOT EXISTS sim_pnl_snapshot (
    symbol       TEXT NOT NULL,
    datetime     TEXT NOT NULL,
    slot         INTEGER NOT NULL DEFAULT 0,
    entry_price  REAL,
    current_price REAL,
    pnl_pct      REAL,
    pnl_amount   REAL,
    PRIMARY KEY (symbol, datetime, slot)
);
CREATE INDEX IF NOT EXISTS idx_pnl_dt ON sim_pnl_snapshot(datetime);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


def _get_latest_price(conn: sqlite3.Connection, symbol: str) -> Optional[float]:
    """从 intraday_minute 获取最新价"""
    row = conn.execute(
        "SELECT price FROM intraday_minute WHERE symbol=? ORDER BY datetime DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row and row["price"] and row["price"] > 0:
        return row["price"]
    return None


def _get_latest_price_batch(conn: sqlite3.Connection, symbols: list[str]) -> dict[str, float]:
    """批量获取最新价"""
    if not symbols:
        return {}
    
    result = {}
    for sym in symbols:
        price = _get_latest_price(conn, sym)
        if price is not None:
            result[sym] = price
    return result


def update_sim_pnl() -> int:
    """更新所有持仓的浮动盈亏
    
    Returns:
        写入的快照数量
    """
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    print("=" * 50)
    print(f"sim_pnl_updater — {now_str}")
    print("=" * 50)
    
    conn = get_db()
    try:
        # 获取所有有持仓的记录
        positions = conn.execute(
            "SELECT * FROM sim_position WHERE shares > 0"
        ).fetchall()
        
        if not positions:
            print("  [sim_pnl_updater] 当前无持仓")
            return 0
        
        positions = [dict(p) for p in positions]
        print(f"  [sim_pnl_updater] 当前持仓: {len(positions)} 条")
        
        # 按 symbol 分组
        sym_positions = {}
        for pos in positions:
            sym = pos["symbol"]
            if sym not in sym_positions:
                sym_positions[sym] = []
            sym_positions[sym].append(pos)
        
        # 批量获取最新价
        all_symbols = list(sym_positions.keys())
        prices = _get_latest_price_batch(conn, all_symbols)
        print(f"  [sim_pnl_updater] 获取到 {len(prices)} 个最新价")
        
        inserted = 0
        for sym, pos_list in sorted(sym_positions.items()):
            current_price = prices.get(sym)
            if current_price is None or current_price <= 0:
                print(f"    ~ {sym}: 无当前价，跳过")
                continue
            
            total_pnl_amount = 0
            for pos in pos_list:
                entry_price = pos["entry_price"]
                shares = pos["shares"]
                slot = pos["slot"]
                
                if entry_price is None or entry_price <= 0 or shares <= 0:
                    continue
                
                # 浮动盈亏
                pnl_amount = round((current_price - entry_price) * shares, 2)
                pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)
                total_pnl_amount += pnl_amount
                
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO sim_pnl_snapshot "
                        "(symbol, datetime, slot, entry_price, current_price, pnl_pct, pnl_amount) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (sym, now_str, slot, entry_price, current_price, pnl_pct, pnl_amount),
                    )
                    inserted += 1
                except Exception as e:
                    print(f"    ✗ {sym} slot {slot}: 写入失败 {e}")
            
            # 打印汇总
            total_shares = sum(p["shares"] for p in pos_list)
            avg_cost = sum(p["shares"] * p["entry_price"] for p in pos_list) / total_shares if total_shares > 0 else 0
            print(f"    {sym}: 持仓 {total_shares}股, 均价 {avg_cost:.2f}, 现价 {current_price:.2f}, "
                  f"浮动盈亏 {total_pnl_amount:.0f}元 ({pnl_pct:.2f}%)")
        
        conn.commit()
        now_ts = now.strftime("%H:%M:%S")
        print(f"\n  [sim_pnl_updater] {now_ts} 写入 {inserted} 条 PnL 快照")
        
        # 汇总统计
        total_pnl = conn.execute(
            "SELECT SUM(pnl_amount) as total FROM sim_pnl_snapshot WHERE datetime=?",
            (now_str,),
        ).fetchone()
        if total_pnl and total_pnl["total"]:
            print(f"  [sim_pnl_updater] 总浮动盈亏: {total_pnl['total']:.0f} 元")
        
        return inserted
    finally:
        conn.close()


def main():
    """独立运行入口"""
    count = update_sim_pnl()
    print(f"\nsim_pnl_updater 完成，写入 {count} 条")


if __name__ == "__main__":
    main()
