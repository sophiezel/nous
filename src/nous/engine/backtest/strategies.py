"""策略定义 — 因子集 + 模型 + 组合规则 + 成本模型

对标 Qlib/WorldQuant 策略体系，区分 α 来源而非改超参。
A股做空通过股指期货(IF/IC/IM)实现，不虚构个股融券。

每个策略 = FactorSpec(ModelSpec(PortfolioSpec(CostSpec))))
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Callable, Optional


# ── Factor specification ──────────────────────────────────────────────

@dataclass
class FactorSpec:
    """因子规格 — 定义 α 来源"""
    groups: list[str] = field(default_factory=lambda: ["K1", "K2", "K3", "K4", "K5", "K6"])
    neutralization: list[str] = field(default_factory=lambda: ["market_cap", "industry"])
    lookback_days: int = 252
    universe_filter: dict = field(default_factory=dict)


# ── Model specification ───────────────────────────────────────────────

@dataclass 
class ModelSpec:
    """模型规格"""
    model_type: Literal["lgb", "xgb", "catboost", "ridge", "ensemble"] = "lgb"
    params: dict = field(default_factory=dict)
    forward_return_days: int = 5
    top_k_features: int = 30
    cv_folds: int = 5
    train_min_samples: int = 500


# ── Portfolio specification ───────────────────────────────────────────

# Hard safety ceiling aligned with trading.max_position_pct
HARD_MAX_SINGLE_WEIGHT = 0.30


@dataclass
class PortfolioSpec:
    """组合构建规则"""
    method: Literal["equal_weight", "hrp", "max_sharpe", "risk_parity", "score_weighted"] = "equal_weight"
    max_positions: int = 20
    max_single_weight: float = 0.10
    min_single_weight: float = 0.01
    sector_max: float = 0.30
    turnover_limit: float = 0.30
    cash_buffer: float = 0.02
    hedge_beta_target: float = 1.0  # 1=no hedge, 0=full hedge
    hedge_instrument: str = "IF"
    # Topk-Drop: hold top K, replace at most drop_n names each rebalance
    drop_n: int = 3

    def effective_max_single(self) -> float:
        """Strategy cap floored by hard safety ceiling (never exceed 30%)."""
        return min(float(self.max_single_weight), HARD_MAX_SINGLE_WEIGHT)


# ── Cost model ────────────────────────────────────────────────────────

@dataclass
class CostSpec:
    """A股真实交易成本"""
    stamp_duty: float = 0.0005      # 印花税: 0.05% 卖出单边
    commission: float = 0.00025     # 佣金: 万2.5
    slippage_bps: float = 10.0      # 滑点: 10bps = 0.1%
    min_commission: float = 5.0     # 最低佣金 5元
    futures_commission: float = 0.000023
    futures_margin_rate: float = 0.12

    def buy_cost(self, amount: float) -> float:
        comm = max(amount * self.commission, self.min_commission)
        return comm + amount * self.slippage_bps / 10000

    def sell_cost(self, amount: float) -> float:
        stamp = amount * self.stamp_duty
        comm = max(amount * self.commission, self.min_commission)
        slip = amount * self.slippage_bps / 10000
        return stamp + comm + slip

    def effective_buy_price(self, ref_price: float) -> float:
        return ref_price * (1 + self.slippage_bps / 10000)

    def effective_sell_price(self, ref_price: float) -> float:
        return ref_price * (1 - self.stamp_duty - self.slippage_bps / 10000)


# ── Strategy definition ───────────────────────────────────────────────

@dataclass
class Strategy:
    """完整策略定义"""
    name: str
    description: str = ""
    market: Literal["a", "hk", "both"] = "a"
    factors: FactorSpec = field(default_factory=FactorSpec)
    model: ModelSpec = field(default_factory=ModelSpec)
    portfolio: PortfolioSpec = field(default_factory=PortfolioSpec)
    costs: CostSpec = field(default_factory=CostSpec)
    rebalance_freq: int = 20
    wf_train_years: int = 3
    wf_test_months: int = 6
    wf_embargo_days: int = 5

    def __post_init__(self):
        if not self.description:
            self.description = self.name


# ── Predefined strategies ─────────────────────────────────────────────

STRATEGIES = {
    "海鹰F3": Strategy(
        name="海鹰F3",
        description="价值质量精选: PE/PB/ROE/股息率/负债率, 月度HRP组合, 大盘池",
        market="a",
        factors=FactorSpec(
            groups=["K7"],
            neutralization=["market_cap"],
            lookback_days=252,
            universe_filter={"min_mv": 1e10, "st_removal": True, "new_stock_days": 120},
        ),
        model=ModelSpec(
            model_type="lgb", forward_return_days=20, top_k_features=15,
            params={"num_leaves": 31, "learning_rate": 0.05, "min_child_samples": 50},
        ),
        portfolio=PortfolioSpec(
            method="hrp", max_positions=15, max_single_weight=0.12, sector_max=0.25,
            drop_n=3,
        ),
        rebalance_freq=20, wf_train_years=4, wf_test_months=6,
    ),

    "龙脉TRL": Strategy(
        name="龙脉TRL",
        description="趋势动量精选: 动量/RSI/MA/量比/波动率, 周度等权换仓",
        market="a",
        factors=FactorSpec(
            groups=["K1", "K2", "K3", "K4", "K5", "K6"],
            neutralization=["industry", "market_cap"],
            lookback_days=120,
            universe_filter={"min_mv": 3e9, "st_removal": True, "new_stock_days": 60},
        ),
        model=ModelSpec(
            model_type="lgb", forward_return_days=5, top_k_features=30,
            params={"num_leaves": 127, "learning_rate": 0.03, "min_child_samples": 20},
        ),
        portfolio=PortfolioSpec(
            method="equal_weight", max_positions=25, max_single_weight=0.08,
            sector_max=0.20, turnover_limit=0.40,
        ),
        rebalance_freq=5, wf_train_years=2, wf_test_months=3,
    ),

    "鳄鱼派": Strategy(
        name="鳄鱼派",
        description="板块轮动: 主线识别+北向资金+拥挤度, 信号驱动换仓",
        market="a",
        factors=FactorSpec(
            groups=["K1", "K4", "K8"],
            neutralization=[],
            lookback_days=60,
            universe_filter={"min_mv": 5e9},
        ),
        model=ModelSpec(
            model_type="lgb", forward_return_days=10, top_k_features=20,
            params={"num_leaves": 63, "learning_rate": 0.05},
        ),
        portfolio=PortfolioSpec(
            method="score_weighted", max_positions=10, max_single_weight=0.15,
            sector_max=0.40, turnover_limit=0.60,
        ),
        rebalance_freq=10, wf_train_years=2, wf_test_months=3,
    ),

    "市场中性": Strategy(
        name="市场中性",
        description="Alpha+对冲: 做多个股α + IF期货空头对冲β, 周度再平衡",
        market="a",
        factors=FactorSpec(
            groups=["K1", "K2", "K3", "K5", "K6", "K7"],
            neutralization=["market_cap", "industry", "beta"],
            lookback_days=252,
            universe_filter={"min_mv": 5e9, "st_removal": True},
        ),
        model=ModelSpec(
            model_type="lgb", forward_return_days=5, top_k_features=25,
            params={"num_leaves": 63, "learning_rate": 0.03, "min_child_samples": 30},
        ),
        portfolio=PortfolioSpec(
            method="equal_weight", max_positions=30, max_single_weight=0.06,
            hedge_beta_target=0.0, hedge_instrument="IF",
        ),
        costs=CostSpec(futures_commission=0.000023, futures_margin_rate=0.12),
        rebalance_freq=5, wf_train_years=3, wf_test_months=3,
    ),

    "指数增强": Strategy(
        name="指数增强",
        description="300增强: 跟踪沪深300 + 因子α超额, 控制跟踪误差",
        market="a",
        factors=FactorSpec(
            groups=["K1", "K3", "K6", "K7"],
            neutralization=["beta"],
            lookback_days=252,
            universe_filter={"min_mv": 1e10, "csi300_only": True},
        ),
        model=ModelSpec(
            model_type="ridge", forward_return_days=20, top_k_features=15,
            params={"alpha": 1.0},
        ),
        portfolio=PortfolioSpec(
            method="max_sharpe", max_positions=50, max_single_weight=0.05,
            sector_max=0.35, turnover_limit=0.15,
        ),
        rebalance_freq=20, wf_train_years=4, wf_test_months=6,
    ),

    "多因子综合": Strategy(
        name="多因子综合",
        description="全因子集成: K1-K8全部因子, 月度HRP组合",
        market="a",
        factors=FactorSpec(
            groups=["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8"],
            neutralization=["market_cap", "industry"],
            lookback_days=252,
            universe_filter={"min_mv": 3e9, "st_removal": True, "new_stock_days": 120},
        ),
        model=ModelSpec(
            model_type="lgb", forward_return_days=20, top_k_features=35,
            params={"num_leaves": 127, "learning_rate": 0.03, "min_child_samples": 20},
        ),
        portfolio=PortfolioSpec(
            method="hrp", max_positions=25, max_single_weight=0.08, sector_max=0.20,
        ),
        rebalance_freq=20, wf_train_years=3, wf_test_months=6,
    ),
}


def list_strategies() -> list[str]:
    return sorted(STRATEGIES.keys())


def get_strategy(name: str) -> Strategy:
    if name not in STRATEGIES:
        available = list_strategies()
        raise KeyError(f"Unknown strategy '{name}'. Available: {available}")
    return STRATEGIES[name]
