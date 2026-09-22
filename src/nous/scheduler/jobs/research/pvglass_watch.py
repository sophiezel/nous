#!/usr/bin/env python3
"""光伏玻璃 — 观察清单跃迁告警（调度器入口）。

建议调度：每个工作日 18:30（``30 18 * * 1-5``）——港股收盘后、人工录入当天数据之后。

与 ``pvglass_weekly`` 的分工：

    pvglass_weekly  每周一推一份全量周报（**无条件**推）
    pvglass_watch   每个工作日检查一次，**只在状态跃迁时**才推

所以本任务可以天天跑而不刷屏：没有变化时只打印一行、不推送。
本任务**不采集任何数据**（纯读库计算，无网络），因此不会与采集任务抢锁。

首次运行只建基线、不推送——否则装完当天会把全部历史状态
（"S1 未触发""库存 44.7"）当成"刚发生的变化"推出去。
用 ``nous pv watch --reset-baseline`` 可重建基线。

输出约定：与 trader_daily_report 一致，最终响应里带 ===REPORT_START/END=== 块，
便于 cron/网关直接消费（即使没配推送通道也能被网关拿走）。
"""

from __future__ import annotations

import sys

from nous.core.db import get_db
from nous.research.pvglass import store, watch
from nous.research.pvglass.registry import load_registry


def main() -> int:
    store.init_db()
    registry = load_registry()
    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        outcome = watch.check(conn, registry)

    lines = [outcome.push_title()]
    lines.extend(outcome.push_body().splitlines())

    # 只在有跃迁时推送（无变化不打扰人）；推送失败不影响退出码
    if outcome.alerted:
        from nous.core.notify import send

        for res in send(outcome.push_title(), outcome.push_body()):
            lines.append(f"推送 {res.channel}: {res.status} {res.detail}"[:120])

    print("===REPORT_START===")
    print("\n".join(lines))
    print("===REPORT_END===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
