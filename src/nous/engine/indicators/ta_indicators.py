"""pandas-ta 技术指标封装 — 30+ 核心指标批量计算

不依赖 TA-Lib（C 编译麻烦），纯 Python pandas-ta。

函数:
- compute_all(symbol) → dict — 返回 30+ 核心指标
- compute_candlestick_patterns(symbol) → dict — 蜡烛形态识别
- compute_volatility_metrics(symbol) → dict — ATR/布林带宽度/历史波动率
"""

import contextlib
import logging
import io
import sys
from typing import Optional

import pandas as pd
import numpy as np
import pandas_ta as ta  # noqa: F401 — 注册 df.ta 访问器

from nous.data.query_engine import get_daily_df

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────
_MIN_BARS = 130  # ADX/一目均衡需要至少 120+ 根 K 线


def _ensure_bars(symbol: str, min_bars: int = _MIN_BARS) -> pd.DataFrame:
    """确保有足够数据，不足时自动扩展获取"""
    df = get_daily_df(symbol, days=min_bars)
    if len(df) < min_bars:
        # 尝试获取更多
        df = get_daily_df(symbol, days=min_bars + 60)
    return df


# ── 主计算函数 ───────────────────────────────────────


def _silent_compute(func):
    """装饰器: 抑制 pandas-ta 的 verbose stdout 输出"""
    def wrapper(*args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return func(*args, **kwargs)
    return wrapper


def compute_all(symbol: str) -> dict:
    """计算单只股票全部 pandas-ta 指标，返回 dict。

    包括: BBANDS/ATR/ADX/RSI(14)/MACD/OBV/一目均衡/EMA(12,26)/
          SMA(20,50)/BB宽度/历史波动率/K线形态

    Args:
        symbol: 股票代码

    Returns:
        dict — 最新值的指标字典，计算失败返回空字段
    """
    df = _ensure_bars(symbol)
    if df.empty or len(df) < 30:
        return _empty_result("Insufficient data")

    result: dict = {
        "_symbol": symbol,
        "_bars": len(df),
        "_status": "ok",
    }

    # 抑制 pandas-ta 的 verbose stdout/stderr 输出
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            _calc_bbands(df, result)
            _calc_atr(df, result)
            _calc_adx(df, result)
            _calc_rsi(df, result)
            _calc_macd(df, result)
            _calc_obv(df, result)
            _calc_ichimoku(df, result)
            _calc_ema_sma(df, result)
            _calc_historical_volatility(df, result)
            _calc_candlestick_patterns(df, result)
        except Exception as e:
            logger.warning(
                "ta_indicators compute_all(%s) error: %s", symbol, e
            )
            result["_status"] = f"partial_error: {e}"

    return result


def compute_candlestick_patterns(symbol: str) -> dict:
    """蜡烛形态识别专用函数"""
    df = _ensure_bars(symbol, min_bars=60)
    result: dict = {"_symbol": symbol, "_bars": len(df), "_status": "ok"}
    _calc_candlestick_patterns(df, result)
    return result


def compute_volatility_metrics(symbol: str) -> dict:
    """波动率指标专用函数：
    - ATR (Average True Range)
    - 布林带宽度 (Bandwidth)
    - 历史波动率 (Historical Volatility)
    """
    df = _ensure_bars(symbol, min_bars=60)
    result: dict = {"_symbol": symbol, "_bars": len(df), "_status": "ok"}
    _calc_atr(df, result)
    _calc_bbands(df, result)
    _calc_historical_volatility(df, result)
    return result


# ── 内部计算函数 ─────────────────────────────────────


def _calc_bbands(df: pd.DataFrame, result: dict) -> None:
    """布林带 (20, 2) — 含带宽"""
    try:
        bb = df.ta.bbands(length=20, std=2, append=False)
        if bb is not None and not bb.empty:
            cols = list(bb.columns)
            # 动态查找列名 (pandas-ta 版本间后缀可能不同)
            upper_col = next((c for c in cols if c.startswith("BBU_")), None)
            mid_col = next((c for c in cols if c.startswith("BBM_")), None)
            lower_col = next((c for c in cols if c.startswith("BBL_")), None)
            bw_col = next((c for c in cols if c.startswith("BBB_")), None)
            bp_col = next((c for c in cols if c.startswith("BBP_")), None)

            if upper_col and bb[upper_col].notna().any():
                result["bb_upper"] = _safe_float(bb[upper_col].iloc[-1])
            if mid_col and bb[mid_col].notna().any():
                result["bb_mid"] = _safe_float(bb[mid_col].iloc[-1])
            if lower_col and bb[lower_col].notna().any():
                result["bb_lower"] = _safe_float(bb[lower_col].iloc[-1])
            if bw_col and bb[bw_col].notna().any():
                result["bb_bandwidth"] = _safe_float(bb[bw_col].iloc[-1])
            if bp_col and bb[bp_col].notna().any():
                result["bb_percent_b"] = _safe_float(bb[bp_col].iloc[-1])

            mid = result.get("bb_mid")
            upper = result.get("bb_upper")
            lower = result.get("bb_lower")
            if mid and upper and lower and mid != 0:
                result["bb_bandwidth_pct"] = round(
                    (upper - lower) / mid * 100, 2
                )
            # 收盘价在布林带中的相对位置
            close = float(df["close"].iloc[-1])
            if lower is not None and upper is not None and (upper - lower) > 0:
                result["bb_position"] = round(
                    (close - lower) / (upper - lower), 4
                )
    except Exception as e:
        logger.debug("BBANDS calc error: %s", e)


def _calc_atr(df: pd.DataFrame, result: dict) -> None:
    """ATR (14)"""
    try:
        atr_series = df.ta.atr(length=14, append=False)
        if atr_series is not None and not atr_series.empty:
            atr_val = _safe_float(atr_series.iloc[-1])
            result["atr"] = atr_val
            close = float(df["close"].iloc[-1])
            if close and close != 0:
                result["atr_pct"] = round(atr_val / close * 100, 2)
    except Exception as e:
        logger.debug("ATR calc error: %s", e)


def _calc_adx(df: pd.DataFrame, result: dict) -> None:
    """ADX (14) — 趋势强度"""
    try:
        adx_df = df.ta.adx(length=14, append=False)
        if adx_df is not None and not adx_df.empty:
            cols = adx_df.columns
            result["adx"] = _safe_float(adx_df.iloc[-1, cols.get_loc("ADX_14")])
            result["adx_plus_di"] = _safe_float(adx_df.iloc[-1, cols.get_loc("DMP_14")])
            result["adx_minus_di"] = _safe_float(adx_df.iloc[-1, cols.get_loc("DMN_14")])
    except Exception as e:
        logger.debug("ADX calc error: %s", e)


def _calc_rsi(df: pd.DataFrame, result: dict) -> None:
    """RSI (14)"""
    try:
        rsi = df.ta.rsi(length=14, append=False)
        if rsi is not None and not rsi.empty:
            result["rsi_14"] = round(float(rsi.iloc[-1]), 2)
    except Exception as e:
        logger.debug("RSI calc error: %s", e)


def _calc_macd(df: pd.DataFrame, result: dict) -> None:
    """MACD (12, 26, 9)"""
    try:
        macd_df = df.ta.macd(fast=12, slow=26, signal=9, append=False)
        if macd_df is not None and not macd_df.empty:
            cols = macd_df.columns
            result["macd_value"] = _safe_float(macd_df.iloc[-1, cols.get_loc("MACD_12_26_9")])
            result["macd_signal"] = _safe_float(macd_df.iloc[-1, cols.get_loc("MACDs_12_26_9")])
            result["macd_hist"] = _safe_float(macd_df.iloc[-1, cols.get_loc("MACDh_12_26_9")])
            # 金叉/死叉判断
            if len(macd_df) >= 3:
                hist = macd_df.iloc[-3:, cols.get_loc("MACDh_12_26_9")].values
                signal_line = macd_df.iloc[-3:, cols.get_loc("MACDs_12_26_9")].values
                macd_line = macd_df.iloc[-3:, cols.get_loc("MACD_12_26_9")].values
                # 最近5天内金叉
                golden = False
                dead = False
                for i in range(1, min(5, len(macd_line))):
                    if (
                        macd_line[-i - 1] < signal_line[-i - 1]
                        and macd_line[-i] > signal_line[-i]
                    ):
                        golden = True
                    if (
                        macd_line[-i - 1] > signal_line[-i - 1]
                        and macd_line[-i] < signal_line[-i]
                    ):
                        dead = True
                result["macd_golden_cross"] = golden
                result["macd_dead_cross"] = dead
    except Exception as e:
        logger.debug("MACD calc error: %s", e)


def _calc_obv(df: pd.DataFrame, result: dict) -> None:
    """OBV (On-Balance Volume) 及 OBV 与价格背离检测"""
    try:
        obv_series = df.ta.obv(append=False)
        if obv_series is not None and not obv_series.empty:
            obv_val = _safe_float(obv_series.iloc[-1])
            result["obv"] = obv_val

            # 简化背离检测: 最近20天 OBV 走势与价格走势对比
            if len(obv_series) >= 20 and len(df) >= 20:
                obv_recent = obv_series.iloc[-20:]
                close_recent = df["close"].iloc[-20:]
                obv_trend = _safe_float(obv_recent.iloc[-1]) - _safe_float(
                    obv_recent.iloc[0]
                )
                price_trend = float(close_recent.iloc[-1]) - float(
                    close_recent.iloc[0]
                )
                # OBV 升但价格跌 → 主力暗中吸筹（看涨背离）
                if obv_trend > 0 and price_trend < 0:
                    result["obv_divergence"] = "bullish"
                # OBV 降但价格升 → 主力暗中派发（看跌背离）
                elif obv_trend < 0 and price_trend > 0:
                    result["obv_divergence"] = "bearish"
                else:
                    result["obv_divergence"] = "none"
    except Exception as e:
        logger.debug("OBV calc error: %s", e)


def _calc_ichimoku(df: pd.DataFrame, result: dict) -> None:
    """一目均衡表 — 当前价格在云层中的位置"""
    try:
        ichi = df.ta.ichimoku(append=False)
        if ichi is not None:
            # ichimoku returns a DataFrame with multiple columns
            if isinstance(ichi, tuple):
                ichi = ichi[0]  # first element is the main DataFrame
            if ichi is not None and not ichi.empty:
                cols = ichi.columns
                # 仅提取最新值
                if "ISA_9" in cols:
                    result["ichi_tenkan"] = _safe_float(
                        ichi.iloc[-1, cols.get_loc("ISA_9")]
                    )
                if "ISB_26" in cols:
                    result["ichi_kijun"] = _safe_float(
                        ichi.iloc[-1, cols.get_loc("ISB_26")]
                    )
                if "ITS_9" in cols:
                    result["ichi_senkou_a"] = _safe_float(
                        ichi.iloc[-1, cols.get_loc("ITS_9")]
                    )
                if "IKS_26" in cols:
                    result["ichi_senkou_b"] = _safe_float(
                        ichi.iloc[-1, cols.get_loc("IKS_26")]
                    )
                if "ICS_26" in cols:
                    result["ichi_chikou"] = _safe_float(
                        ichi.iloc[-1, cols.get_loc("ICS_26")]
                    )

                # 价格在云层中的位置
                close = float(df["close"].iloc[-1])
                sa = result.get("ichi_senkou_a")
                sb = result.get("ichi_senkou_b")
                if sa is not None and sb is not None:
                    cloud_top = max(sa, sb)
                    cloud_bot = min(sa, sb)
                    if close > cloud_top:
                        result["ichi_cloud_position"] = "above"
                    elif close < cloud_bot:
                        result["ichi_cloud_position"] = "below"
                    else:
                        result["ichi_cloud_position"] = "inside"
    except Exception as e:
        logger.debug("Ichimoku calc error: %s", e)


def _calc_ema_sma(df: pd.DataFrame, result: dict) -> None:
    """EMA(12, 26) + SMA(20, 50)"""
    try:
        close = df["close"]
        result["ema_12"] = round(float(close.ewm(span=12, adjust=False).mean().iloc[-1]), 2)
        result["ema_26"] = round(float(close.ewm(span=26, adjust=False).mean().iloc[-1]), 2)
        result["sma_20"] = round(float(close.rolling(20).mean().iloc[-1]), 2) if len(df) >= 20 else None
        result["sma_50"] = round(float(close.rolling(50).mean().iloc[-1]), 2) if len(df) >= 50 else None
        result["ema_cross"] = "bullish" if result["ema_12"] > result["ema_26"] else "bearish"
    except Exception as e:
        logger.debug("EMA/SMA calc error: %s", e)


def _calc_historical_volatility(df: pd.DataFrame, result: dict) -> None:
    """历史波动率 (20 日年化)"""
    try:
        if len(df) >= 21:
            log_ret = np.log(df["close"] / df["close"].shift(1))
            hv_20 = log_ret.tail(20).std() * np.sqrt(252)  # 年化
            result["hv_20"] = round(float(hv_20) * 100, 2)  # 百分比

            hv_60 = log_ret.tail(60).std() * np.sqrt(252) if len(df) >= 61 else None
            if hv_60 is not None:
                result["hv_60"] = round(float(hv_60) * 100, 2)
    except Exception as e:
        logger.debug("HV calc error: %s", e)


def _calc_candlestick_patterns(df: pd.DataFrame, result: dict) -> None:
    """K 线形态识别 — 看涨/看跌信号

    使用 pandas-ta 内置的蜡烛识别，将多个形态聚合为看涨/看跌摘要。
    不依赖 TA-Lib: 使用原生 pandas-ta 实现的形态（如 doji）。
    """
    try:
        # 只使用 pandas-ta 原生支持的形态（不需要 TA-Lib）
        patterns = {}

        # 看涨形态 — doji 是 pandas-ta 原生支持的
        bullish_patterns = {
            "cdl_doji": df.ta.cdl_pattern(name="doji", append=False),
        }
        # 看跌形态 (pandas-ta 原生)
        bearish_patterns = {}

        latest_val: dict[str, Optional[int]] = {}

        # 汇总看涨信号
        bullish_signals = []
        for name, series in bullish_patterns.items():
            if series is not None and not series.empty:
                val = int(series.iloc[-1]) if pd.notna(series.iloc[-1]) else 0
                latest_val[name] = val
                if val != 0:
                    bullish_signals.append(name)

        # 汇总看跌信号
        bearish_signals = []
        for name, series in bearish_patterns.items():
            if series is not None and not series.empty:
                val = int(series.iloc[-1]) if pd.notna(series.iloc[-1]) else 0
                latest_val[name] = val
                if val != 0:
                    bearish_signals.append(name)

        result["cdl_bullish"] = bullish_signals
        result["cdl_bearish"] = bearish_signals
        result["cdl_bullish_count"] = len(bullish_signals)
        result["cdl_bearish_count"] = len(bearish_signals)

        # 最新一根 K 线形态摘要
        active_bullish = [s.replace("cdl_", "") for s in bullish_signals]
        active_bearish = [s.replace("cdl_", "") for s in bearish_signals]

        if active_bullish:
            result["cdl_active_bullish"] = active_bullish
        if active_bearish:
            result["cdl_active_bearish"] = active_bearish

        # 净信号
        net = len(bullish_signals) - len(bearish_signals)
        if net > 0:
            result["cdl_net_signal"] = "bullish"
        elif net < 0:
            result["cdl_net_signal"] = "bearish"
        else:
            result["cdl_net_signal"] = "neutral"

    except Exception as e:
        logger.debug("Candlestick pattern calc error: %s", e)


# ── 辅助函数 ─────────────────────────────────────────


def _safe_float(val) -> Optional[float]:
    """安全转浮点，None/NaN → None"""
    if val is None:
        return None
    try:
        v = float(val)
        if np.isnan(v) or np.isinf(v):
            return None
        return round(v, 4)
    except (ValueError, TypeError):
        return None


def _empty_result(reason: str = "No data") -> dict:
    return {
        "_symbol": "",
        "_bars": 0,
        "_status": reason,
    }
