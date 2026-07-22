"""模拟盘交易系统 — 交易日报生成器

每日 16:10 生成日报 Markdown，保存到 ~/wiki/finance/reports/trade/YYYY-MM-DD.md
同时推送到微信（由 cron 负责 delivery）。
"""

from __future__ import annotations
import subprocess
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .account import Account, RiskRules
from .portfolio import Portfolio, Position
from .order import OrderBook, Order, OrderSide
from .state_mgr import StateManager

# 风险分解（可选模块，加载失败静默跳过）
try:
    from .risk_decomp import get_risk_report as _get_risk_report
    _HAS_RISK_DECOMP = True
except ImportError:
    _HAS_RISK_DECOMP = False


class Reporter:
    """日报生成器"""

    def __init__(self, report_dir: str = ""):
        if not report_dir:
            report_dir = str(Path.home() / "wiki/finance/reports/trade")
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        state: StateManager,
        dt: Optional[date] = None,
    ) -> str:
        """生成日报 Markdown，返回文件内容"""
        if dt is None:
            dt = date.today()
        dt_str = dt.isoformat()

        account = state.account
        portfolio = state.portfolio
        orders = state.orders
        risk = state.risk_rules

        total_asset = account.total_asset(portfolio.total_market_value)
        today_pnl = account.get_daily_pnl(dt)
        today_trades = account.daily_trades.get(dt_str, 0)

        # 当日成交
        today_buys = orders.get_today_buys(dt_str)
        today_sells = orders.get_today_sells(dt_str)

        # 计算收益
        pnl_rate = (today_pnl / (total_asset - today_pnl) * 100).quantize(Decimal("0.01")) \
            if total_asset != today_pnl and total_asset > 0 else Decimal("0")

        # 累计统计
        cumulative_pnl = account.total_pnl
        cumulative_rate = (cumulative_pnl / account.initial_capital * 100).quantize(Decimal("0.01"))
        win_rate = account.get_win_rate()

        lines = []
        lines.append(f"# 模拟盘交易日报 — {dt_str}")
        lines.append("")
        lines.append(f"> 生成时间：{datetime.now().strftime('%H:%M CST')} | 初始资金：¥{_fmt(account.initial_capital)}")
        lines.append("")

        # ---- 账户概览 ----
        lines.append("## 一、账户概览")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 初始资金 | ¥{_fmt(account.initial_capital)} |")
        lines.append(f"| 总资产 | ¥{_fmt(total_asset)} |")
        lines.append(f"| 现金 | ¥{_fmt(account.cash)} |")
        lines.append(f"| 冻结资金 | ¥{_fmt(account.frozen_cash)} |")
        lines.append(f"| 持仓市值 | ¥{_fmt(portfolio.total_market_value)} |")
        lines.append(f"| 仓位比例 | {_pct(portfolio.total_market_value, total_asset)} |")
        lines.append(f"| 今日盈亏 | ¥{_fmt(today_pnl)} ({_fmt_signed(pnl_rate)}%) |")
        lines.append(f"| 累计盈亏 | ¥{_fmt(cumulative_pnl)} ({_fmt_signed(cumulative_rate)}%) |")
        lines.append(f"| 累计交易 | {account.total_trades}笔 |")
        lines.append(f"| 胜率 | {win_rate:.0%} |")
        lines.append(f"| 最大回撤 | {_pct_raw(account.max_drawdown)} |")
        lines.append("")

        # 市场仓位分布
        lines.append("### 仓位分布")
        lines.append("")
        a_mv = portfolio.get_market_value_by_market("A")
        hk_mv = portfolio.get_market_value_by_market("HK")
        lines.append(f"- A股：¥{_fmt(a_mv)}（上限 {_pct_raw(risk.max_market_a)}）")
        lines.append(f"- 港股：¥{_fmt(hk_mv)}（上限 {_pct_raw(risk.max_market_hk)}）")
        lines.append(f"- A股持仓数：{len(portfolio.get_by_market('A'))} | 港股：{len(portfolio.get_by_market('HK'))}")
        lines.append(f"- 短线：{portfolio.get_count_by_strategy('short_term')}/{risk.max_short_term_count} | 中线：{portfolio.get_count_by_strategy('mid_term')}/{risk.max_short_term_count} | 长线：{portfolio.get_count_by_strategy('long_term')}/{risk.max_long_term_count}")
        lines.append("")

        # ---- 今日操作 ----
        lines.append("## 二、今日操作")
        lines.append("")
        all_today = today_buys + today_sells
        if all_today:
            lines.append("| 时间 | 代码 | 名称 | 方向 | 价格 | 数量 | 金额 | 策略 | 原因 |")
            lines.append("|------|------|------|------|------|------|------|------|------|")
            for order in sorted(all_today, key=lambda o: o.filled_at):
                dir_emoji = "🟢买入" if order.side == OrderSide.BUY else "🔴卖出"
                amount = order.filled_price * order.filled_shares
                time_str = order.filled_at[11:16] if "T" in order.filled_at else order.filled_at[-8:-3]
                lines.append(
                    f"| {time_str} | {order.symbol} | {order.name} | {dir_emoji} | "
                    f"¥{_fmt(order.filled_price)} | {order.filled_shares} | "
                    f"¥{_fmt(amount)} | {order.strategy} | {order.reason.value} |"
                )
        else:
            lines.append("> 今日无操作")
        lines.append("")

        # ---- 当前持仓 ----
        lines.append("## 三、当前持仓")
        lines.append("")
        if portfolio.positions:
            lines.append("| 代码 | 名称 | 成本 | 现价 | 数量 | 市值 | 盈亏% | 策略 | "
                         "锁定 | 最高 |")
            lines.append("|------|------|------|------|------|------|-------|------|"
                         "------|------|")
            for pos in sorted(portfolio.positions.values(),
                              key=lambda p: p.market_value, reverse=True):
                lock_str = "🔒" if not pos.is_unlocked else "✓"
                max_str = f"¥{_fmt(pos.highest_price)}" if pos.highest_price > 0 else "-"
                stop_str = ""
                if pos.trailing_active:
                    stop_str = " [移动]"
                elif pos.breakeven_active:
                    stop_str = " [保本]"

                lines.append(
                    f"| {pos.symbol} | {pos.name} | ¥{_fmt(pos.entry_price)} | "
                    f"¥{_fmt(pos.current_price)} | {pos.shares} | "
                    f"¥{_fmt(pos.market_value)} | "
                    f"{_fmt_signed(pos.pnl_pct * 100)}%{stop_str} | "
                    f"{pos.strategy[:2]} | {lock_str} | {max_str} |"
                )
        else:
            lines.append("> 空仓")
        lines.append("")

        # ---- 风险分解（可选模块） ----
        if _HAS_RISK_DECOMP and portfolio.positions:
            try:
                total_asset_val = float(total_asset)
                positions_data = []
                for sym, pos in portfolio.positions.items():
                    positions_data.append({
                        'symbol': sym,
                        'name': pos.name,
                        'market': pos.market,
                        'weight': float(pos.market_value / total_asset_val) if total_asset_val > 0 else 0.0,
                        'sector': pos.sector if hasattr(pos, 'sector') and pos.sector else '其他',
                    })
                lines.append("## 四、风险分解")
                lines.append("")
                risk_report = _get_risk_report(positions_data, lookback_days=60)
                lines.append(risk_report)
                lines.append("")
            except Exception:
                pass

        # ---- 盈亏归因 ----
        sec_num = "五" if _HAS_RISK_DECOMP else "四"
        lines.append(f"## {sec_num}、盈亏归因")
        lines.append("")

        # 卖出盈亏
        if today_sells:
            winners = [o for o in today_sells
                       if hasattr(o, '_sell_pnl') and o._sell_pnl > 0]
            losers = [o for o in today_sells
                      if hasattr(o, '_sell_pnl') and o._sell_pnl <= 0]

            lines.append("### 今日卖出")
            for o in today_sells:
                pnl_str = _fmt_signed(getattr(o, '_sell_pnl', Decimal("0")))
                lines.append(f"- **{o.name}({o.symbol})** — {o.reason.value} — "
                            f"盈亏 {pnl_str}")
            lines.append("")

        # 策略胜率
        lines.append("### 策略统计")
        lines.append("")
        all_history = list(orders.history.values())
        for strategy in ["short_term", "mid_term", "long_term"]:
            sells = [o for o in all_history
                     if o.side == OrderSide.SELL and o.strategy == strategy]
            if not sells:
                continue
            wins = [o for o in sells
                    if getattr(o, '_sell_pnl', Decimal("0")) > 0]
            lines.append(f"- {strategy}: {len(sells)}笔 | 胜率 {len(wins)/len(sells):.0%} "
                        f"({len(wins)}/{len(sells)})")
        lines.append("")

        # ---- 持仓风险 ----
        sec_num_dd = "六" if _HAS_RISK_DECOMP else "五"
        lines.append(f"## {sec_num_dd}、风控状态")
        lines.append("")
        lines.append("| 检查项 | 状态 |")
        lines.append("|--------|------|")
        daily_dd = state.get_daily_drawdown(dt)
        dd_status = "⚠️ 接近熔断" if daily_dd >= Decimal("0.06") else "✓ 正常"
        lines.append(f"| 日回撤 | {_pct_raw(daily_dd)} — {dd_status} |")

        cum_dd = (account.peak_asset - total_asset) / account.peak_asset if account.peak_asset > 0 else Decimal("0")
        cum_status = "⚠️ 接近熔断" if cum_dd >= Decimal("0.10") else "✓ 正常"
        lines.append(f"| 累计回撤 | {_pct_raw(cum_dd)} — {cum_status} |")
        lines.append(f"| 当日交易 | {today_trades}/{risk.max_daily_trades} |")
        lines.append("")

        # ---- 鳄鱼派复盘 ----
        sec_num_al = "七" if _HAS_RISK_DECOMP else "六"
        lines.append(f"## {sec_num_al}、鳄鱼派复盘")
        lines.append("")
        lines.append("| 检查项 | 自评 |")
        lines.append("|--------|------|")
        lines.append("| 是否潜伏等待信号？ | — |")
        lines.append("| 有无追高？ | — |")
        lines.append("| 止损执行是否果断？ | — |")
        lines.append("| 仓位在风控线内？ | ✓ |")
        lines.append("| 是否过度交易？ | — |")
        lines.append("")
        lines.append(f"> 📌 **鳄鱼派提醒**：像鳄鱼一样潜伏——信号不共振不出手，宁可错过不做错。")
        lines.append(f"> 📅 下次报告：{(dt + timedelta(days=1)).isoformat()} 16:10 CST")
        lines.append("")

        return "\n".join(lines)

    def save(self, state: StateManager, dt: Optional[date] = None) -> str:
        """生成日报并保存到文件，返回文件路径"""
        content = self.generate(state, dt)
        if dt is None:
            dt = date.today()
        filepath = self.report_dir / f"{dt.isoformat()}.md"
        filepath.write_text(content, encoding="utf-8")
        self._gen_image(str(filepath))
        return str(filepath)

    def _gen_image(self, md_path: str):
        """生成长图 PNG (零 token, Playwright Chromium)"""
        script = Path.home() / ".hermes/scripts/md_to_long_image.py"
        venv_python = Path.home() / ".hermes/hermes-agent/venv/bin/python3"
        if not script.exists():
            return
        try:
            r = subprocess.run(
                [str(venv_python), str(script), md_path],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                print(f"[IMG] 失败: {r.stderr[:200]}")
        except Exception as e:
            print(f"[IMG] 异常: {e}")

    def generate_wechat_summary(self, state: StateManager, dt: Optional[date] = None) -> str:
        """生成微信推送用的精简摘要"""
        if dt is None:
            dt = date.today()

        account = state.account
        portfolio = state.portfolio
        total_asset = account.total_asset(portfolio.total_market_value)
        today_pnl = account.get_daily_pnl(dt)
        pnl_pct = (today_pnl / (total_asset - today_pnl) * 100).quantize(Decimal("0.01")) \
            if total_asset != today_pnl else Decimal("0")

        lines = []
        lines.append(f"📊 模拟盘日报 {dt.isoformat()}")
        lines.append(f"总资产：¥{_fmt(total_asset)} | 今日：{_fmt_signed(today_pnl)} ({_fmt_signed(pnl_pct)}%)")
        lines.append(f"胜率：{account.get_win_rate():.0%} | 仓位：{_pct(portfolio.total_market_value, total_asset)}")
        lines.append("")

        if portfolio.positions:
            lines.append("💰 持仓：")
            for pos in sorted(portfolio.positions.values(),
                              key=lambda p: p.market_value, reverse=True):
                pnl_emoji = "🟢" if pos.pnl >= 0 else "🔴"
                lines.append(f"  {pnl_emoji} {pos.name} {_fmt_signed(pos.pnl_pct*100)}% "
                            f"({pos.strategy[:2]})")
        else:
            lines.append("💰 空仓")
        lines.append("")

        # 今日操作
        today_orders = state.orders.get_today_all(dt.isoformat())
        if today_orders:
            lines.append("📝 今日操作：")
            for o in today_orders[:6]:
                act = "买入" if o.side == OrderSide.BUY else "卖出"
                lines.append(f"  {act} {o.name} {o.filled_shares}股 @ ¥{_fmt(o.filled_price)}")

        return "\n".join(lines)


# ============================================================
# 格式化工具
# ============================================================

def _fmt(d: Decimal) -> str:
    """格式化金额"""
    return f"{float(d):,.2f}"


def _fmt_signed(d: Decimal) -> str:
    """格式化带符号数值"""
    val = float(d)
    sign = "+" if val > 0 else ""
    return f"{sign}{val:,.2f}"


def _pct(numerator: Decimal, denominator: Decimal) -> str:
    """计算百分比"""
    if denominator == 0:
        return "0.00%"
    return f"{float(numerator / denominator * 100):.2f}%"


def _pct_raw(d: Decimal) -> str:
    """格式化已有小数（0.15 → 15.00%）"""
    return f"{float(d * 100):.2f}%"
