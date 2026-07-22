#!/usr/bin/env python3
"""
sim_reconciler — 每日盘后模拟交易对账

运行时段: 16:35 (post-close)
检查项:
  1. sim_trade_plans 执行完整性 (今日未执行计划)
  2. 新入池标的 slot 买入完整性 (每个新入池标的应有3次买入)
  3. sim_trades 重复交易检测
  4. recommendation_history 与 realtime_pool 一致性

用法:
    python -m src.collectors.sim_reconciler
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from nous.data.storage import get_db


def reconcile() -> list[str]:
    """执行所有对账检查，返回告警列表"""
    alerts = []
    conn = get_db(write=False)
    today = date.today().isoformat()

    try:
        # ── 1. 未执行计划检测 ─────────────────────────────
        unexecuted = conn.execute(
            "SELECT id, symbol, action, slot, created_at "
            "FROM sim_trade_plans "
            "WHERE date(created_at) = date('now') AND executed = 0 "
            "ORDER BY id"
        ).fetchall()
        if unexecuted:
            alerts.append(
                f"⚠ {len(unexecuted)} plans not executed today: "
                + ", ".join(f"{r['symbol']}/{r['action']}/slot{r['slot']}" for r in unexecuted[:10])
                + (" ..." if len(unexecuted) > 10 else "")
            )

        # ── 2. 新入池标的 slot 买入完整性 ─────────────────
        # 找出今日推荐池中 NOT 在 sim_position 中的标的 (即今日新入池标的)
        new_entries = conn.execute(
            "SELECT rp.symbol "
            "FROM realtime_pool rp "
            "WHERE rp.pool_source = 'recommend' AND rp.active = 1 "
            "AND rp.symbol NOT IN ("
            "  SELECT DISTINCT symbol FROM sim_position WHERE shares > 0"
            ") "
            "ORDER BY rp.symbol"
        ).fetchall()

        for row in new_entries:
            sym = row["symbol"]
            # 查询今日该标的的买入记录
            buys = conn.execute(
                "SELECT slot, COUNT(*) as cnt "
                "FROM sim_trades "
                "WHERE symbol = ? AND trade_date = ? AND action = 'buy' "
                "GROUP BY slot "
                "ORDER BY slot",
                (sym, today),
            ).fetchall()
            bought_slots = {b["slot"] for b in buys}

            for slot in (1, 2, 3):
                if slot not in bought_slots:
                    alerts.append(f"⚠ Symbol {sym} missing slot {slot} buy (new entry today)")

        # ── 3. 重复交易检测 ───────────────────────────────
        duplicates = conn.execute(
            "SELECT symbol, slot, action, trade_date, COUNT(*) as cnt "
            "FROM sim_trades "
            "WHERE trade_date = ? "
            "GROUP BY symbol, slot, action, trade_date "
            "HAVING cnt > 1",
            (today,),
        ).fetchall()
        if duplicates:
            for d in duplicates:
                alerts.append(
                    f"⚠ Duplicate trades detected: {d['symbol']} slot {d['slot']} "
                    f"{d['action']} x{d['cnt']} on {d['trade_date']}"
                )

        # ── 4. recommendation_history 一致性 ──────────────
        active_rec_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM recommendation_history WHERE status = 'active'"
        ).fetchone()["cnt"]

        pool_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM realtime_pool WHERE active = 1 AND pool_source = 'recommend'"
        ).fetchone()["cnt"]

        if active_rec_count != pool_count:
            alerts.append(
                f"⚠ Recommendation mismatch: {active_rec_count} active recs vs "
                f"{pool_count} realtime_pool 'recommend' entries"
            )

        # ── 5. 额外: 检查是否还有未建立 sim_position 的 sim_trades 标的 ──
        # (这可能导致持仓不一致)
        trades_not_in_pos = conn.execute(
            "SELECT DISTINCT t.symbol "
            "FROM sim_trades t "
            "LEFT JOIN sim_position p ON t.symbol = p.symbol AND p.shares > 0 "
            "WHERE t.trade_date = ? AND p.symbol IS NULL",
            (today,),
        ).fetchall()
        if trades_not_in_pos:
            syms = [r["symbol"] for r in trades_not_in_pos]
            alerts.append(
                f"⚠ {len(syms)} symbols have today's trades but no active position: {', '.join(syms[:10])}"
                + (" ..." if len(syms) > 10 else "")
            )

    finally:
        conn.close()

    return alerts


def main():
    print(f"SimReconciler — {date.today()}")
    print("=" * 45)

    alerts = reconcile()

    if not alerts:
        print("\n✅ sim reconciler: all clear")
        return 0
    else:
        print(f"\n{'=' * 45}")
        print(f"SimReconciler Report — {len(alerts)} issue(s):")
        print(f"{'=' * 45}")
        for a in alerts:
            print(f"  {a}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
