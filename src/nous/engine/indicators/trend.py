"""趋势指标：MA金叉、MACD、RSI、价格强度 — DuckDB 查询 + pandas-ta 增强"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

from nous.data.query_engine import get_daily_df as _qe_get_daily_df

logger = logging.getLogger(__name__)


def get_daily_df(symbol: str, limit: int = 120) -> pd.DataFrame:
    """从 DuckDB (ATTACH SQLite) 获取日线 DataFrame，按 trade_date 升序"""
    return _qe_get_daily_df(symbol, days=limit)


def compute_ma_cross(df: pd.DataFrame, short: int = 5, long: int = 20,
                     lookback_days: int = 5) -> tuple[bool, Optional[int]]:
    """
    MA 金叉检测。
    返回: (has_cross, days_since_cross)
    days_since_cross: 最近一次金叉距今多少天（0=今天）
    """
    if len(df) < long + 1:
        return False, None

    ma_s = df["close"].rolling(window=short).mean()
    ma_l = df["close"].rolling(window=long).mean()

    # 金叉: MA5 上穿 MA20（前一天 MA5 < MA20 且今天 MA5 > MA20）
    prev_s, prev_l = ma_s.shift(1), ma_l.shift(1)
    cross_up = (prev_s < prev_l) & (ma_s > ma_l)

    # 找最近一次金叉
    cross_indices = cross_up[cross_up].index
    if len(cross_indices) == 0:
        return False, None

    last_cross_idx = cross_indices[-1]
    days_since = len(df) - 1 - last_cross_idx

    if days_since <= lookback_days:
        return True, days_since
    return False, days_since


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                 signal: int = 9) -> dict:
    """
    MACD 指标。
    返回: {dif, dea, macd_hist, golden_cross, dead_cross, position}
    """
    if len(df) < slow + signal:
        return {"dif": None, "dea": None, "macd_hist": None,
                "golden_cross": False, "dead_cross": False, "position": None}

    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = 2 * (dif - dea)

    # 最近 5 天内是否金叉
    prev_dif, prev_dea = dif.shift(1), dea.shift(1)
    cross_up = (prev_dif < prev_dea) & (dif > dea)
    recent_cross = cross_up.tail(5).any()

    return {
        "dif": round(float(dif.iloc[-1]), 4),
        "dea": round(float(dea.iloc[-1]), 4),
        "macd_hist": round(float(macd_hist.iloc[-1]), 4),
        "golden_cross": bool(recent_cross),
        "dead_cross": bool(((prev_dif > prev_dea) & (dif < dea)).tail(5).any()),
        "position": "above_zero" if float(dif.iloc[-1]) > 0 else "below_zero",
    }


def compute_rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """RSI(14)"""
    if len(df) < period + 1:
        return None

    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


def compute_price_strength(df: pd.DataFrame, window: int = 60,
                           high_pct: float = 0.80) -> dict:
    """
    价格强度：当前价距 N 日最高价的百分比。
    返回: {strength_pct, near_high}
    """
    if len(df) < window:
        return {"strength_pct": None, "near_high": False}

    high_n = df["close"].tail(window).max()
    current = df["close"].iloc[-1]
    pct = round(current / high_n * 100, 1)
    return {
        "strength_pct": pct,
        "near_high": pct >= high_pct * 100,
    }


def compute_momentum(df: pd.DataFrame, period_short: int = 5,
                     period_long: int = 20) -> dict:
    """
    动量因子：短期和中期涨跌幅。
    返回: {ret_5d, ret_20d, momentum_score}
    """
    if len(df) < period_long + 1:
        return {"ret_5d": None, "ret_20d": None, "momentum_score": 0}

    close = df["close"]
    current = close.iloc[-1]
    
    ret_5d = (current - close.iloc[-period_short-1]) / close.iloc[-period_short-1] * 100 if len(df) > period_short else None
    ret_20d = (current - close.iloc[-period_long-1]) / close.iloc[-period_long-1] * 100 if len(df) > period_long else None

    # 动量打分：5日涨幅和20日涨幅加权
    score = 0
    if ret_5d is not None:
        if ret_5d >= 15:
            score += 10
        elif ret_5d >= 8:
            score += 7
        elif ret_5d >= 3:
            score += 4
        elif ret_5d > 0:
            score += 2
        elif ret_5d < -5:
            score -= 3
    
    if ret_20d is not None:
        if ret_20d >= 25:
            score += 8
        elif ret_20d >= 10:
            score += 5
        elif ret_20d >= 3:
            score += 2
        elif ret_20d < -10:
            score -= 3

    return {
        "ret_5d": round(ret_5d, 1) if ret_5d else None,
        "ret_20d": round(ret_20d, 1) if ret_20d else None,
        "momentum_score": min(10, max(-5, score))
    }


def trend_scores(symbol: str, cfg: dict, daily_df=None) -> dict:
    """
    趋势维度打分（0-10分），K1指标带数据溯源。
    daily_df: 预加载的日线 DataFrame (可选)
    """
    tcfg = cfg["trend"]
    if daily_df is not None and not daily_df.empty:
        df = daily_df
    else:
        df = get_daily_df(symbol, limit=120)

    result = {
        "symbol": symbol,
        "ma_cross": False,
        "days_since_cross": None,
        "macd_golden": False,
        "macd_position": None,
        "rsi": None,
        "strength_pct": None,
        "near_high": False,
        "score": None,
        # ── K1 溯源字段 ──
        "k1_ready": False,
        "k1_missing": [],
    }

    if df.empty:
        result["k1_missing"] = ["ma_cross", "macd_golden"]
        return result

    # MA 金叉
    has_cross, days_cross = compute_ma_cross(
        df, short=tcfg["ma_short"], long=tcfg["ma_long"],
        lookback_days=tcfg["ma_cross_days"]
    )
    result["ma_cross"] = has_cross
    result["days_since_cross"] = days_cross

    # MACD
    macd = compute_macd(df, fast=tcfg["macd_fast"], slow=tcfg["macd_slow"],
                        signal=tcfg["macd_signal"])
    result["macd_golden"] = macd["golden_cross"]
    result["macd_position"] = macd["position"]

    # RSI
    rsi = compute_rsi(df, period=tcfg["rsi_period"])
    result["rsi"] = rsi

    # 价格强度
    ps = compute_price_strength(df, window=tcfg["price_strength_window"],
                                high_pct=tcfg["price_strength_high_pct"])
    result["strength_pct"] = ps["strength_pct"]
    result["near_high"] = ps["near_high"]

    # ── 打分（新增动量因子）──
    score = 0
    count = 0

    if has_cross:
        score += 8  # 金叉给 8 分
        count += 1

    if macd["golden_cross"]:
        score += 6  # MACD 金叉给 6 分
        count += 1
    elif macd["position"] == "above_zero":
        score += 4  # DIF 在零轴上方给 4 分
        count += 1

    if rsi and tcfg["rsi_min"] <= rsi <= tcfg["rsi_max"]:
        mid = (tcfg["rsi_min"] + tcfg["rsi_max"]) / 2
        score += min(10, 10 * (1 - abs(rsi - mid) / (tcfg["rsi_max"] - tcfg["rsi_min"])))
        count += 1

    if ps["near_high"]:
        score += 7  # 接近新高给 7 分
        count += 1

    # 动量因子：捕获短期/中期涨跌幅
    mom = compute_momentum(df)
    result["ret_5d"] = mom["ret_5d"]
    result["ret_20d"] = mom["ret_20d"]
    if mom["momentum_score"] != 0:
        score += mom["momentum_score"]
        count += 1

    # ── pandas-ta 增强维度 ──
    ta_cfg = cfg.get("ta_indicators", {})
    if ta_cfg.get("enabled", True):
        try:
            from nous.engine.indicators.ta_indicators import compute_all as _ta_compute

            ta = _ta_compute(symbol)
            ta_status = ta.get("_status", "")
            if ta_status == "ok" or "partial" in ta_status:
                # ADX 趋势强度
                adx = ta.get("adx")
                if adx is not None:
                    result["adx"] = adx
                    adx_th = ta_cfg.get("adx_threshold", 25)
                    adx_strong = ta_cfg.get("adx_strong_threshold", 40)
                    if adx >= adx_strong:
                        score += 8  # 强趋势
                    elif adx >= adx_th:
                        score += 4  # 中等趋势
                    count += 1

                # 布林带位置: close 接近下轨可能反弹
                bb_pos = ta.get("bb_position")
                if bb_pos is not None:
                    result["bb_position"] = bb_pos
                    # bb_position 0=下轨, 1=上轨
                    if bb_pos <= 0.15:
                        score += 3  # 接近下轨，可能反弹
                    elif bb_pos >= 0.85:
                        score -= 2  # 接近上轨，注意压力
                    count += 1

                # K 线形态加分
                cdl_net = ta.get("cdl_net_signal")
                if cdl_net == "bullish":
                    score += 3
                    count += 1
                elif cdl_net == "bearish":
                    score -= 2
                    count += 1

                # 一目均衡云层位置
                cloud_pos = ta.get("ichi_cloud_position")
                if cloud_pos == "above":
                    score += 3  # 在云层上方，趋势偏多
                    count += 1
                elif cloud_pos == "below":
                    score -= 2  # 在云层下方，趋势偏空
                    count += 1

        except Exception as e:
            logger.debug("ta_indicators enhancement failed: %s", e)

    result["score"] = round(score / count, 1) if count > 0 else 0
    
    # K1溯源：MA金叉和MACD金叉都正常计算（df非空即real）
    result["k1_ready"] = True
    return result
