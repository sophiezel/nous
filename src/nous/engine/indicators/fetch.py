"""统一数据获取入口 fetch_validated()

所有K1指标通过此函数获取，自动执行:
1. 市场感知新鲜度门禁
2. 多源按优先级尝试
3. 兜底计算
4. 结果校验

返回 ValidatedResult NamedTuple。
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from . import registry, gate
from .gate import Freshness


@dataclass
class ValidatedResult:
    value: Any = None
    source: str = ""
    freshness: Freshness = Freshness.FRESH
    verified: bool = False
    tier: str = ""
    criticality: str = ""
    warnings: list[str] = field(default_factory=list)
    available: bool = False

    def is_fresh(self) -> bool:
        return self.freshness in (Freshness.FRESH, Freshness.NON_TRADING)

    def is_stale(self) -> bool:
        return self.freshness == Freshness.STALE

    def is_rejected(self) -> bool:
        return self.freshness == Freshness.REJECTED


def fetch_validated(
    symbol: str,
    indicator_name: str,
    market: str = "a",
    context: dict = None,
) -> ValidatedResult:
    """
    统一数据获取入口。

    Args:
        symbol: 股票代码
        indicator_name: 指标名（对应 registry.INDICATORS 的 key）
        market: 'a' 或 'hk'
        context: 上下文数据（如 close/eps 等已获取的值，避免重复拉取）

    Returns:
        ValidatedResult
    """
    indicator = registry.get(indicator_name)
    if indicator is None:
        return ValidatedResult(
            available=False,
            warnings=[f"未知指标: {indicator_name}"],
        )

    tier = indicator["tier"]
    sources = indicator.get("sources", [])

    # 港股使用独立数据源（如果有）
    if market == "hk" and "sources_hk" in indicator:
        sources = indicator["sources_hk"]

    if not sources:
        return ValidatedResult(
            available=False,
            tier=tier,
            criticality=indicator["criticality"],
            warnings=[f"{indicator_name}: 无可用数据源"],
        )

    context = context or {}
    primary_value = None
    primary_source = ""
    all_values = []  # 用于多源交叉验证
    all_errors = []

    # ── 遍历数据源，按优先级 ──
    for src in sources:
        src_name = src["name"]
        fn_name = src.get("fn", "")

        # 尝试从 context 中取
        ctx_key = f"{indicator_name}_{src_name}"
        if ctx_key in context:
            value = context[ctx_key]
            freshness_result = gate.check(tier, market)
        else:
            value, freshness_result = _try_fetch(symbol, market, tier, src, context)

        if value is not None and freshness_result.freshness != Freshness.REJECTED:
            all_values.append((src_name, value, freshness_result))
            if primary_value is None:
                primary_value = value
                primary_source = src_name
        else:
            all_errors.append(f"{src_name}: {freshness_result.reason}")

    # ── 多源交叉验证 ──
    warnings = []
    if len(all_values) >= 2:
        threshold = indicator.get("validation_threshold", 0.10)
        primary_val = all_values[0][1]
        for src_name, val, _ in all_values[1:]:
            if primary_val and val and primary_val != 0:
                diff = abs(val - primary_val) / abs(primary_val)
                if diff > threshold:
                    warnings.append(
                        f"⚠️ {indicator_name}: {primary_source}={primary_val} vs "
                        f"{src_name}={val} 差异 {diff*100:.1f}% > {threshold*100:.0f}%阈值"
                    )
                else:
                    warnings.append(
                        f"✓ {indicator_name}: {primary_source}={primary_val} vs "
                        f"{src_name}={val} 差异 {diff*100:.1f}% OK"
                    )

    # ── 兜底 ──
    if primary_value is None:
        fallback = indicator.get("fallback_value")
        if fallback is not None:
            return ValidatedResult(
                value=fallback,
                source="fallback",
                freshness=Freshness.STALE,
                verified=False,
                tier=tier,
                criticality=indicator["criticality"],
                warnings=[f"{indicator_name}: 所有源失败，使用兜底值{fallback}"] + all_errors,
                available=True,
            )
        return ValidatedResult(
            available=False,
            tier=tier,
            criticality=indicator["criticality"],
            warnings=[f"{indicator_name}: 所有源失败"] + all_errors,
        )

    # ── 新鲜度门禁 ──
    freshness_result = gate.check(tier, market)

    return ValidatedResult(
        value=primary_value,
        source=primary_source,
        freshness=freshness_result.freshness,
        verified=(len(sources) > 1 and primary_source != "fallback"),
        tier=tier,
        criticality=indicator["criticality"],
        warnings=all_errors if all_errors else [],
        available=True,
    )


def _try_fetch(
    symbol: str,
    market: str,
    tier: str,
    source: dict,
    context: dict,
) -> tuple[Any, gate.GateResult]:
    """
    尝试从单个数据源获取数据。
    返回 (value, GateResult)。value=None 表示获取失败。
    """
    fn_name = source.get("fn", "")

    # 映射到实际的数据获取函数
    # 这里根据 fn_name 调用对应的底层函数
    # 由于底层函数分散在不同模块，这里做一个路由映射

    try:
        if fn_name in _FETCH_ROUTES:
            value = _FETCH_ROUTES[fn_name](symbol, market, context)
            freshness = gate.check(tier, market)
            return value, freshness
    except Exception as e:
        pass

    return None, gate.GateResult(freshness=Freshness.REJECTED, reason=f"{fn_name}不可用")


# ── 路由映射：fn_name → 底层函数 ────────────
# 这些函数由各模块注册

_FETCH_ROUTES = {}


def register_fetcher(fn_name: str, func):
    """注册数据获取函数"""
    _FETCH_ROUTES[fn_name] = func


# ── 便捷函数 ─────────────────────────────────

def get_k1_values(symbol: str, market: str = "a", context: dict = None) -> dict[str, ValidatedResult]:
    """
    批量获取所有K1核心指标。
    返回 {indicator_name: ValidatedResult}
    """
    results = {}
    for name in registry.list_k1():
        # 跳过不适用于该市场的指标
        ind = registry.get(name)
        if ind and market == "hk" and name in ("margin_balance",):
            continue
        results[name] = fetch_validated(symbol, name, market, context)
    return results


# ── 注册核心获取函数 ─────────────────────────

def _register_all():
    """注册所有数据获取函数（延迟导入避免循环依赖）"""
    from nous.data.collectors.fetchers import finance
    from nous.data import storage as st
    import sqlite3
    from pathlib import Path

    def _daily_db_close(symbol, market, ctx):
        rows = st.get_daily(symbol, limit=1)
        return rows[0]["close"] if rows else None

    def _daily_db_volume(symbol, market, ctx):
        rows = st.get_daily(symbol, limit=1)
        return rows[0]["volume"] if rows else None

    def _daily_db_change(symbol, market, ctx):
        rows = st.get_daily(symbol, limit=2)
        if len(rows) >= 2:
            prev, curr = rows[1]["close"], rows[0]["close"]
            return round((curr - prev) / prev * 100, 2) if prev > 0 else None
        return None

    def _compute_ttm_pe(symbol, market, ctx):
        close = ctx.get("close")
        if close is None:
            rows = st.get_daily(symbol, limit=1)
            close = rows[0]["close"] if rows else None
        if close is None:
            return None
        return finance._compute_ttm_pe(symbol, close, {})

    def _compute_static_pe(symbol, market, ctx):
        fin = finance.fetch_financial_abstract(symbol)
        if not fin or not fin.get("eps"):
            return None
        rows = st.get_daily(symbol, limit=1)
        close = rows[0]["close"] if rows else None
        if close and fin["eps"] > 0:
            return round(close / fin["eps"], 1)
        return None

    def _compute_dynamic_pe(symbol, market, ctx):
        # 动态PE = 最新季度EPS × 4
        import re, akshare as ak, pandas as pd
        try:
            df = ak.stock_financial_abstract(symbol)
            eps_row = df[df["指标"] == "摊薄每股收益_最新股数"]
            if eps_row.empty:
                eps_row = df[df["指标"] == "稀释每股收益"]
            if eps_row.empty:
                eps_row = df[df["指标"] == "基本每股收益"]
            if eps_row.empty:
                return None
            date_cols = sorted([c for c in eps_row.columns if re.match(r"^\d{8}$", str(c))], reverse=True)
            latest = date_cols[0]
            eps_q = float(eps_row[latest].iloc[0])
            rows = st.get_daily(symbol, limit=1)
            close = rows[0]["close"] if rows else None
            if close and eps_q > 0:
                return round(close / (eps_q * 4), 1)
        except Exception:
            pass
        return None

    def _compute_pb(symbol, market, ctx):
        fin = finance.fetch_financial_abstract(symbol)
        if not fin or not fin.get("bvps"):
            return None
        rows = st.get_daily(symbol, limit=1)
        close = rows[0]["close"] if rows else None
        if close and fin["bvps"] > 0:
            return round(close / fin["bvps"], 2)
        return None

    def _fetch_roe(symbol, market, ctx):
        fin = finance.fetch_financial_abstract(symbol)
        return fin.get("roe") if fin else None

    def _fetch_eps(symbol, market, ctx):
        fin = finance.fetch_financial_abstract(symbol)
        return fin.get("eps") if fin else None

    def _db_stock_basic(symbol, market, ctx):
        db_path = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT name, market FROM stock_basic WHERE symbol=?", (symbol,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def _compute_total_mv(symbol, market, ctx):
        rows = st.get_daily(symbol, limit=1)
        close = rows[0]["close"] if rows else None
        eps = _fetch_eps(symbol, market, ctx)
        if close is None or eps is None:
            return None
        # 需要净利润和总股本...简化：从 stock_fundamental 取
        db_path = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT total_mv FROM stock_fundamental WHERE symbol=?", (symbol,)
            ).fetchone()
            conn.close()
            if row and row[0]:
                return row[0]
        return None

    register_fetcher("daily_db_close", _daily_db_close)
    register_fetcher("daily_db_volume", _daily_db_volume)
    register_fetcher("daily_db_change", _daily_db_change)
    register_fetcher("compute_ttm_pe", _compute_ttm_pe)
    register_fetcher("compute_static_pe", _compute_static_pe)
    register_fetcher("compute_dynamic_pe", _compute_dynamic_pe)
    register_fetcher("compute_pb", _compute_pb)
    register_fetcher("fetch_roe", _fetch_roe)
    register_fetcher("fetch_eps", _fetch_eps)
    register_fetcher("db_stock_basic", _db_stock_basic)
    register_fetcher("compute_total_mv", _compute_total_mv)


# 模块加载时自动注册
_register_all()
