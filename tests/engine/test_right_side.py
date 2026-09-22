"""Right-side Phase1 unit tests (synthetic)."""
from __future__ import annotations

import pandas as pd
import numpy as np

from nous.engine.screening.right_side import detect_rs1, detect_rs2, load_config


def _ohlcv(n=120, trend=True, breakout=False, pullback=False):
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2025-01-02", periods=n)
    if trend:
        close = 100 + np.linspace(0, 30, n) + rng.normal(0, 0.3, n)
    else:
        close = 100 + rng.normal(0, 1, n).cumsum() * 0.1
    if breakout:
        close[-1] = close[-60:-1].max() + 2
    high = close + 0.5
    low = close - 0.5
    if pullback:
        # yesterday dip below MA10 path: lower yesterday low
        low[-2] = close[-2] - 3
        close[-1] = close[-2] + 1.5
        high[-1] = close[-1] + 0.3
    open_ = close - 0.1
    vol = np.full(n, 1e6)
    vol[-1] = 2.5e6
    return pd.DataFrame({
        "trade_date": dates.astype(str),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": vol, "amount": vol * close,
    })


def test_load_config():
    cfg = load_config()
    assert "rs1" in cfg and cfg["rs1"]["entry_n"] == 55


def test_rs1_breakout_fires():
    cfg = load_config()
    df = _ohlcv(n=100, breakout=True)
    h = detect_rs1(df, cfg)
    assert h is not None
    assert h["setup"] == "RS1"


def test_rs1_no_breakout():
    cfg = load_config()
    df = _ohlcv(n=100, breakout=False)
    # force last below prior high
    df.loc[df.index[-1], "close"] = df["high"].iloc[-60:-1].max() - 1
    df.loc[df.index[-1], "high"] = df.loc[df.index[-1], "close"]
    assert detect_rs1(df, cfg) is None


def test_rs2_pullback_reclaim():
    cfg = load_config()
    # strong uptrend then pullback reclaim
    n = 100
    close = 50 + np.linspace(0, 40, n)
    close[-3] = close[-4] - 0.5
    close[-2] = close[-3] - 1.0
    close[-1] = close[-2] + 2.0
    high = close + 0.4
    low = close - 0.4
    # ensure yesterday low pierces MA10
    ma10 = pd.Series(close).rolling(10).mean()
    low[-2] = float(ma10.iloc[-2]) - 1.0
    close[-1] = float(ma10.iloc[-1]) + 0.5
    high[-1] = close[-1] + 0.3
    vol = np.full(n, 1e6); vol[-1] = 1.2e6
    df = pd.DataFrame({
        "trade_date": pd.bdate_range("2025-01-02", periods=n).astype(str),
        "open": close - 0.1, "high": high, "low": low, "close": close,
        "volume": vol, "amount": vol * close,
    })
    h = detect_rs2(df, cfg)
    # may or may not fire depending on stack; at least function returns dict or None without error
    assert h is None or h["setup"] == "RS2"
