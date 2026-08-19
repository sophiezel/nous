"""SLA registry + ConsumerContract — single source of truth for data freshness.

Distilled from Qlib (calendar-first) + Lean (subscription contracts).
All assert / gap_detector / pipeline readiness should read this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class Priority(str, Enum):
    P0 = "P0"  # hard fail consumers
    P1 = "P1"  # degrade / block short
    P2 = "P2"  # warn / neutral signal
    P3 = "P3"  # weekly report only


class ProduceStage(str, Enum):
    """DAG stage that produces this asset."""

    FEATURES = "features"  # S1
    FACTORS = "factors"  # S2
    PRODUCTS = "products"  # S4
    MODELS = "models"  # weekly only
    NONE = "none"  # existence-only / no auto producer


@dataclass(frozen=True)
class AssetSLA:
    """One checkable asset (table column or file)."""

    key: str
    domain: str  # micro|fundamental|macro|capital|lhb|factor|model|recommend
    priority: Priority
    kind: str  # table|file|glob
    # table fields
    table: str = ""
    date_col: str = "trade_date"
    market_filter: str = ""  # e.g. "a" / "hk" via stock_basic join — optional SQL fragment unused for simple MAX
    max_lag_trading_days: int = 1
    min_coverage_pct: Optional[float] = None  # vs prior session row count
    min_rows: Optional[int] = None
    # file fields
    path: str = ""  # may contain {home}
    path_glob: str = ""
    max_age_calendar_days: Optional[int] = None  # for model mtime
    require_as_of_match: bool = False  # factor latest must match last trade date snapshot
    label: str = ""
    # Producer DAG (2026-07-23)
    produce_stage: ProduceStage = ProduceStage.NONE
    remediable: bool = False  # morning chain may auto-reproduce once


def _home() -> Path:
    return Path.home()


def factor_dir() -> Path:
    from nous.core.paths import factor_dir as _factor_dir

    return _factor_dir()


def model_dir() -> Path:
    from nous.core.paths import model_dir as _model_dir

    return _model_dir()


# ── Canonical SLA list ──────────────────────────────────────────────────

ASSETS: list[AssetSLA] = [
    # B micro P0
    AssetSLA(
        key="stock_daily_a",
        domain="micro",
        priority=Priority.P0,
        kind="table",
        table="stock_daily",
        date_col="trade_date",
        max_lag_trading_days=1,
        min_coverage_pct=80.0,
        label="A股日线",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="stock_basic",
        domain="micro",
        priority=Priority.P0,
        kind="table",
        table="stock_basic",
        date_col="",  # existence / count only
        min_rows=1,
        label="股票基础信息",
        produce_stage=ProduceStage.NONE,
        remediable=False,
    ),
    # B fundamental P1
    AssetSLA(
        key="stock_fundamental",
        domain="fundamental",
        priority=Priority.P1,
        kind="table",
        table="stock_fundamental",
        date_col="snapshot_date",
        max_lag_trading_days=2,
        label="基本面快照",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    # A macro
    AssetSLA(
        key="index_daily",
        domain="macro",
        priority=Priority.P1,
        kind="table",
        table="index_daily",
        date_col="trade_date",
        max_lag_trading_days=1,
        label="指数日线",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="index_global_daily",
        domain="macro",
        priority=Priority.P2,
        kind="table",
        table="index_global_daily",
        date_col="trade_date",
        max_lag_trading_days=2,
        label="全球指数",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="futures_daily",
        domain="macro",
        priority=Priority.P2,
        kind="table",
        table="futures_daily",
        date_col="trade_date",
        max_lag_trading_days=1,
        label="期货日线",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="futures_basis",
        domain="macro",
        priority=Priority.P2,
        kind="table",
        table="futures_basis",
        date_col="trade_date",
        max_lag_trading_days=1,
        label="期指基差",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="sentiment_cache",
        domain="macro",
        priority=Priority.P2,
        kind="table",
        table="sentiment_cache",
        date_col="date",  # live schema uses date, not trade_date
        max_lag_trading_days=1,
        label="市场情绪",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    # C capital
    AssetSLA(
        key="hsgt_market_daily",
        domain="capital",
        priority=Priority.P1,
        kind="table",
        table="hsgt_market_daily",
        date_col="trade_date",
        max_lag_trading_days=1,
        label="沪深港通市场",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="hsgt_stock_daily",
        domain="capital",
        priority=Priority.P1,
        kind="table",
        table="hsgt_stock_daily",
        date_col="trade_date",
        max_lag_trading_days=3,
        label="沪深港通个股",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="fund_flow_stock",
        domain="capital",
        priority=Priority.P2,
        kind="table",
        table="fund_flow_stock",
        date_col="trade_date",
        max_lag_trading_days=2,
        label="个股资金流向",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="margin_daily",
        domain="capital",
        priority=Priority.P2,
        kind="table",
        table="margin_daily",
        date_col="trade_date",
        max_lag_trading_days=2,
        label="融资融券",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="etf_flow_daily",
        domain="capital",
        priority=Priority.P2,
        kind="table",
        table="etf_flow_daily",
        date_col="trade_date",
        max_lag_trading_days=2,
        label="ETF资金流",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    AssetSLA(
        key="block_trades",
        domain="capital",
        priority=Priority.P2,
        kind="table",
        table="block_trades",
        date_col="trade_date",
        max_lag_trading_days=2,
        label="大宗交易",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    # D LHB
    AssetSLA(
        key="lhb_daily",
        domain="lhb",
        priority=Priority.P2,
        kind="table",
        table="lhb_daily",
        date_col="trade_date",
        max_lag_trading_days=2,
        label="龙虎榜",
        produce_stage=ProduceStage.FEATURES,
        remediable=True,
    ),
    # E factors / models
    AssetSLA(
        key="factors_latest",
        domain="factor",
        priority=Priority.P0,
        kind="file",
        path="~/nous-data/factors/latest.parquet",
        max_lag_trading_days=1,
        min_rows=500,
        require_as_of_match=True,
        label="A股因子 latest",
        produce_stage=ProduceStage.FACTORS,
        remediable=True,
    ),
    AssetSLA(
        key="factors_snapshot",
        domain="factor",
        priority=Priority.P0,
        kind="file",
        path="",  # resolved dynamically to snapshots/{last_trade_date}.parquet
        max_lag_trading_days=1,
        min_rows=500,
        label="A股因子 dated snapshot",
        produce_stage=ProduceStage.FACTORS,
        remediable=True,
    ),
    AssetSLA(
        key="models_lgb",
        domain="model",
        priority=Priority.P1,
        kind="glob",
        path_glob="~/nous-data/models/lgb_*.pkl",
        max_age_calendar_days=14,
        label="LightGBM 模型",
        produce_stage=ProduceStage.MODELS,
        remediable=False,
    ),
    # F recommend products (produced in S4; not asserted in S3 gate)
    AssetSLA(
        key="screen_results",
        domain="recommend",
        priority=Priority.P0,
        kind="table",
        table="screen_results",
        date_col="screen_date",
        max_lag_trading_days=1,
        label="筛选结果",
        produce_stage=ProduceStage.PRODUCTS,
        remediable=False,
    ),
    AssetSLA(
        key="theme_auto_pools",
        domain="recommend",
        priority=Priority.P0,
        kind="table",
        table="theme_auto_pools",
        date_col="scan_date",
        max_lag_trading_days=1,
        label="龙脉主题池",
        produce_stage=ProduceStage.PRODUCTS,
        remediable=False,
    ),
]


def assets_for_stage(stage: ProduceStage) -> list[AssetSLA]:
    return [a for a in ASSETS if a.produce_stage == stage]


def remediable_assets() -> list[AssetSLA]:
    return [a for a in ASSETS if a.remediable]


@dataclass(frozen=True)
class ConsumerContract:
    """Lean-style subscription: which asset keys a consumer needs."""

    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    description: str = ""


CONSUMERS: dict[str, ConsumerContract] = {
    "recommend": ConsumerContract(
        name="recommend",
        required=(
            "stock_daily_a",
            "stock_basic",
            "stock_fundamental",
            "index_daily",
            "factors_latest",
            "factors_snapshot",
        ),
        optional=("models_lgb", "hsgt_stock_daily"),
        description="海鹰F3 日荐 / daily_recommendation_pipeline",
    ),
    "trl": ConsumerContract(
        name="trl",
        required=(
            "stock_daily_a",
            "stock_basic",
            "theme_auto_pools",
        ),
        optional=("stock_fundamental", "index_daily", "hsgt_market_daily"),
        description="龙脉TRL 主题荐股",
    ),
    "review": ConsumerContract(
        name="review",
        required=("index_daily", "stock_daily_a"),
        optional=("margin_daily", "hsgt_stock_daily", "futures_basis", "sentiment_cache"),
        description="鳄鱼派六信号复盘",
    ),
    "backtest": ConsumerContract(
        name="backtest",
        required=("stock_daily_a", "stock_basic"),
        optional=("factors_latest", "stock_fundamental", "index_daily"),
        description="海鹰/龙脉 WF 回测与 accept",
    ),
    "all": ConsumerContract(
        name="all",
        required=tuple(a.key for a in ASSETS),
        description="全量鲜度断言",
    ),
}


DOMAIN_KEYS = {
    "micro": ("stock_daily_a", "stock_basic"),
    "fundamental": ("stock_fundamental",),
    "macro": (
        "index_daily",
        "index_global_daily",
        "futures_daily",
        "futures_basis",
        "sentiment_cache",
    ),
    "capital": (
        "hsgt_market_daily",
        "hsgt_stock_daily",
        "fund_flow_stock",
        "margin_daily",
        "etf_flow_daily",
        "block_trades",
    ),
    "lhb": ("lhb_daily",),
    "factor": ("factors_latest", "factors_snapshot", "models_lgb"),
    "recommend": ("screen_results", "theme_auto_pools"),
    "all": tuple(a.key for a in ASSETS),
}


def asset_by_key(key: str) -> AssetSLA | None:
    for a in ASSETS:
        if a.key == key:
            return a
    return None


def expand_path(p: str) -> Path:
    from nous.core.paths import data_dir

    text = p.replace("{home}", str(Path.home()))
    prefix = "~/nous-data"
    if text.startswith(prefix):
        rel = text[len(prefix):].lstrip("/")
        return data_dir() / rel if rel else data_dir()
    return Path(text.replace("~", str(Path.home()))).expanduser()
