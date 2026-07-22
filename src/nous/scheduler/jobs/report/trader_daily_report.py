#!/usr/bin/env python3
"""模拟盘交易系统 — 每日交易日报生成

交易日 16:10 运行，生成日报 Markdown 并返回微信推送内容。
"""

import sys
from datetime import date
from pathlib import Path

PROJECT_DIR = Path.home() / "code/stock-advisor"
sys.path.insert(0, str(PROJECT_DIR))

from nous.trader import StateManager, Reporter, DisciplineChecker, RiskEngine


def main():
    today = date.today()
    state_dir = str(PROJECT_DIR / "trader")

    # 加载状态
    state = StateManager(state_dir).load()

    # 每日维护（持仓天数+1）
    state.portfolio.increment_all_days()
    state.save()

    # 纪律检查
    dc = DisciplineChecker(state_dir)
    risk = RiskEngine(state.risk_rules)
    disc_result = dc.check(state.orders, risk)

    # 生成日报
    reporter = Reporter()
    filepath = reporter.save(state, today)
    print(f"日报已保存: {filepath}")

    # 微信摘要
    summary = reporter.generate_wechat_summary(state, today)

    # 附加纪律状态
    summary += "\n\n📋 纪律检查:\n" + dc.get_status_summary()

    if disc_result.violations:
        summary += "\n\n⚠️ 违规:\n" + "\n".join(f"  - {v}" for v in disc_result.violations)
    if disc_result.warnings:
        summary += "\n" + "\n".join(f"  ⚡ {w}" for w in disc_result.warnings)

    # 输出为最终响应（cron 会自动发送到微信）
    print("===REPORT_START===")
    print(summary)
    print("===REPORT_END===")

    # ---- 附加风险分解到日报文件 ----
    try:
        from nous.trader.risk_decomp import get_risk_report as _cron_risk_report
        portfolio = state.portfolio
        if portfolio.positions:
            total_asset_val = float(state.account.total_asset(portfolio.total_market_value))
            positions_data = []
            for sym, pos in portfolio.positions.items():
                positions_data.append({
                    'symbol': sym,
                    'name': pos.name,
                    'market': pos.market,
                    'weight': float(pos.market_value / total_asset_val) if total_asset_val > 0 else 0.0,
                    'sector': pos.sector if hasattr(pos, 'sector') and pos.sector else '其他',
                })
            risk_section = _cron_risk_report(positions_data)
            # 追加到已生成的日报文件
            report_path = Path(filepath)
            if report_path.exists():
                existing = report_path.read_text(encoding='utf-8')
                if "风险分解" not in existing:
                    report_path.write_text(
                        existing.rstrip() + f"\n\n## 风险分解\n\n{risk_section}\n",
                        encoding='utf-8'
                    )
    except Exception:
        pass


if __name__ == "__main__":
    main()
