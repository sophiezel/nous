"""模拟盘交易系统 — 订单管理模块

支持市价单和限价单，订单簿管理，状态流转。
订单状态：pending → filled / partial / cancelled / expired
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
import uuid


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"  # 市价单
    LIMIT = "limit"  # 限价单


class OrderStatus(str, Enum):
    PENDING = "pending"  # 待成交
    FILLED = "filled"  # 全部成交
    PARTIAL = "partial"  # 部分成交
    CANCELLED = "cancelled"  # 已撤销
    EXPIRED = "expired"  # 过期（限价单未成交）
    REJECTED = "rejected"  # 被拒（风控/资金不足）


class OrderReason(str, Enum):
    """订单原因"""
    RECOMMENDATION = "recommendation"  # 荐股报告推荐
    PYRAMID_ADD = "pyramid_add"  # 金字塔加仓
    STOP_LOSS = "stop_loss"  # 止损
    TAKE_PROFIT = "take_profit"  # 止盈
    TRAILING_STOP = "trailing_stop"  # 移动止盈
    TIME_STOP = "time_stop"  # 时间止损
    MANUAL = "manual"  # 手动
    COOLDOWN_REBUY = "cooldown_rebuy"  # 冷却期后补买
    DISCIPLINE_SELL = "discipline_sell"  # 纪律性减仓


@dataclass
class Order:
    """模拟订单"""

    order_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    symbol: str = ""  # 股票代码
    name: str = ""  # 股票名称
    market: str = "A"  # A / HK
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    price: Decimal = Decimal("0")  # 委托价格（市价单为0表示不限价）
    shares: int = 0
    filled_shares: int = 0  # 已成交数量
    filled_price: Decimal = Decimal("0")  # 实际成交均价
    status: OrderStatus = OrderStatus.PENDING
    reason: OrderReason = OrderReason.MANUAL
    strategy: str = "short_term"  # short_term / mid_term / long_term
    sector: str = ""  # 板块
    theme: str = ""  # 主线
    recommendation_id: str = ""  # 来源荐股报告日期
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    filled_at: str = ""
    cancelled_at: str = ""

    # 成本相关
    commission: Decimal = Decimal("0")  # 佣金（模拟）
    stamp_tax: Decimal = Decimal("0")  # 印花税
    slippage: Decimal = Decimal("0")  # 滑点成本

    @property
    def total_cost(self) -> Decimal:
        """买入总成本（含佣金+印花税+滑点）"""
        if self.side == OrderSide.BUY:
            return (self.filled_price * self.filled_shares
                    + self.commission + self.stamp_tax + self.slippage)
        return Decimal("0")

    @property
    def total_proceeds(self) -> Decimal:
        """卖出净收入"""
        if self.side == OrderSide.SELL:
            return (self.filled_price * self.filled_shares
                    - self.commission - self.stamp_tax - self.slippage)
        return Decimal("0")

    @property
    def remaining_shares(self) -> int:
        """未成交数量"""
        return self.shares - self.filled_shares

    def fill(self, price: Decimal, shares: int = 0, commission: Decimal = Decimal("0"),
             stamp_tax: Decimal = Decimal("0"), slippage: Decimal = Decimal("0")):
        """成交（全部或部分）"""
        fill_qty = shares if shares > 0 else self.shares
        self.filled_shares += fill_qty
        if self.filled_shares > 0:
            self.filled_price = (
                (self.filled_price * (self.filled_shares - fill_qty) + price * fill_qty)
                / self.filled_shares
            ).quantize(Decimal("0.01"))
        self.commission += commission
        self.stamp_tax += stamp_tax
        self.slippage += slippage
        if self.filled_shares >= self.shares:
            self.status = OrderStatus.FILLED
            self.filled_at = datetime.now().isoformat()
        else:
            self.status = OrderStatus.PARTIAL

    def cancel(self):
        """撤销订单"""
        if self.status in (OrderStatus.PENDING, OrderStatus.PARTIAL):
            self.status = OrderStatus.CANCELLED
            self.cancelled_at = datetime.now().isoformat()

    def reject(self, reason: str = ""):
        """拒绝订单"""
        self.status = OrderStatus.REJECTED

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "price": str(self.price),
            "shares": self.shares,
            "filled_shares": self.filled_shares,
            "filled_price": str(self.filled_price),
            "status": self.status.value,
            "reason": self.reason.value,
            "strategy": self.strategy,
            "sector": self.sector,
            "theme": self.theme,
            "recommendation_id": self.recommendation_id,
            "created_at": self.created_at,
            "filled_at": self.filled_at,
            "cancelled_at": self.cancelled_at,
            "commission": str(self.commission),
            "stamp_tax": str(self.stamp_tax),
            "slippage": str(self.slippage),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        return cls(
            order_id=d["order_id"],
            symbol=d["symbol"],
            name=d["name"],
            market=d.get("market", "A"),
            side=OrderSide(d["side"]),
            order_type=OrderType(d.get("order_type", "market")),
            price=Decimal(d["price"]),
            shares=d["shares"],
            filled_shares=d.get("filled_shares", 0),
            filled_price=Decimal(d.get("filled_price", "0")),
            status=OrderStatus(d["status"]),
            reason=OrderReason(d.get("reason", "manual")),
            strategy=d.get("strategy", "short_term"),
            sector=d.get("sector", ""),
            theme=d.get("theme", ""),
            recommendation_id=d.get("recommendation_id", ""),
            created_at=d["created_at"],
            filled_at=d.get("filled_at", ""),
            cancelled_at=d.get("cancelled_at", ""),
            commission=Decimal(d.get("commission", "0")),
            stamp_tax=Decimal(d.get("stamp_tax", "0")),
            slippage=Decimal(d.get("slippage", "0")),
        )


class OrderBook:
    """订单簿"""

    def __init__(self):
        self.orders: dict[str, Order] = {}  # order_id → Order
        # 历史订单（已成交/已撤销）
        self.history: dict[str, Order] = {}

    def submit(self, order: Order) -> str:
        """提交订单"""
        self.orders[order.order_id] = order
        return order.order_id

    def get(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)

    def fill(self, order_id: str, price: Decimal, shares: int = 0,
             commission: Decimal = Decimal("0"), stamp_tax: Decimal = Decimal("0"),
             slippage: Decimal = Decimal("0")):
        """成交订单"""
        order = self.orders.get(order_id)
        if not order:
            return
        order.fill(price, shares, commission, stamp_tax, slippage)
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            self.history[order_id] = order
            del self.orders[order_id]

    def cancel(self, order_id: str):
        """撤销订单"""
        order = self.orders.get(order_id)
        if not order:
            return
        order.cancel()
        self.history[order_id] = order
        del self.orders[order_id]

    def get_pending_orders(self) -> list[Order]:
        """获取所有待处理订单"""
        return [o for o in self.orders.values() if o.status == OrderStatus.PENDING]

    def get_pending_by_symbol(self, symbol: str) -> list[Order]:
        """获取某只股票的待处理订单"""
        return [o for o in self.orders.values()
                if o.symbol == symbol and o.status == OrderStatus.PENDING]

    def get_today_buys(self, dt_str: str) -> list[Order]:
        """获取当日买入订单"""
        return [o for o in self.history.values()
                if o.side == OrderSide.BUY and o.filled_at.startswith(dt_str)]

    def get_today_sells(self, dt_str: str) -> list[Order]:
        """获取当日卖出订单"""
        return [o for o in self.history.values()
                if o.side == OrderSide.SELL and o.filled_at.startswith(dt_str)]

    def get_today_all(self, dt_str: str) -> list[Order]:
        """获取当日所有成交订单"""
        return [o for o in self.history.values() if o.filled_at.startswith(dt_str)]

    def to_dict(self) -> dict:
        return {
            "pending": {k: v.to_dict() for k, v in self.orders.items()},
            "history": {k: v.to_dict() for k, v in self.history.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OrderBook":
        book = cls()
        for k, v in d.get("pending", {}).items():
            book.orders[k] = Order.from_dict(v)
        for k, v in d.get("history", {}).items():
            book.history[k] = Order.from_dict(v)
        return book

    def __len__(self) -> int:
        return len(self.orders) + len(self.history)
