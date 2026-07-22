"""模拟盘交易系统 — 状态管理

持久化加载/保存 account + risk_rules + positions + orders
到 state.yaml 和 history/ 归档。
"""

from __future__ import annotations
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import yaml
import shutil

from .account import Account, RiskRules
from .portfolio import Portfolio, Position
from .order import OrderBook, Order


class StateManager:
    """模拟盘状态管理器"""

    def __init__(self, state_dir: str = ""):
        if not state_dir:
            state_dir = str(Path(__file__).parent)
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / "state.yaml"
        self.history_dir = self.state_dir / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)

        # 核心状态
        self.account: Account = Account()
        self.risk_rules: RiskRules = RiskRules()
        self.portfolio: Portfolio = Portfolio()
        self.orders: OrderBook = OrderBook()

    def load(self) -> StateManager:
        """从 state.yaml 加载状态"""
        if not self.state_file.exists():
            return self

        with open(self.state_file) as f:
            data = yaml.safe_load(f) or {}

        # 加载账户
        if "account" in data:
            self.account = Account.from_dict(data["account"])
        # 加载风控规则
        if "risk_rules" in data:
            self.risk_rules = RiskRules.from_dict(data["risk_rules"])
        # 加载持仓
        if "positions" in data:
            self.portfolio = Portfolio.from_dict({"positions": data["positions"]})
        # 加载订单簿
        if "orders" in data:
            self.orders = OrderBook.from_dict(data["orders"])

        return self

    def save(self):
        """保存状态到 state.yaml"""
        data = {
            "account": self.account.to_dict(),
            "risk_rules": self.risk_rules.to_dict(),
            "positions": {k: v.to_dict() for k, v in self.portfolio.positions.items()},
            "orders": self.orders.to_dict(),
        }
        # 原子写入：先写临时文件再重命名
        tmp_file = self.state_file.with_suffix(".yaml.tmp")
        with open(tmp_file, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        tmp_file.rename(self.state_file)

    def archive(self, dt: Optional[date] = None):
        """归档当日快照到 history/YYYY-MM-DD_state.yaml"""
        if dt is None:
            dt = date.today()
        arch_file = self.history_dir / f"{dt.isoformat()}_state.yaml"
        shutil.copy2(self.state_file, arch_file)

        # 同时保存当日交易明细
        trades_file = self.history_dir / f"{dt.isoformat()}_trades.json"
        import json
        trades = [o.to_dict() for o in self.orders.get_today_all(dt.isoformat())]
        with open(trades_file, "w") as f:
            json.dump(trades, f, ensure_ascii=False, indent=2)

    def get_available_cash(self) -> Decimal:
        """获取可用资金（排除最低保留）"""
        available = self.account.available_cash
        reserve = self.risk_rules.min_cash_reserve
        return max(Decimal("0"), available - reserve)

    def get_total_asset(self) -> Decimal:
        """总资产 = 现金 + 冻结 + 持仓市值"""
        positions_value = sum(
            (p.market_value for p in self.portfolio.positions.values()),
            Decimal("0")
        )
        return self.account.total_asset(positions_value)

    def get_daily_pnl(self, dt: Optional[date] = None) -> Decimal:
        """获取当日盈亏"""
        return self.account.get_daily_pnl(dt)

    def get_daily_drawdown(self, dt: Optional[date] = None) -> Decimal:
        """获取当日回撤"""
        if dt is None:
            dt = date.today()
        key = dt.isoformat()
        peak = self.account.peak_asset
        current = self.get_total_asset()
        if peak == 0:
            return Decimal("0")
        return (peak - current) / peak

    def get_market_exposure(self, market: str) -> Decimal:
        """某市场仓位占比"""
        total = self.get_total_asset()
        if total == 0:
            return Decimal("0")
        return self.portfolio.get_market_value_by_market(market) / total

    def get_sector_exposure(self, sector: str) -> Decimal:
        """某板块仓位占比"""
        total = self.get_total_asset()
        if total == 0:
            return Decimal("0")
        return self.portfolio.get_market_value_by_sector(sector) / total

    def get_theme_exposure(self, theme: str) -> Decimal:
        """某主线仓位占比"""
        total = self.get_total_asset()
        if total == 0:
            return Decimal("0")
        return self.portfolio.get_market_value_by_theme(theme) / total


def load_state(state_dir: str = "") -> StateManager:
    """便捷加载"""
    return StateManager(state_dir).load()


def create_fresh_state(state_dir: str = "") -> StateManager:
    """创建全新空状态并保存"""
    sm = StateManager(state_dir)
    sm.save()
    return sm
