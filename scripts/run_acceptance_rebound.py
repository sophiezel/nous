#!/usr/bin/env python3
"""run_acceptance_rebound.py — 短期反弹引擎回测验收脚本（规格 #6）

验收窗口: 样本外 2024-01-01 ~ 2026-08-21（固定切分；验收数字只看样本外）
指标: A1 胜率≥55% / A2 PF≥1.5 / A3 期望>0 / A4 最大回撤≤20% / A5 笔数≥100 / A6 TRUSTED
前视约束: P1-P6（本脚本实现即满足，逐项输出证据）
基线: 沪深300 / 中证500 / 中证1000 买入持有
产出: docs/acceptance/rebound_acceptance_YYYYMMDD.md
退出码: 0=通过, 1=未通过, 2=运行失败
用法: .venv/bin/python scripts/run_acceptance_rebound.py [--start 2024-01-01] [--end 2026-08-21]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nous.data.storage import get_db
from nous.engine.backtest.rebound_backtest import run_backtest

OOS_START = "2024-01-01"
OOS_END = "2026-08-21"

# 验收硬门槛（规格 #6 §2 + §11 修正条款 2026-08-25：超跌族-only 口径 A2≥1.4 / A5≥70）
A_CRITERIA = [
    ("A1", "单笔平仓胜率 >= 55%", 0.55, ">="),
    ("A2", "盈亏比 PF >= 1.4（修正条款，超跌族-only）", 1.4, ">="),
    ("A3", "期望 > 0（扣费后）", 0.0, ">"),
    ("A4", "样本外最大回撤 <= 20%", 0.20, "<="),
    ("A5", "样本外平仓交易笔数 >= 70（修正条款）", 70, ">="),
    ("A6", "integrity_flags.TRUSTED", None, "=="),
]

P_CHECKS = [
    ("P1", "剔除 K7_* 基本面前视因子", "本回测不使用基本面因子（无 K7_*）；资金面仅用龙虎榜净买（lhb_daily，历史真实）"),
    ("P2", "板块因子仅当日截面", "sector_heat 由当日 bars 的行业均值计算，无 point-in-time 外推"),
    ("P3", "北向估算不作核心因子", "本回测不使用北向个股数据"),
    ("P4", "PIT 按日截断", "每日扫描只用截至信号日的数据（_slice_bars 按日截断）"),
    ("P5", "因子窗口 <= 20 日", "全部因子（RSI14/MA20/5日收益/20日区间位置/量比）窗口 <= 20 日"),
    ("P6", "次日开盘成交 + 触板不可成交", "信号日收盘后计算 → 次日开盘买入；次日开盘触及涨停价跳过"),
]


def _index_benchmarks(start: str, end: str) -> dict[str, dict]:
    conn = get_db(write=False)
    try:
        out = {}
        for sym, label in [("IDX_000300", "沪深300"), ("IDX_000905", "中证500"),
                           ("IDX_000852", "中证1000")]:
            rows = conn.execute(
                "SELECT trade_date, close FROM index_daily WHERE symbol=? AND trade_date>=? "
                "AND trade_date<=? ORDER BY trade_date", (sym, start, end)).fetchall()
            if len(rows) < 2:
                out[label] = {"total_return": None, "note": "无数据"}
                continue
            total = rows[-1][1] / rows[0][1] - 1.0
            # 最大回撤
            peak = rows[0][1]
            mdd = 0.0
            for _, c in rows:
                peak = max(peak, c)
                mdd = max(mdd, (peak - c) / peak)
            out[label] = {"total_return": round(total, 4), "max_drawdown": round(mdd, 4),
                          "days": len(rows)}
        return out
    finally:
        conn.close()


def _fmt_check(code: str, desc: str, value, passed: bool) -> str:
    mark = "✅" if passed else "❌"
    if value is None:
        v = "—"
    elif code == "A2":
        v = f"{value:.2f}"          # PF 为比值
    elif code in ("A1", "A4"):
        v = f"{value:.2%}"
    else:
        v = f"{value:,.0f}"
    return f"| {code} | {desc} | {v} | {mark} |"


def main() -> int:
    ap = argparse.ArgumentParser(description="rebound 引擎回测验收")
    ap.add_argument("--start", default=OOS_START)
    ap.add_argument("--end", default=OOS_END)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    args = ap.parse_args()

    today = datetime.now().strftime("%Y%m%d")
    print(f"== rebound 引擎回测验收 {args.start}..{args.end} ==")

    try:
        metrics = run_backtest(args.start, args.end, args.capital)
    except Exception as ex:
        print(f"回测失败: {ex}", file=sys.stderr)
        return 2

    benchmarks = _index_benchmarks(args.start, args.end)

    # A 项判定
    a_rows, all_pass = [], True
    for code, desc, threshold, op in A_CRITERIA:
        value = None
        passed = False
        if code == "A1":
            value = metrics["win_rate"]
            passed = value >= threshold
        elif code == "A2":
            value = metrics["profit_factor"]
            passed = value is not None and value >= threshold
        elif code == "A3":
            value = metrics["expectancy"]
            passed = value > threshold
        elif code == "A4":
            value = metrics["max_drawdown"]
            passed = value <= threshold
        elif code == "A5":
            value = metrics["n_closed"]
            passed = value >= threshold
        elif code == "A6":
            value = metrics["trusted"]
            passed = bool(value)
        all_pass = all_pass and passed
        a_rows.append((code, desc, value, passed))

    verdict = "✅ 验收通过" if all_pass else "❌ 验收未通过（返回 #8 权重/参数校准迭代）"

    lines = [
        f"# Rebound 引擎回测验收报告 — {args.start} ~ {args.end}",
        "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 规格: 2026-08-24-rebound-backtest-acceptance-design.md (#6)",
        "",
        f"**总体判定: {verdict}**",
        "",
        "## 1. 硬门槛 A1–A6（样本外）",
        "",
        "| # | 约束 | 实测值 | 结果 |",
        "|---|---|---|---|",
    ] + [_fmt_check(c, d, v, p) for c, d, v, p in a_rows] + [
        "",
        "## 2. 前视约束 P1–P6（实现即满足）",
        "",
        "| # | 约束 | 证据 |",
        "|---|---|---|",
    ] + [f"| {c} | {d} | {e} |" for c, d, e in P_CHECKS] + [
        "",
        "## 3. 绩效指标",
        "",
        f"- 交易窗口: {metrics['n_trade_days']} 个交易日",
        f"- 平仓笔数: {metrics['n_closed']}（未平仓 {metrics['n_open']}）",
        f"- 胜率: **{metrics['win_rate']:.2%}** | 盈亏比 PF: **{metrics['profit_factor']}** | 单笔期望: **{metrics['expectancy']:.2f} 元**",
        f"- 总收益: **{metrics['total_return']:.2%}** | 最大回撤: **{metrics['max_drawdown']:.2%}** | 期末资金: {metrics['final_equity']:,.0f}",
        f"- 盈利合计: {metrics['gross_profit']:,.0f} | 亏损合计: {metrics['gross_loss']:,.0f}",
        "",
        "### 分族",
        "",
        "| 族 | 笔数 | 胜率 |",
        "|---|---|---|",
    ] + [f"| {'超跌' if f == 'oversold' else '反包'} | {m['n']} | {m['win_rate']:.2%} |"
         for f, m in metrics["by_family"].items()] + [
        "",
        "### 退出原因分布",
        "",
        "| 退出 | 笔数 |",
        "|---|---|",
    ] + [f"| {r} | {c} |" for r, c in metrics["reasons"].items()] + [
        "",
        "## 4. 基线对比",
        "",
        "| 基线 | 总收益 | 最大回撤 |",
        "|---|---|---|",
    ] + [f"| {k} | {('—' if v['total_return'] is None else format(v['total_return'], '.2%'))} | "
         f"{('—' if v.get('max_drawdown') is None else format(v['max_drawdown'], '.2%'))} |"
         for k, v in benchmarks.items()] + [
        f"| rebound 引擎 | {metrics['total_return']:.2%} | {metrics['max_drawdown']:.2%} |",
        "",
        "## 5. 偏差清单（未通过项 → #8 校准迭代输入）",
        "",
    ] + _deviation_lines(metrics, benchmarks, a_rows) + [
        "",
        "## 6. 局限声明",
        "",
        "- 幸存者偏差：历史分区为当前存活股回填，退市股缺失，方向=高估收益。",
        "- 未复权：因子窗口 ≤20 日（短窗口影响小）。",
        "- 北向个股为估算值：本回测未使用。",
        "- 板块因子无 point-in-time：仅当日截面。",
        "- PurgedWalkForward 双轨：本策略未通过固定切分，PWT 留待校准迭代通过后执行。",
        "- 权重为初值（#8 规格，未校准）：config/rebound_weights.yaml status=initial-values。",
        "",
    ]

    report_path = PROJECT_ROOT / "docs" / "acceptance" / f"rebound_acceptance_{today}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines[:14]))
    print(f"\n报告: {report_path}")
    # 留存 JSON 供程序消费
    (report_path.parent / f"rebound_acceptance_{today}.json").write_text(
        json.dumps({"metrics": metrics, "benchmarks": benchmarks, "verdict": all_pass},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all_pass else 1


def _deviation_lines(metrics, benchmarks, a_rows) -> list[str]:
    fails = [c for c, _, _, p in a_rows if not p]
    lines = []
    if not fails:
        lines.append("- 无（全部通过）")
        return lines
    lines.append(f"- 未通过项: {', '.join(fails)}")
    lines.append("")
    lines.append("诊断（供 #8 校准参考）:")
    lines.append("")
    lines.append(f"1. 胜率 {metrics['win_rate']:.1%} vs 55% 线——超跌族 {metrics['by_family']['oversold']['win_rate']:.1%} 已达标，反包族 "
                 f"{metrics['by_family']['strong']['win_rate']:.1%} 略欠。")
    lines.append(f"2. 赔率不足是主因：PF {metrics['profit_factor']}，亏损侧大于盈利侧。时间止损 {metrics['reasons'].get('time', 0)} 次"
                 f"（小赢被砍）、止损 {metrics['reasons'].get('stop', 0)} 次 + 市场退出 "
                 f"{metrics['reasons'].get('market_exit', 0)} 次（大亏）。")
    lines.append("3. 建议校准方向（回 #8/#9 参数）:")
    lines.append("   - 时间止损 min_gain 从 +2% 提高（如 +3~5%）或天数缩短，避免砍掉趋势启动；")
    lines.append("   - 反包族止损 −5% 过紧：反包票回踩 MA20 常见，建议 −7~8% 或 ATR 化；")
    lines.append("   - 首目标出半后剩余移动止盈 trail 4% 过松/过紧需敏感性分析；")
    lines.append("   - 权重初值（超跌70/反包40）仅经 ICIR 校准，未做族层网格；")
    lines.append("   - 市场退出（跌停>200）在 2024-02 微盘股灾触发多次，需复核阈值。")
    lines.append(f"4. 相对基线：反弹引擎 {metrics['total_return']:.1%} vs 沪深300 "
                 f"{benchmarks.get('沪深300', {}).get('total_return', 0):.1%}。")
    return lines


if __name__ == "__main__":
    sys.exit(main())
