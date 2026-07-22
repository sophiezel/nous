"""模拟盘交易系统 — 账户管理模块

管理模拟账户资金：现金、冻结资金、总资产、盈亏统计。
支持 YAML 持久化，每次状态变更自动保存。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class Account:
    """模拟账户"""

    initial_capital: Decimal = Decimal("1000000.00")
    cash: Decimal = Decimal("1000000.00")
    frozen_cash: Decimal = Decimal("0.00")  # 挂单冻结
    total_deposit: Decimal = Decimal("0.00")
    total_withdraw: Decimal = Decimal("0.00")
    created_date: str = field(default_factory=lambda: date.today().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    # 累计统计
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl: Decimal = Decimal("0.00")
    best_trade_pnl: Decimal = Decimal("0.00")
    worst_trade_pnl: Decimal = Decimal("0.00")

    # 每日统计
    daily_pnl: dict[str, Decimal] = field(default_factory=dict)
    daily_trades: dict[str, int] = field(default_factory=dict)
    max_drawdown: Decimal = Decimal("0.00")  # 累计最大回撤
    peak_asset: Decimal = Decimal("1000000.00")  # 历史最高总资产

    @property
    def available_cash(self) -> Decimal:
        """可用资金（排除冻结）"""
        return self.cash - self.frozen_cash

    def total_asset(self, positions_value: Decimal = Decimal("0")) -> Decimal:
        """总资产 = 现金 + 冻结 + 持仓市值"""
        return self.cash + positions_value

    def can_buy(self, amount: Decimal) -> bool:
        """检查是否有足够可用资金"""
        return self.available_cash >= amount

    def reserve_cash(self, amount: Decimal) -> bool:
        """冻结资金（下单时）"""
        if not self.can_buy(amount):
            return False
        self.frozen_cash += amount
        self._save_state_flag = True
        return True

    def commit_buy(self, cost: Decimal) -> bool:
        """确认买入，扣减现金"""
        if cost > self.frozen_cash:
            return False
        self.frozen_cash -= cost
        self.cash -= cost
        self._save_state_flag = True
        return True

    def release_reserve(self, amount: Decimal):
        """释放冻结资金（撤单时）"""
        self.frozen_cash -= amount
        self._save_state_flag = True

    def receive_sell(self, proceeds: Decimal, pnl: Decimal):
        """确认卖出，回笼现金"""
        self.cash += proceeds
        self.total_trades += 1
        if pnl > 0:
            self.winning_trades += 1
        self.total_pnl += pnl
        if pnl > self.best_trade_pnl:
            self.best_trade_pnl = pnl
        if pnl < self.worst_trade_pnl:
            self.worst_trade_pnl = pnl

        today = date.today().isoformat()
        self.daily_pnl[today] = self.daily_pnl.get(today, Decimal("0")) + pnl
        self.daily_trades[today] = self.daily_trades.get(today, 0) + 1

        # 更新回撤
        current_asset = self.cash  # caller will add positions
        if current_asset > self.peak_asset:
            self.peak_asset = current_asset
        drawdown = (self.peak_asset - current_asset) / self.peak_asset
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown

        self._save_state_flag = True

    def get_daily_pnl(self, dt: Optional[date] = None) -> Decimal:
        """获取指定日盈亏"""
        key = (dt or date.today()).isoformat()
        return self.daily_pnl.get(key, Decimal("0"))

    def get_win_rate(self) -> float:
        """胜率"""
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    def to_dict(self) -> dict:
        return {
            "initial_capital": str(self.initial_capital),
            "cash": str(self.cash),
            "frozen_cash": str(self.frozen_cash),
            "total_deposit": str(self.total_deposit),
            "total_withdraw": str(self.total_withdraw),
            "created_date": self.created_date,
            "last_updated": self.last_updated,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "total_pnl": str(self.total_pnl),
            "best_trade_pnl": str(self.best_trade_pnl),
            "worst_trade_pnl": str(self.worst_trade_pnl),
            "daily_pnl": {k: str(v) for k, v in self.daily_pnl.items()},
            "daily_trades": self.daily_trades,
            "max_drawdown": str(self.max_drawdown),
            "peak_asset": str(self.peak_asset),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        return cls(
            initial_capital=Decimal(d["initial_capital"]),
            cash=Decimal(d["cash"]),
            frozen_cash=Decimal(d.get("frozen_cash", "0")),
            created_date=d["created_date"],
            last_updated=d["last_updated"],
            total_trades=d.get("total_trades", 0),
            winning_trades=d.get("winning_trades", 0),
            total_pnl=Decimal(d.get("total_pnl", "0")),
            best_trade_pnl=Decimal(d.get("best_trade_pnl", "0")),
            worst_trade_pnl=Decimal(d.get("worst_trade_pnl", "0")),
            daily_pnl={k: Decimal(v) for k, v in d.get("daily_pnl", {}).items()},
            daily_trades=d.get("daily_trades", {}),
            max_drawdown=Decimal(d.get("max_drawdown", "0")),
            peak_asset=Decimal(d.get("peak_asset", d["initial_capital"])),
        )


@dataclass
class RiskRules:
    """风控参数"""

    # 仓位上限
    max_position_pct: Decimal = Decimal("0.30")  # 单票 ≤30%
    max_market_a: Decimal = Decimal("0.60")  # A股总仓位 ≤60%
    max_market_hk: Decimal = Decimal("0.40")  # 港股总仓位 ≤40%
    max_sector_pct: Decimal = Decimal("0.40")  # 单板块 ≤40%
    max_theme_pct: Decimal = Decimal("0.50")  # 同主线 ≤50%

    # 持仓数量上限
    max_short_term_count: int = 4
    max_long_term_count: int = 4

    # 止损止盈
    stop_loss_pct: dict = field(default_factory=lambda: {
        "short_term": Decimal("-0.05"),
        "mid_term": Decimal("-0.08"),
        "long_term": Decimal("-0.12"),
    })
    take_profit_pct: dict = field(default_factory=lambda: {
        "short_term": Decimal("0.10"),
        "mid_term": Decimal("0.18"),
        "long_term": Decimal("0.30"),
    })

    # 移动止盈触发点（盈利百分比）
    trailing_breakeven_pct: dict = field(default_factory=lambda: {
        "short_term": Decimal("0.08"),
        "mid_term": Decimal("0.12"),
        "long_term": Decimal("0.20"),
    })
    trailing_start_pct: dict = field(default_factory=lambda: {
        "short_term": Decimal("0.15"),
        "mid_term": Decimal("0.22"),
        "long_term": Decimal("0.35"),
    })
    trailing_callback_pct: dict = field(default_factory=lambda: {
        "short_term": Decimal("0.03"),
        "mid_term": Decimal("0.03"),
        "long_term": Decimal("0.05"),
    })

    # ATR 仓位计算参数
    max_loss_pct: dict = field(default_factory=lambda: {
        "short_term": Decimal("0.005"),  # 最大亏损0.5%总资金
        "mid_term": Decimal("0.01"),
        "long_term": Decimal("0.015"),
    })

    # 账户级风控
    daily_drawdown_limit: Decimal = Decimal("0.08")  # 日回撤>8%熔断
    cumulative_drawdown_limit: Decimal = Decimal("0.15")  # 累计>15%减仓
    market_crash_threshold: Decimal = Decimal("-0.04")  # 大盘跌4%停买

    # 交易限制
    max_daily_trades: int = 6
    max_daily_buys: int = 4
    cooldown_minutes: int = 30  # 同板块冷却期
    min_cash_reserve: Decimal = Decimal("50000")  # 最低现金

    # 时间止损
    time_stop_days: dict = field(default_factory=lambda: {
        "short_term": 3, "mid_term": 5, "long_term": 10,
    })

    def to_dict(self) -> dict:
        return {
            "max_position_pct": str(self.max_position_pct),
            "max_market_a": str(self.max_market_a),
            "max_market_hk": str(self.max_market_hk),
            "max_sector_pct": str(self.max_sector_pct),
            "max_theme_pct": str(self.max_theme_pct),
            "max_short_term_count": self.max_short_term_count,
            "max_long_term_count": self.max_long_term_count,
            "stop_loss_pct": {k: str(v) for k, v in self.stop_loss_pct.items()},
            "take_profit_pct": {k: str(v) for k, v in self.take_profit_pct.items()},
            "trailing_breakeven_pct": {k: str(v) for k, v in self.trailing_breakeven_pct.items()},
            "trailing_start_pct": {k: str(v) for k, v in self.trailing_start_pct.items()},
            "trailing_callback_pct": {k: str(v) for k, v in self.trailing_callback_pct.items()},
            "max_loss_pct": {k: str(v) for k, v in self.max_loss_pct.items()},
            "daily_drawdown_limit": str(self.daily_drawdown_limit),
            "cumulative_drawdown_limit": str(self.cumulative_drawdown_limit),
            "market_crash_threshold": str(self.market_crash_threshold),
            "max_daily_trades": self.max_daily_trades,
            "max_daily_buys": self.max_daily_buys,
            "cooldown_minutes": self.cooldown_minutes,
            "min_cash_reserve": str(self.min_cash_reserve),
            "time_stop_days": self.time_stop_days,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RiskRules":
        return cls(
            max_position_pct=Decimal(d.get("max_position_pct", "0.30")),
            max_market_a=Decimal(d.get("max_market_a", "0.60")),
            max_market_hk=Decimal(d.get("max_market_hk", "0.40")),
            max_sector_pct=Decimal(d.get("max_sector_pct", "0.40")),
            max_theme_pct=Decimal(d.get("max_theme_pct", "0.50")),
            max_short_term_count=d.get("max_short_term_count", 4),
            max_long_term_count=d.get("max_long_term_count", 4),
            stop_loss_pct={k: Decimal(v) for k, v in d.get("stop_loss_pct", {}).items()} or RiskRules().stop_loss_pct,
            take_profit_pct={k: Decimal(v) for k, v in d.get("take_profit_pct", {}).items()} or RiskRules().take_profit_pct,
            trailing_breakeven_pct={k: Decimal(v) for k, v in d.get("trailing_breakeven_pct", {}).items()} or RiskRules().trailing_breakeven_pct,
            trailing_start_pct={k: Decimal(v) for k, v in d.get("trailing_start_pct", {}).items()} or RiskRules().trailing_start_pct,
            trailing_callback_pct={k: Decimal(v) for k, v in d.get("trailing_callback_pct", {}).items()} or RiskRules().trailing_callback_pct,
            max_loss_pct={k: Decimal(v) for k, v in d.get("max_loss_pct", {}).items()} or RiskRules().max_loss_pct,
            daily_drawdown_limit=Decimal(d.get("daily_drawdown_limit", "0.08")),
            cumulative_drawdown_limit=Decimal(d.get("cumulative_drawdown_limit", "0.15")),
            market_crash_threshold=Decimal(d.get("market_crash_threshold", "-0.04")),
            max_daily_trades=d.get("max_daily_trades", 6),
            max_daily_buys=d.get("max_daily_buys", 4),
            cooldown_minutes=d.get("cooldown_minutes", 30),
            min_cash_reserve=Decimal(d.get("min_cash_reserve", "50000")),
            time_stop_days=d.get("time_stop_days", RiskRules().time_stop_days),
        )
