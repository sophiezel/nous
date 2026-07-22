"""模拟盘交易系统 — 持仓追踪模块

管理所有持仓，包含 T+1 锁定、最高价追踪（移动止盈）、
时间止损计时、市值/盈亏计算。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional


@dataclass
class Position:
    """单个持仓"""

    symbol: str  # 600519
    name: str  # 贵州茅台
    market: str  # A / HK
    strategy: str  # short_term / mid_term / long_term
    sector: str = ""  # 板块
    theme: str = ""  # 主线

    # 成本与数量
    entry_price: Decimal = Decimal("0")  # 买入均价
    entry_date: str = ""  # 首次买入日期
    shares: int = 0
    total_cost: Decimal = Decimal("0")  # 总成本（含佣金+滑点）

    # 当前行情
    current_price: Decimal = Decimal("0")
    last_update: str = ""

    # T+1 锁定（A股专用）
    locked_until: str = ""  # 解锁日期 "2026-05-14"
    is_locked_today: bool = False  # 是否今日新仓

    # 移动止盈
    highest_price: Decimal = Decimal("0")  # 持仓期间最高价
    stop_loss_active: bool = True  # 止损是否启用
    trailing_active: bool = False  # 移动止盈是否启用
    breakeven_active: bool = False  # 保本止损是否启用

    # 止损止盈价（动态）
    stop_loss_price: Decimal = Decimal("0")
    take_profit_price: Decimal = Decimal("0")

    # 时间止损
    days_held: int = 0  # 持仓天数
    last_trend_check: str = ""  # 上次趋势检查日期

    # 跌停/停牌标记
    limit_down_days: int = 0  # 连续跌停天数
    is_limit_down: bool = False
    is_suspended: bool = False  # 停牌

    # 除权标记
    dividend_adjusted: bool = False
    dividend_amount: Decimal = Decimal("0")  # 每股分红

    # 来源
    recommendation_id: str = ""  # 荐股报告日期
    pyramid_stage: int = 1  # 金字塔建仓阶段 (1/2/3)

    @property
    def market_value(self) -> Decimal:
        """当前市值"""
        return self.current_price * self.shares

    @property
    def pnl(self) -> Decimal:
        """浮动盈亏（金额）"""
        return self.market_value - self.total_cost

    @property
    def pnl_pct(self) -> Decimal:
        """浮动盈亏率"""
        if self.total_cost == 0:
            return Decimal("0")
        return (self.pnl / self.total_cost).quantize(Decimal("0.0001"))

    @property
    def is_unlocked(self) -> bool:
        """T+1 是否已解锁（可卖出）"""
        if self.market == "HK":
            return True  # 港股 T+0
        if not self.is_locked_today:
            return True
        return date.today().isoformat() >= self.locked_until

    def update_price(self, price: Decimal, dt: str = ""):
        """更新当前价格"""
        self.current_price = price
        self.last_update = dt or datetime.now().isoformat()
        if price > self.highest_price:
            self.highest_price = price
        # 检查价格是否触发跌停（A股 -10% 跌停）
        if self.entry_price > 0 and self.market == "A":
            pct = (price - self.entry_price) / self.entry_price
            if pct <= Decimal("-0.098"):  # 接近跌停
                self.is_limit_down = True
                self.limit_down_days += 1
            else:
                self.is_limit_down = False

    def check_stop_loss(self, risk_rules) -> Optional[str]:
        """检查是否触发止损。返回 None 或触发原因。"""
        if not self.is_unlocked:
            return None  # T+1 锁定，不执行止损
        if self.is_suspended:
            return None

        sl = self.stop_loss_price if self.stop_loss_price > 0 else \
            self.entry_price * (Decimal("1") + risk_rules.stop_loss_pct[self.strategy])

        if self.current_price <= sl and self.current_price > 0:
            return "stop_loss"

        return None

    def check_take_profit(self, risk_rules) -> Optional[str]:
        """检查是否触发止盈（含移动止盈）。返回 None 或触发原因。"""
        if not self.is_unlocked:
            return None
        if self.is_suspended:
            return None

        # 固定止盈
        tp = self.take_profit_price if self.take_profit_price > 0 else \
            self.entry_price * (Decimal("1") + risk_rules.take_profit_pct[self.strategy])
        if self.current_price >= tp:
            return "take_profit"

        # 移动止盈
        if self.highest_price > 0 and self.entry_price > 0:
            pnl_pct = (self.highest_price - self.entry_price) / self.entry_price
            trailing_start = risk_rules.trailing_start_pct[self.strategy]

            if pnl_pct >= trailing_start:
                self.trailing_active = True
                trail_stop = self.highest_price * (Decimal("1") - risk_rules.trailing_callback_pct[self.strategy])
                if self.current_price <= trail_stop:
                    return "trailing_stop"

            # 保本止损
            breakeven_pct = risk_rules.trailing_breakeven_pct[self.strategy]
            if pnl_pct >= breakeven_pct and not self.breakeven_active:
                self.breakeven_active = True
                self.stop_loss_price = self.entry_price  # 止损上移至成本

        return None

    def check_time_stop(self, risk_rules) -> Optional[str]:
        """检查时间止损"""
        max_days = risk_rules.time_stop_days.get(self.strategy, 999)
        if self.days_held >= max_days:
            pnl = self.pnl_pct
            if self.strategy == "short_term" and pnl < Decimal("0.01"):
                return "time_stop"
            elif self.strategy == "mid_term" and abs(float(pnl)) < 0.02:
                return "time_stop"
            elif self.strategy == "long_term" and abs(float(pnl)) < 0.03:
                return "time_stop"
        return None

    def increment_days(self):
        """持仓天数+1"""
        self.days_held += 1

    def adjust_for_dividend(self, dividend_per_share: Decimal):
        """除权除息调整成本价"""
        self.entry_price -= dividend_per_share
        self.dividend_adjusted = True
        self.dividend_amount += dividend_per_share

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "strategy": self.strategy,
            "sector": self.sector,
            "theme": self.theme,
            "entry_price": str(self.entry_price),
            "entry_date": self.entry_date,
            "shares": self.shares,
            "total_cost": str(self.total_cost),
            "current_price": str(self.current_price),
            "last_update": self.last_update,
            "locked_until": self.locked_until,
            "is_locked_today": self.is_locked_today,
            "highest_price": str(self.highest_price),
            "stop_loss_active": self.stop_loss_active,
            "trailing_active": self.trailing_active,
            "breakeven_active": self.breakeven_active,
            "stop_loss_price": str(self.stop_loss_price),
            "take_profit_price": str(self.take_profit_price),
            "days_held": self.days_held,
            "limit_down_days": self.limit_down_days,
            "is_limit_down": self.is_limit_down,
            "is_suspended": self.is_suspended,
            "dividend_adjusted": self.dividend_adjusted,
            "dividend_amount": str(self.dividend_amount),
            "recommendation_id": self.recommendation_id,
            "pyramid_stage": self.pyramid_stage,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(
            symbol=d["symbol"],
            name=d["name"],
            market=d.get("market", "A"),
            strategy=d.get("strategy", "short_term"),
            sector=d.get("sector", ""),
            theme=d.get("theme", ""),
            entry_price=Decimal(d["entry_price"]),
            entry_date=d.get("entry_date", ""),
            shares=d["shares"],
            total_cost=Decimal(d.get("total_cost", "0")),
            current_price=Decimal(d.get("current_price", "0")),
            last_update=d.get("last_update", ""),
            locked_until=d.get("locked_until", ""),
            is_locked_today=d.get("is_locked_today", False),
            highest_price=Decimal(d.get("highest_price", "0")),
            stop_loss_active=d.get("stop_loss_active", True),
            trailing_active=d.get("trailing_active", False),
            breakeven_active=d.get("breakeven_active", False),
            stop_loss_price=Decimal(d.get("stop_loss_price", "0")),
            take_profit_price=Decimal(d.get("take_profit_price", "0")),
            days_held=d.get("days_held", 0),
            limit_down_days=d.get("limit_down_days", 0),
            is_limit_down=d.get("is_limit_down", False),
            is_suspended=d.get("is_suspended", False),
            dividend_adjusted=d.get("dividend_adjusted", False),
            dividend_amount=Decimal(d.get("dividend_amount", "0")),
            recommendation_id=d.get("recommendation_id", ""),
            pyramid_stage=d.get("pyramid_stage", 1),
        )


class Portfolio:
    """持仓组合"""

    def __init__(self):
        self.positions: dict[str, Position] = {}  # symbol → Position

    def add(self, pos: Position):
        """添加/更新持仓"""
        self.positions[pos.symbol] = pos

    def get(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    def remove(self, symbol: str) -> Optional[Position]:
        return self.positions.pop(symbol, None)

    def has(self, symbol: str) -> bool:
        return symbol in self.positions

    @property
    def total_market_value(self) -> Decimal:
        """持仓总市值"""
        return sum((p.market_value for p in self.positions.values()), Decimal("0"))

    @property
    def total_pnl(self) -> Decimal:
        """持仓总浮动盈亏"""
        return sum((p.pnl for p in self.positions.values()), Decimal("0"))

    def get_unlocked_positions(self) -> list[Position]:
        """可卖出的持仓（T+1 已解锁）"""
        return [p for p in self.positions.values() if p.is_unlocked]

    def get_locked_positions(self) -> list[Position]:
        """T+1 锁定持仓"""
        return [p for p in self.positions.values() if not p.is_unlocked]

    def get_by_market(self, market: str) -> list[Position]:
        """按市场筛选"""
        return [p for p in self.positions.values() if p.market == market]

    def get_by_strategy(self, strategy: str) -> list[Position]:
        """按策略筛选"""
        return [p for p in self.positions.values() if p.strategy == strategy]

    def get_by_sector(self, sector: str) -> list[Position]:
        """按板块筛选"""
        return [p for p in self.positions.values() if p.sector == sector]

    def get_by_theme(self, theme: str) -> list[Position]:
        """按主线筛选"""
        return [p for p in self.positions.values() if p.theme == theme]

    def get_market_value_by_market(self, market: str) -> Decimal:
        """某市场总市值"""
        return sum((p.market_value for p in self.get_by_market(market)), Decimal("0"))

    def get_market_value_by_sector(self, sector: str) -> Decimal:
        """某板块总市值"""
        return sum((p.market_value for p in self.get_by_sector(sector)), Decimal("0"))

    def get_market_value_by_theme(self, theme: str) -> Decimal:
        """某主线总市值"""
        return sum((p.market_value for p in self.get_by_theme(theme)), Decimal("0"))

    def get_count_by_strategy(self, strategy: str) -> int:
        """某策略持仓数"""
        return len(self.get_by_strategy(strategy))

    def update_all_prices(self, prices: dict[str, Decimal], dt: str = ""):
        """批量更新价格 {symbol: price}"""
        for symbol, price in prices.items():
            pos = self.positions.get(symbol)
            if pos:
                pos.update_price(price, dt)

    def increment_all_days(self):
        """所有持仓天数+1"""
        for pos in self.positions.values():
            pos.increment_days()

    def to_dict(self) -> dict:
        return {
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        pf = cls()
        for k, v in d.get("positions", {}).items():
            pf.positions[k] = Position.from_dict(v)
        return pf

    def __len__(self) -> int:
        return len(self.positions)
