"""量价分析：成交量放大 — DuckDB查询 + pandas-ta增强"""
import logging
from typing import Optional

from nous.engine.indicators.trend import get_daily_df

logger = logging.getLogger(__name__)


def compute_volume_ratio(df, short: int = 5, long: int = 20) -> dict:
    """
    成交量放大比率 = 近N日均量 / 近M日均量。
    返回: {ratio, signal}
    """
    if len(df) < long:
        return {"ratio": None, "signal": False}

    vol_short = df["volume"].tail(short).mean()
    vol_long = df["volume"].tail(long).mean()

    if vol_long == 0:
        return {"ratio": None, "signal": False}

    ratio = round(vol_short / vol_long, 2)
    return {"ratio": ratio, "signal": ratio >= 1.5}


def volume_scores(symbol: str, cfg: dict, daily_df=None) -> dict:
    """
    量价维度打分（0-10分），K1指标带数据溯源。
    daily_df: 预加载的日线 DataFrame (可选)
    """
    tcfg = cfg["trend"]
    if daily_df is not None and not daily_df.empty:
        df = daily_df
    else:
        df = get_daily_df(symbol, limit=120)

    result = {
        "symbol": symbol,
        "volume_ratio": None,
        "score": None,
        # ── K1 溯源字段 ──
        "k1_ready": False,
        "k1_missing": [],
    }

    if df.empty:
        result["k1_missing"] = ["volume_ratio"]
        return result

    vol = compute_volume_ratio(
        df, short=tcfg["volume_short"], long=tcfg["volume_long"]
    )
    result["volume_ratio"] = vol["ratio"]

    ratio = vol["ratio"]
    threshold = tcfg["volume_ratio_min"]

    if ratio and ratio >= threshold:
        result["score"] = min(10, 5 * ratio)
    else:
        result["score"] = 0

    # ── pandas-ta 增强维度 ──
    ta_cfg = cfg.get("ta_indicators", {})
    if ta_cfg.get("enabled", True):
        try:
            from nous.engine.indicators.ta_indicators import compute_all as _ta_compute

            ta = _ta_compute(symbol)
            ta_status = ta.get("_status", "")
            if ta_status == "ok" or "partial" in ta_status:
                # ATR 波动率调整
                atr_pct = ta.get("atr_pct")
                if atr_pct is not None:
                    result["atr_pct"] = atr_pct
                    atr_warn = ta_cfg.get("atr_warning_pct", 3.0)
                    if atr_pct > atr_warn:
                        result["score"] = min(10, (result.get("score") or 0) + 2)
                        logger.debug("  %s ATR=%.1f%% > %.0f%%, +2分波动率", symbol, atr_pct, atr_warn)

                # OBV 背离
                obv_div = ta.get("obv_divergence")
                if obv_div == "bullish":
                    result["obv_divergence"] = "bullish"
                    result["score"] = min(10, (result.get("score") or 0) + 3)
                elif obv_div == "bearish":
                    result["obv_divergence"] = "bearish"
                    result["score"] = max(0, (result.get("score") or 0) - 2)

        except Exception as e:
            logger.debug("ta_indicators volume enhancement failed: %s", e)

    # K1溯源：volume_ratio正常计算（df非空且vol_long>0）
    result["k1_ready"] = True
    return result
