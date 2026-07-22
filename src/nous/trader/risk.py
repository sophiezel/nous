"""模拟盘交易系统 — 风控引擎

完整的 pre-trade / post-trade 风控检查：
- ATR 动态仓位计算（风险敞口归一化）
- 板块/主线/市场仓位上限
- 账户级熔断（日回撤/累计回撤/大盘熔断）
- 金字塔分批建仓管理
- 滑点估算（按流动性分4档）
- 冷却期管理
- 时间止损触发
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Callable
from pathlib import Path
import sys

from .account import Account, RiskRules
from .portfolio import Portfolio, Position
from .order import OrderBook, Order, OrderSide, OrderReason, OrderStatus


# ============================================================
# 辅助结构
# ============================================================

@dataclass
class CheckResult:
    """风控检查结果"""
    passed: bool
    reason: str = ""
    max_shares: int = 0  # 最大可买数量
    suggested_shares: int = 0  # 建议数量（ATR计算）
    slippage_pct: Decimal = Decimal("0.001")  # 预估滑点
    pyramid_stage: int = 1  # 金字塔建仓阶段


@dataclass
class PyramidPlan:
    """金字塔建仓计划"""
    total_shares: int
    stages: list[int]  # 每批数量
    stage_pcts: list[Decimal]  # 每批占比
    confirm_conditions: list[str]  # 每批确认条件


# ============================================================
# ATR 计算
# ============================================================

def compute_atr(highs: list[float], lows: list[float], closes: list[float],
                period: int = 14) -> Decimal:
    """从日线数据计算 ATR(14)

    TR = max(H-L, |H-C_prev|, |L-C_prev|)
    ATR = TR 的 EMA(14)
    """
    if len(highs) < period + 1:
        return Decimal("0")

    tr_list = []
    for i in range(1, len(highs)):
        h, l, c_prev = highs[i], lows[i], closes[i - 1]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_list.append(tr)

    # EMA 初始值用简单平均
    initial_atr = sum(tr_list[:period]) / period
    multiplier = 2.0 / (period + 1)

    atr = initial_atr
    for tr in tr_list[period:]:
        atr = (tr - atr) * multiplier + atr

    return Decimal(str(round(atr, 4)))


def compute_atr_from_df(df, period: int = 14) -> Decimal:
    """从 DataFrame 计算 ATR（兼容 akshare 输出）

    df 需有 'high', 'low', 'close' 列
    """
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    closes = df["close"].astype(float).tolist()
    return compute_atr(highs, lows, closes, period)


# ============================================================
# 滑点估算
# ============================================================

def estimate_slippage(daily_amount_yuan: float, price: Decimal) -> Decimal:
    """按日成交额分档估算滑点

    | 档位       | 日成交额      | 滑点   |
    |-----------|--------------|--------|
    | 大盘蓝筹    | >5亿         | 0.05%  |
    | 中盘       | 0.5~5亿      | 0.1%   |
    | 小盘       | 500万~5000万  | 0.3%   |
    | 微盘       | <500万        | 0.5%   |
    """
    if daily_amount_yuan >= 500_000_000:
        return Decimal("0.0005")
    elif daily_amount_yuan >= 50_000_000:
        return Decimal("0.001")
    elif daily_amount_yuan >= 5_000_000:
        return Decimal("0.003")
    else:
        return Decimal("0.005")


# ============================================================
# 风控引擎
# ============================================================

class RiskEngine:
    """模拟盘风控引擎"""

    def __init__(self, rules: RiskRules):
        self.rules = rules

        # 冷却期追踪 {sector: last_buy_datetime}
        self._cooldowns: dict[str, datetime] = {}

        # 当日记数
        self._today_buys: int = 0
        self._today_sells: int = 0
        self._today_date: str = date.today().isoformat()

    # ---- 仓位计算 ----

    def calculate_position_size(
        self,
        price: Decimal,
        atr: Decimal,
        strategy: str,
        total_asset: Decimal,
        market: str = "A",
    ) -> int:
        """ATR 动态仓位计算

        公式: shares = max_loss / (ATR × stop_distance)
        max_loss = total_asset × max_loss_pct
        """
        if atr == 0 or price == 0:
            return 0

        max_loss = total_asset * self.rules.max_loss_pct[strategy]
        stop_distance = abs(self.rules.stop_loss_pct[strategy])
        risk_per_share = atr * stop_distance

        if risk_per_share == 0:
            return 0

        shares = int(max_loss / risk_per_share)
        shares = (shares // 100) * 100  # A股100股整数倍

        # 上限校验：单票 ≤ 30% 总资产
        max_value = total_asset * self.rules.max_position_pct
        max_shares = int(max_value / price)
        max_shares = (max_shares // 100) * 100
        shares = min(shares, max_shares)

        return max(shares, 100)  # 最少100股

    def get_pyramid_plan(
        self,
        total_shares: int,
        strategy: str,
    ) -> PyramidPlan:
        """金字塔分批建仓计划

        短线：2批 [60%, 40%]
        中线：3批 [40%, 30%, 30%]
        长线：3批 [30%, 35%, 35%]
        """
        if strategy == "short_term":
            pcts = [Decimal("0.60"), Decimal("0.40")]
            conditions = [
                "高开+量比>1.5",
                "价格未跌破首笔成本-2%",
            ]
        elif strategy == "mid_term":
            pcts = [Decimal("0.40"), Decimal("0.30"), Decimal("0.30")]
            conditions = [
                "触发买入信号",
                "次日未触发止损+量能持续",
                "T+2趋势延续+RSI未超80",
            ]
        else:  # long_term
            pcts = [Decimal("0.30"), Decimal("0.35"), Decimal("0.35")]
            conditions = [
                "触发买入信号",
                "T+1趋势确认",
                "T+3趋势延续",
            ]

        stages = []
        remaining = total_shares
        for i, pct in enumerate(pcts):
            if i == len(pcts) - 1:
                stage_shares = remaining
            else:
                stage_shares = int(total_shares * pct)
                stage_shares = (stage_shares // 100) * 100
            stages.append(max(stage_shares, 100))
            remaining -= stages[-1]

        return PyramidPlan(
            total_shares=total_shares,
            stages=[s for s in stages if s > 0],
            stage_pcts=pcts[:len(stages)],
            confirm_conditions=conditions[:len(stages)],
        )

    # ---- Pre-trade 检查 ----

    def can_open_position(
        self,
        symbol: str,
        market: str,
        strategy: str,
        sector: str,
        theme: str,
        price: Decimal,
        portfolio: Portfolio,
        account: Account,
    ) -> CheckResult:
        """综合 pre-trade 检查

        返回 CheckResult，passed=True 表示可以建仓。
        """
        total_asset = account.total_asset(
            portfolio.total_market_value
        )

        # 1. 资金检查
        reserve = self.rules.min_cash_reserve
        if account.available_cash <= reserve:
            return CheckResult(False, f"现金不足：可用{account.available_cash} < 最低保留{reserve}")

        # 2. 每日买入次数
        self._reset_daily_if_needed()
        if self._today_buys >= self.rules.max_daily_buys:
            return CheckResult(False, f"当日买入次数已达上限 {self.rules.max_daily_buys}")

        # 3. 冷却期
        if self._check_cooldown(sector):
            return CheckResult(False, f"板块 {sector} 在冷却期内")

        # 4. 市场仓位上限
        market_value = portfolio.get_market_value_by_market(market)
        market_limit = self.rules.max_market_a if market == "A" else self.rules.max_market_hk
        if total_asset > 0 and (market_value / total_asset) >= market_limit:
            return CheckResult(False, f"{market}股总仓位已达上限 {market_limit:.0%}")

        # 5. 策略持仓数上限
        count = portfolio.get_count_by_strategy(strategy)
        max_count = (
            self.rules.max_short_term_count
            if strategy in ("short_term", "mid_term")
            else self.rules.max_long_term_count
        )
        if count >= max_count:
            return CheckResult(False, f"策略 {strategy} 持仓数已达上限 {max_count}")

        # 6. 板块仓位上限
        sector_value = portfolio.get_market_value_by_sector(sector)
        if total_asset > 0 and (sector_value / total_asset) >= self.rules.max_sector_pct:
            return CheckResult(False, f"板块 {sector} 仓位已达上限 {self.rules.max_sector_pct:.0%}")

        # 7. 主线仓位上限
        if theme:
            theme_value = portfolio.get_market_value_by_theme(theme)
            if total_asset > 0 and (theme_value / total_asset) >= self.rules.max_theme_pct:
                return CheckResult(False, f"主线 {theme} 仓位已达上限 {self.rules.max_theme_pct:.0%}")

        # 8. 单票仓位上限
        pos = portfolio.get(symbol)
        if pos:
            pos_pct = pos.market_value / total_asset if total_asset > 0 else Decimal("0")
            if pos_pct >= self.rules.max_position_pct:
                return CheckResult(False, f"单票 {symbol} 仓位已达上限 {self.rules.max_position_pct:.0%}")

        # 9. 已持仓则检查金字塔阶段
        pyramid_stage = 1
        if pos:
            pyramid_stage = pos.pyramid_stage + 1
            max_stages = 2 if strategy == "short_term" else 3
            if pyramid_stage > max_stages:
                return CheckResult(False, f"金字塔建仓已完成 ({pyramid_stage - 1}/{max_stages})")

        return CheckResult(
            passed=True,
            pyramid_stage=pyramid_stage,
        )

    def can_sell_position(
        self,
        pos: Position,
        reason: str,
    ) -> CheckResult:
        """检查是否可卖出

        - T+1 锁定：A股今日新仓不可卖
        - 跌停：挂单但不保证成交
        - 停牌：不可卖
        """
        if pos.is_suspended:
            return CheckResult(False, "停牌中，无法卖出")

        if not pos.is_unlocked:
            return CheckResult(False, f"T+1 锁定中，解锁日期 {pos.locked_until}")

        return CheckResult(passed=True, reason=reason)

    # ---- 熔断检查 ----

    def check_market_circuit_breaker(self, index_changes: dict[str, Decimal]) -> CheckResult:
        """大盘熔断检查

        index_changes: {"sh000001": -0.042, "hsindex": -0.035, ...}
        上证/恒生单日跌幅 >4% → 暂停买入
        """
        crash_threshold = self.rules.market_crash_threshold  # -0.04

        for idx_name, change in index_changes.items():
            if change <= crash_threshold:
                return CheckResult(
                    False,
                    f"大盘熔断：{idx_name} 跌幅 {change:.2%} > {crash_threshold:.0%}，暂停所有买入"
                )

        return CheckResult(passed=True)

    def check_account_circuit_breaker(
        self,
        account: Account,
        portfolio: Portfolio,
    ) -> CheckResult:
        """账户级熔断检查

        - 日回撤 > 8% → 停止开新仓
        - 累计回撤 > 15% → 强制减仓
        """
        total_asset = account.total_asset(portfolio.total_market_value)

        # 日回撤
        today_pnl = account.get_daily_pnl()
        if total_asset > 0:
            daily_dd = abs(today_pnl) / (total_asset - today_pnl) if today_pnl < 0 else Decimal("0")
            if daily_dd >= self.rules.daily_drawdown_limit:
                return CheckResult(
                    False,
                    f"日回撤熔断：{daily_dd:.2%} >= {self.rules.daily_drawdown_limit:.0%}，停止开新仓"
                )

        # 累计回撤
        if account.peak_asset > 0:
            cumulative_dd = (account.peak_asset - total_asset) / account.peak_asset
            if cumulative_dd >= self.rules.cumulative_drawdown_limit:
                return CheckResult(
                    False,
                    f"累计回撤熔断：{cumulative_dd:.2%} >= {self.rules.cumulative_drawdown_limit:.0%}，强制减仓至30%"
                )

        return CheckResult(passed=True)

    # ---- 移动止盈 ----

    def evaluate_exit_signals(self, pos: Position) -> list[str]:
        """评估所有退出信号，返回触发的原因列表

        由 executor 每5分钟调用。
        """
        signals = []

        # 固定止损
        if pos.check_stop_loss(self.rules):
            signals.append("stop_loss")

        # 止盈 + 移动止盈 + 保本止损
        tp_signal = pos.check_take_profit(self.rules)
        if tp_signal:
            signals.append(tp_signal)

        # 时间止损
        if pos.check_time_stop(self.rules):
            signals.append("time_stop")

        return signals

    # ---- 冷却期 ----

    def record_buy(self, sector: str):
        """记录买入，启动冷却期"""
        self._cooldowns[sector] = datetime.now()
        self._today_buys += 1

    def record_sell(self):
        self._today_sells += 1

    def _check_cooldown(self, sector: str) -> bool:
        """检查板块是否在冷却期"""
        last_time = self._cooldowns.get(sector)
        if not last_time:
            return False
        cooldown_end = last_time + timedelta(minutes=self.rules.cooldown_minutes)
        return datetime.now() < cooldown_end

    def _reset_daily_if_needed(self):
        """跨日重置计数器"""
        today = date.today().isoformat()
        if today != self._today_date:
            self._today_buys = 0
            self._today_sells = 0
            self._cooldowns.clear()
            self._today_date = today

    @property
    def remaining_buys_today(self) -> int:
        self._reset_daily_if_needed()
        return max(0, self.rules.max_daily_buys - self._today_buys)

    @property
    def remaining_trades_today(self) -> int:
        self._reset_daily_if_needed()
        return max(0, self.rules.max_daily_trades - self._today_buys - self._today_sells)


# ============================================================
# 综合风控检查（便捷函数）
# ============================================================

def full_pre_trade_check(
    risk: RiskEngine,
    symbol: str,
    market: str,
    strategy: str,
    sector: str,
    theme: str,
    price: Decimal,
    atr: Decimal,
    portfolio: Portfolio,
    account: Account,
    index_changes: dict[str, Decimal],
) -> CheckResult:
    """完整 pre-trade 检查流水线"""

    # 0. 大盘熔断
    result = risk.check_market_circuit_breaker(index_changes)
    if not result.passed:
        return result

    # 1. 账户熔断
    result = risk.check_account_circuit_breaker(account, portfolio)
    if not result.passed:
        return result

    # 2. 综合约束
    result = risk.can_open_position(
        symbol, market, strategy, sector, theme, price, portfolio, account
    )
    if not result.passed:
        return result

    # 3. ATR 仓位计算
    total_asset = account.total_asset(portfolio.total_market_value)
    suggested = risk.calculate_position_size(price, atr, strategy, total_asset, market)
    result.suggested_shares = suggested

    return result
