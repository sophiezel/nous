"""Alpha158-style extras + WorldQuant 101 formulaic subset (K9/K10).

No qlib runtime dependency — expressions ported in pandas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_alpha158_subset(df: pd.DataFrame) -> pd.DataFrame:
    """Add complementary Alpha158-style features (K10_*)."""
    g = df.groupby("symbol", group_keys=False)

    df["K10_roc_12"] = g["close"].pct_change(12)
    df["K10_roc_24"] = g["close"].pct_change(24)
    ma5 = g["close"].transform(lambda x: x.rolling(5, min_periods=3).mean())
    ma20 = g["close"].transform(lambda x: x.rolling(20, min_periods=10).mean())
    df["K10_bias_5"] = df["close"] / ma5 - 1
    df["K10_bias_20"] = df["close"] / ma20 - 1

    df["K10_hl_ratio"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["K10_co_ratio"] = (df["close"] - df["open"]) / df["open"].replace(0, np.nan)
    df["K10_upper_shadow"] = (
        (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"].replace(0, np.nan)
    )
    df["K10_lower_shadow"] = (
        (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"].replace(0, np.nan)
    )

    vol_ma60 = g["volume"].transform(lambda x: x.rolling(60, min_periods=20).mean())
    df["K10_vol_ma60_ratio"] = df["volume"] / vol_ma60.replace(0, np.nan)
    df["K10_amt_ma5"] = g["amount"].transform(lambda x: x.rolling(5, min_periods=3).mean())
    df["K10_amt_ma20"] = g["amount"].transform(lambda x: x.rolling(20, min_periods=10).mean())
    df["K10_amt_ratio"] = df["K10_amt_ma5"] / df["K10_amt_ma20"].replace(0, np.nan)
    df["K10_vol_std_20"] = g["volume"].transform(
        lambda x: x.pct_change().rolling(20, min_periods=10).std()
    )

    ret1 = g["close"].pct_change()
    df["K10_pvt_chg"] = ret1 * df["volume"]
    df["K10_pvt_ma10"] = g["K10_pvt_chg"].transform(
        lambda x: x.rolling(10, min_periods=5).mean()
    )

    delta = g["close"].diff()
    df["_gain"] = delta.clip(lower=0)
    df["_loss"] = (-delta).clip(lower=0)
    avg_gain = df.groupby("symbol")["_gain"].transform(
        lambda x: x.rolling(14, min_periods=7).mean()
    )
    avg_loss = df.groupby("symbol")["_loss"].transform(
        lambda x: x.rolling(14, min_periods=7).mean()
    )
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["K10_rsi14"] = 100 - (100 / (1 + rs))
    df.drop(columns=["_gain", "_loss"], inplace=True, errors="ignore")

    std20 = g["close"].transform(lambda x: x.rolling(20, min_periods=10).std())
    df["K10_bb_pos"] = (df["close"] - ma20) / (2 * std20.replace(0, np.nan))

    return df


def _ts_corr(df: pd.DataFrame, a: str, b: str, window: int) -> pd.Series:
    parts = []
    for _, g in df.groupby("symbol"):
        parts.append(g[a].rolling(window, min_periods=max(3, window // 2)).corr(g[b]))
    return pd.concat(parts).sort_index()


def add_wq_alpha_subset(df: pd.DataFrame) -> pd.DataFrame:
    """Hand-picked WorldQuant 101-style formulas as K9_wqNN (no industry alphas)."""
    g = df.groupby("symbol", group_keys=False)
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]

    ret = g["close"].pct_change()
    dvol2 = g["volume"].pct_change(2)
    df["K9_wq002"] = -(ret * dvol2)

    df["K9_wq006"] = -_ts_corr(df, "open", "volume", 10)

    adv20 = g["volume"].transform(lambda x: x.rolling(20, min_periods=10).mean())
    df["K9_wq007"] = np.where(
        adv20 < v,
        -np.sign(g["close"].diff(7)) * np.log(v.replace(0, np.nan)),
        -1.0,
    )

    df["K9_wq012"] = np.sign(g["volume"].diff(1)) * (-g["close"].diff(1))

    corr5 = _ts_corr(df, "volume", "close", 5)
    df["_corr5"] = corr5
    df["K9_wq026"] = -df.groupby("symbol")["_corr5"].transform(
        lambda x: x.rolling(3, min_periods=1).max()
    )
    df.drop(columns=["_corr5"], inplace=True, errors="ignore")

    df["K9_wq033"] = -(1 - o / c.replace(0, np.nan))

    vwap = df["amount"] / v.replace(0, np.nan)
    df["K9_wq041"] = np.sqrt((h * l).clip(lower=0)) - vwap

    hl = (h - l).replace(0, np.nan)
    mid = ((c - l) - (h - c)) / hl
    df["_mid"] = mid
    df["K9_wq053"] = -df.groupby("symbol")["_mid"].diff(9)
    df.drop(columns=["_mid"], inplace=True, errors="ignore")

    df["K9_wq054"] = (-l + c) * o / ((l - h).replace(0, np.nan) * c.replace(0, np.nan))
    df["K9_wq101"] = (c - o) / ((h - l) + 0.001)

    low_min5 = g["low"].transform(lambda x: x.rolling(5, min_periods=3).min())
    df["K9_wq009"] = np.where(l == low_min5, -g["close"].diff(1), 0.0)

    high_ma20 = g["high"].transform(lambda x: x.rolling(20, min_periods=10).mean())
    df["K9_wq023"] = np.where(high_ma20 < h, -g["high"].diff(2), 0.0)

    return df
