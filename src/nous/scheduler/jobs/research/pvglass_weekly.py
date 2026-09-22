#!/usr/bin/env python3
"""光伏玻璃产业链 — 周度例行任务（调度器入口）。

建议调度：每周一 08:40（`0 40 * * 1`）——周末资讯/公告已出，盘前可用。

行为：nous pv fetch（全部自动源）→ seed（基线同步）→ signal（拐点判定）
      → digest（写 wiki 周报）→ notify（配置了通道就发消息）。
      单源失败只降级不中断；推送失败也不影响退出码。

输出约定：与 trader_daily_report 一致，最终响应里带 ===REPORT_START/END=== 块，
便于 cron/网关直接推送（即使没配推送通道也能被网关消费）。
"""

from __future__ import annotations

import sys

from nous.research.pvglass import pipeline


def main() -> int:
    # push=True：强制走推送通道，但 stdout 仍会输出（launchd 日志可查）
    outcome = pipeline.run_weekly(push=True)
    lines = outcome.summary_lines()
    body = "\n".join(lines)

    print("===REPORT_START===")
    print(body)
    print("===REPORT_END===")

    # 采集全失败时给非零退出码，便于调度器告警
    if outcome.fetches and all(f.status == "error" for f in outcome.fetches):
        print("所有数据源均失败", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
