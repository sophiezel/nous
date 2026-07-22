"""双源交叉验证 — 简单中位数共识引擎

提供:
  - median_consensus(df_sina, df_tencent) → merged DataFrame 带分歧标记
  - reconcile_pair(close_sina, close_tencent) → 分歧检测 + S0/S1/S2 分级

S0: 双源一致 (分歧 ≤ 0.1%)
S1: 轻度分歧 (0.1% < 分歧 ≤ 1%)
S2: 严重分歧 (分歧 > 1%) — 记录告警
"""

from typing import Optional

import pandas as pd


# ── 分歧分级 ──────────────────────────────────────

def reconcile_pair(
    close_sina: float,
    close_tencent: float,
    symbol: str = "",
    trade_date: str = "",
) -> tuple[float, str, float]:
    """比较双源收盘价，返回 (共识价, 分级, 分歧比例)。

    共识价 = 双源中位数。
    分级:
      S0 — 一致 (≤0.1%)
      S1 — 轻度 (≤1%)
      S2 — 严重 (>1%)

    Args:
        close_sina: Sina 源收盘价
        close_tencent: 腾讯源收盘价
        symbol: 股票代码（告警用）
        trade_date: 交易日（告警用）

    Returns:
        (consensus_close, grade, divergence_pct)
    """
    if close_sina is None or close_tencent is None or close_sina == 0:
        return (close_tencent if close_tencent else close_sina, "S1", 0.0)

    if close_tencent is None or close_tencent == 0:
        return (close_sina, "S1", 0.0)

    # 中位数共识
    consensus = (close_sina + close_tencent) / 2.0

    # 分歧比例（相对均值）
    div_pct = abs(close_sina - close_tencent) / max(consensus, 0.001) * 100.0

    if div_pct <= 0.1:
        grade = "S0"
    elif div_pct <= 1.0:
        grade = "S1"
    else:
        grade = "S2"
        if symbol and trade_date:
            print(
                f"  ⚠️ 双源分歧 [{symbol}@{trade_date}]: "
                f"Sina={close_sina:.4f} Tencent={close_tencent:.4f} "
                f"分歧={div_pct:.2f}%"
            )

    return (consensus, grade, div_pct)


# ── 完整 DataFrame 合并 ──────────────────────────

def median_consensus(
    df_sina: pd.DataFrame,
    df_tencent: pd.DataFrame,
    symbol: str = "",
) -> pd.DataFrame:
    """将 Sina 和腾讯源的日线 DataFrame 合并为单源共识 DataFrame。

    策略:
      1. 以 Sina 为主数据（因为它有 volume）
      2. 腾讯源用于交叉验证 close
      3. 若某日只有单源数据，直接采用
      4. 双源都有时，OHLC 取中位数，分歧 > 1% 记录告警

    Args:
        df_sina: Sina 源日线 [trade_date, open, high, low, close, volume, amount]
        df_tencent: 腾讯源日线 [trade_date, open, high, low, close, amount]
        symbol: 股票代码（告警用）

    Returns:
        合并后的 DataFrame [trade_date, open, high, low, close, volume, amount]
        含额外列 _grade 标记分歧等级
    """
    if df_sina is None or df_sina.empty:
        if df_tencent is not None and not df_tencent.empty:
            df = df_tencent.copy()
            df["volume"] = 0
            df["_grade"] = "S1"  # 仅有腾讯源
            return df
        return pd.DataFrame(columns=["trade_date", "open", "high", "low",
                                      "close", "volume", "amount", "_grade"])

    if df_tencent is None or df_tencent.empty:
        df = df_sina.copy()
        df["_grade"] = "S1"  # 仅有 Sina 源
        return df

    # 确保 trade_date 一致可合并
    df_s = df_sina.copy()
    df_t = df_tencent.copy()
    df_s["trade_date"] = pd.to_datetime(df_s["trade_date"]).dt.date
    df_t["trade_date"] = pd.to_datetime(df_t["trade_date"]).dt.date

    # 腾讯缺 volume，补 0
    if "volume" not in df_t.columns:
        df_t["volume"] = 0

    # 合并：以 Sina 为主，右连接腾讯
    merged = pd.merge(
        df_s, df_t,
        on="trade_date",
        suffixes=("_sina", "_tencent"),
        how="outer",
        indicator=True,
    )

    results = []
    for _, row in merged.iterrows():
        if row["_merge"] == "left_only":
            # 仅有 Sina
            results.append({
                "trade_date": row["trade_date"],
                "open": row.get("open_sina"),
                "high": row.get("high_sina"),
                "low": row.get("low_sina"),
                "close": row.get("close_sina"),
                "volume": row.get("volume_sina", 0),
                "amount": row.get("amount_sina"),
                "_grade": "S1",
            })
        elif row["_merge"] == "right_only":
            # 仅有腾讯
            results.append({
                "trade_date": row["trade_date"],
                "open": row.get("open_tencent"),
                "high": row.get("high_tencent"),
                "low": row.get("low_tencent"),
                "close": row.get("close_tencent"),
                "volume": 0,
                "amount": row.get("amount_tencent"),
                "_grade": "S1",
            })
        else:
            # 双源都有
            close_s = row.get("close_sina", 0) or 0
            close_t = row.get("close_tencent", 0) or 0

            # 中位数共识
            consensus, grade, _ = reconcile_pair(
                float(close_s), float(close_t),
                symbol=symbol,
                trade_date=str(row["trade_date"]),
            )

            # OHLC 取双源中位数
            open_v = (row.get("open_sina", 0) or 0 + row.get("open_tencent", 0) or 0) / 2.0
            high_v = (row.get("high_sina", 0) or 0 + row.get("high_tencent", 0) or 0) / 2.0
            low_v = (row.get("low_sina", 0) or 0 + row.get("low_tencent", 0) or 0) / 2.0

            results.append({
                "trade_date": row["trade_date"],
                "open": round(open_v, 2),
                "high": round(high_v, 2),
                "low": round(low_v, 2),
                "close": round(consensus, 2),
                "volume": row.get("volume_sina", 0) or 0,
                "amount": (row.get("amount_sina", 0) or 0 + row.get("amount_tencent", 0) or 0) / 2.0,
                "_grade": grade,
            })

    result_df = pd.DataFrame(results)
    if result_df.empty:
        return result_df

    std_cols = ["trade_date", "open", "high", "low", "close", "volume", "amount", "_grade"]
    return result_df[std_cols]


# ── N源投票共识 ──────────────────────────────────

def n_way_consensus(results: list) -> dict:
    """N源投票共识（≥2源）。

    Args:
        results: [{'source': str, 'data': DataFrame}, ...]

    Returns:
        {'data': float|DataFrame, 'grade': 'S0'|'S1'|'S2', 'confidence': float}
    """
    import statistics as _stats

    values = []
    for r in results:
        data = r.get('data')
        if data is None:
            continue
        if hasattr(data, 'iloc'):
            # DataFrame — 取最新close
            if 'close' in data.columns and len(data) > 0:
                values.append(float(data['close'].iloc[-1]))
        elif isinstance(data, (int, float)):
            values.append(float(data))
        elif isinstance(data, str):
            try:
                v = float(data.strip().split(',')[3]) if ',' in data else float(data)
                values.append(v)
            except (ValueError, IndexError):
                pass

    if len(values) >= 3:
        median = _stats.median(values)
        divs = [abs(v - median) / max(median, 0.001) * 100 for v in values]
        max_div = max(divs)
        if max_div <= 0.1:
            return {'data': median, 'grade': 'S0', 'confidence': 0.95}
        elif max_div <= 1.0:
            return {'data': median, 'grade': 'S1', 'confidence': 0.80}
        else:
            return {'data': median, 'grade': 'S2', 'confidence': 0.50}
    elif len(values) == 2:
        v1, v2 = values
        consensus = (v1 + v2) / 2.0
        div_pct = abs(v1 - v2) / max(consensus, 0.001) * 100
        if div_pct <= 0.1:
            return {'data': consensus, 'grade': 'S0', 'confidence': 0.95}
        elif div_pct <= 1.0:
            return {'data': consensus, 'grade': 'S1', 'confidence': 0.80}
        else:
            return {'data': consensus, 'grade': 'S2', 'confidence': 0.50}
    elif len(values) == 1:
        return {'data': values[0], 'grade': 'S2', 'confidence': 0.30}
    return {'data': None, 'grade': 'S2', 'confidence': 0.0}


# ── Backward compatibility shim ─────────────────────────────────────────
def multi_source_fetch(source_a_fn, source_b_fn, source_a_name="A", source_b_name="B", symbol="", **kwargs):
    """Stub: fetch from two sources, return consensus. Keeps legacy import working."""
    import logging
    _log = logging.getLogger(__name__)
    _log.debug("multi_source_fetch stub: %s/%s for %s", source_a_name, source_b_name, symbol)
    if source_a_fn:
        try:
            return source_a_fn(**kwargs), {"source": source_a_name, "symbol": symbol}
        except Exception:
            pass
    if source_b_fn:
        try:
            return source_b_fn(**kwargs), {"source": source_b_name, "symbol": symbol}
        except Exception:
            pass
    return None, {"source": "none", "symbol": symbol, "error": "both sources failed"}


class MultiSourceMeta:
    """Stub dataclass for legacy import compatibility."""
    def __init__(self, source="unknown", symbol="", grade="S2", confidence=0.0, divergence_pct=0.0):
        self.source = source
        self.symbol = symbol
        self.grade = grade
        self.confidence = confidence
        self.divergence_pct = divergence_pct


def update_source_reliability(source_name, success=True, latency_ms=0):
    """Stub: update source reliability score. Keeps legacy import working."""
    pass

def generate_provenance_log(results, symbol, trade_date):
    """Stub: generate provenance audit log."""
    return {"symbol": symbol, "trade_date": trade_date, "sources": len(results) if results else 0}


def get_source_weight(source_name):
    """Stub: get weight for given source. Returns 0.5 for unknown sources."""
    weights = {"sina": 1.0, "akshare": 0.9, "eastmoney": 0.85, "tencent": 0.8}
    return weights.get(source_name, 0.5)

def write_to_outbox(collector_name, data, effective_date):
    """Stub: queue data for outbox delivery."""
    pass

def get_with_cache_fallback(key, fetch_fn, ttl_sec=300):
    """Stub: get value from cache or fetch + cache."""
    try:
        return fetch_fn()
    except Exception:
        return None

# Re-export grade constants for legacy compatibility
DIVERGENCE_S0 = 0.001   # <0.1%
DIVERGENCE_S1 = 0.01    # <1.0%
DIVERGENCE_S2 = 1.0     # >1.0%
