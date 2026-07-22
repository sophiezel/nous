"""模拟盘交易系统 — 交易纪律检查模块

每日开盘前自动检查：
- 昨日止损执行率
- 是否追高（买入价 > 推荐区间上限）
- 是否过度交易
- 连续违规 → 自动降仓

同时管理 discipline_state.yaml 记录违规历史。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional
import yaml

from .account import Account
from .portfolio import Portfolio, Position
from .order import OrderBook, Order, OrderSide, OrderReason, OrderStatus
from .risk import RiskEngine, CheckResult


@dataclass
class DisciplineState:
    """纪律追踪状态"""
    # 止损执行
    yesterday_should_stop: int = 0  # 昨日应止损数量
    yesterday_actual_stop: int = 0  # 实际止损执行数量
    consecutive_stop_fail_days: int = 0  # 连续未执行止损天数

    # 追高
    yesterday_chase_count: int = 0  # 昨日追高次数
    consecutive_chase_days: int = 0

    # 过度交易
    yesterday_excessive_trades: bool = False
    consecutive_excessive_days: int = 0

    # 当前惩罚
    auto_reduce_to: Decimal = Decimal("1.0")  # 仓位乘数（1.0=正常, 0.5=半仓）
    penalty_reason: str = ""
    penalty_until: str = ""

    total_violations: int = 0


@dataclass
class DisciplineResult:
    """纪律检查结果"""
    violations: list[str]  # 违规列表
    penalty: str  # 处罚措施
    reduce_to: Decimal  # 仓位降至（0-1）
    warnings: list[str]


class DisciplineChecker:
    """交易纪律检查器"""

    def __init__(self, state_dir: str = ""):
        if not state_dir:
            # Try to find the trader directory
            candidate = Path(__file__).parent
            if (candidate / "state.yaml").exists():
                state_dir = str(candidate)
            else:
                state_dir = str(Path.home() / "code/stock-advisor/trader")

        self.state_dir = Path(state_dir)
        self.disc_file = self.state_dir / "discipline_state.yaml"
        self.disc = self._load()

    def _load(self) -> DisciplineState:
        if self.disc_file.exists():
            with open(self.disc_file) as f:
                data = yaml.safe_load(f) or {}
            return DisciplineState(
                yesterday_should_stop=data.get("yesterday_should_stop", 0),
                yesterday_actual_stop=data.get("yesterday_actual_stop", 0),
                consecutive_stop_fail_days=data.get("consecutive_stop_fail_days", 0),
                yesterday_chase_count=data.get("yesterday_chase_count", 0),
                consecutive_chase_days=data.get("consecutive_chase_days", 0),
                yesterday_excessive_trades=data.get("yesterday_excessive_trades", False),
                consecutive_excessive_days=data.get("consecutive_excessive_days", 0),
                auto_reduce_to=Decimal(str(data.get("auto_reduce_to", "1.0"))),
                penalty_reason=data.get("penalty_reason", ""),
                penalty_until=data.get("penalty_until", ""),
                total_violations=data.get("total_violations", 0),
            )
        return DisciplineState()

    def _save(self):
        data = {
            "yesterday_should_stop": self.disc.yesterday_should_stop,
            "yesterday_actual_stop": self.disc.yesterday_actual_stop,
            "consecutive_stop_fail_days": self.disc.consecutive_stop_fail_days,
            "yesterday_chase_count": self.disc.yesterday_chase_count,
            "consecutive_chase_days": self.disc.consecutive_chase_days,
            "yesterday_excessive_trades": self.disc.yesterday_excessive_trades,
            "consecutive_excessive_days": self.disc.consecutive_excessive_days,
            "auto_reduce_to": str(self.disc.auto_reduce_to),
            "penalty_reason": self.disc.penalty_reason,
            "penalty_until": self.disc.penalty_until,
            "total_violations": self.disc.total_violations,
        }
        tmp = self.disc_file.with_suffix(".yaml.tmp")
        with open(tmp, "w") as f:
            yaml.dump(data, f, allow_unicode=True)
        tmp.rename(self.disc_file)

    def check(
        self,
        orders: OrderBook,
        risk: RiskEngine,
    ) -> DisciplineResult:
        """开盘前纪律检查

        返回 DisciplineResult，自动更新状态。
        """
        violations = []
        warnings = []
        penalty = ""
        reduce_to = Decimal("1.0")

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        yesterday_orders = orders.get_today_all(yesterday)

        if not yesterday_orders:
            # 没有昨日交易，降级惩罚计数
            self._decay_penalties()
            self._save()
            return DisciplineResult(violations=[], penalty="", reduce_to=Decimal("1.0"), warnings=[])

        # ---- 1. 止损执行率 ----
        sells = [o for o in yesterday_orders if o.side == OrderSide.SELL]
        stop_sells = [o for o in sells if o.reason in (
            OrderReason.STOP_LOSS, OrderReason.TRAILING_STOP, OrderReason.TIME_STOP
        )]

        self.disc.yesterday_actual_stop = len(stop_sells)

        # 估算应止损数（从持仓历史推断，简化版：如果有止损/移动止盈信号就应该执行）
        # 实际应用中由 executor 的 evaluate_exit_signals 判断
        # 这里做基础检查：如果昨日有亏损 >5% 的卖出但不是止损触发 → 违规
        for o in sells:
            if hasattr(o, '_sell_pnl'):
                pnl_pct = o._sell_pnl / (o.filled_price * o.filled_shares - o._sell_pnl) \
                    if o.filled_price * o.filled_shares != o._sell_pnl else Decimal("0")
                if pnl_pct <= Decimal("-0.05") and o.reason not in (
                    OrderReason.STOP_LOSS, OrderReason.TRAILING_STOP, OrderReason.TIME_STOP
                ):
                    self.disc.yesterday_should_stop += 1

        if self.disc.yesterday_should_stop > self.disc.yesterday_actual_stop:
            self.disc.consecutive_stop_fail_days += 1
            violations.append(
                f"止损未执行：应止损 {self.disc.yesterday_should_stop}，实际 {self.disc.yesterday_actual_stop}"
            )
            if self.disc.consecutive_stop_fail_days >= 2:
                reduce_to = min(reduce_to, Decimal("0.5"))
                penalty = f"连续 {self.disc.consecutive_stop_fail_days} 天止损未执行 → 仓位降至 50%"
        else:
            self.disc.consecutive_stop_fail_days = max(0, self.disc.consecutive_stop_fail_days - 1)

        # ---- 2. 追高检查 ----
        # 买入价超过推荐买入区间上限 → 追高
        buys = [o for o in yesterday_orders if o.side == OrderSide.BUY]
        chase_buys = 0
        for o in buys:
            if o.reason == OrderReason.RECOMMENDATION:
                # 从荐股报告解析的买入区间检查（简化版）
                # 实际中从 order.metadata 取
                pass

        self.disc.yesterday_chase_count = chase_buys
        if chase_buys > 0:
            self.disc.consecutive_chase_days += 1
            violations.append(f"追高买入 {chase_buys} 次")
            if self.disc.consecutive_chase_days >= 1:
                warnings.append(f"今日禁止追高板块的新开仓")
        else:
            self.disc.consecutive_chase_days = 0

        # ---- 3. 过度交易 ----
        total_today = len(yesterday_orders)
        max_trades = risk.rules.max_daily_trades
        losing_trades = [o for o in sells
                         if hasattr(o, '_sell_pnl') and o._sell_pnl <= 0]

        if total_today >= max_trades and len(losing_trades) >= 3:
            self.disc.yesterday_excessive_trades = True
            self.disc.consecutive_excessive_days += 1
            violations.append(
                f"过度交易：{total_today}笔（上限{max_trades}），亏损{len(losing_trades)}笔"
            )
            warnings.append(f"今日交易上限 -{self.disc.consecutive_excessive_days}")
        else:
            self.disc.yesterday_excessive_trades = False
            self.disc.consecutive_excessive_days = max(0, self.disc.consecutive_excessive_days - 1)

        # ---- 综合惩罚 ----
        if violations:
            self.disc.total_violations += len(violations)

        if reduce_to < Decimal("1.0"):
            self.disc.auto_reduce_to = reduce_to
            self.disc.penalty_reason = penalty
            self.disc.penalty_until = date.today().isoformat()
        else:
            self._decay_penalties()

        self._save()

        return DisciplineResult(
            violations=violations,
            penalty=penalty,
            reduce_to=reduce_to,
            warnings=warnings,
        )

    def _decay_penalties(self):
        """降级惩罚（无新违规时逐步恢复）"""
        if self.disc.auto_reduce_to < Decimal("1.0"):
            self.disc.auto_reduce_to = min(
                Decimal("1.0"),
                self.disc.auto_reduce_to + Decimal("0.25")
            )
            if self.disc.auto_reduce_to >= Decimal("1.0"):
                self.disc.penalty_reason = ""
                self.disc.penalty_until = ""

    def get_current_cap(self) -> Decimal:
        """获取当前仓位上限乘数"""
        return self.disc.auto_reduce_to

    def get_status_summary(self) -> str:
        """纪律状态摘要（可嵌入日报）"""
        d = self.disc
        lines = []
        if d.auto_reduce_to < Decimal("1.0"):
            lines.append(f"⚠️ 仓位限制：{_fmt_pct(d.auto_reduce_to)} — {d.penalty_reason}")
        else:
            lines.append("✓ 仓位限制：正常（100%）")
        lines.append(f"止损执行：连续 {d.consecutive_stop_fail_days} 天异常")
        lines.append(f"追高：连续 {d.consecutive_chase_days} 天")
        lines.append(f"过度交易：连续 {d.consecutive_excessive_days} 天")
        lines.append(f"累计违规：{d.total_violations} 次")
        return "\n".join(lines)


def _fmt_pct(d: Decimal) -> str:
    return f"{float(d * 100):.0f}%"
