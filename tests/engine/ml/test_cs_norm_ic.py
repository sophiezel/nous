"""Tests for CSZScoreNorm / neutralization / IC gate."""
from __future__ import annotations

import numpy as np
import pandas as pd

from nous.engine.ml.cs_processors import (
    cszscore_norm,
    neutralize_market_cap,
    apply_processors,
    rolling_ic_metrics,
    RANK_IC_PROMOTE_THRESHOLD,
)
from nous.engine.ml.alpha_expand import add_alpha158_subset, add_wq_alpha_subset


def _fake_panel(n_sym: int = 20, n_days: int = 40) -> pd.DataFrame:
    rows = []
    rng = np.random.default_rng(42)
    base = pd.Timestamp("2024-01-02")
    for d in range(n_days):
        date = (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        for s in range(n_sym):
            close = 10 + s + rng.normal(0, 0.5)
            rows.append({
                "symbol": f"S{s:02d}",
                "trade_date": date,
                "open": close * 0.99,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": 1e6 * (1 + s * 0.1),
                "amount": close * 1e6 * (1 + s * 0.1),
                "K1_ret_1d": rng.normal(0, 0.02),
                "K7_mv": 1e9 * (s + 1),
            })
    return pd.DataFrame(rows)


def test_cszscore_norm_zero_mean_unit_std():
    df = _fake_panel()
    out = cszscore_norm(df, ["K1_ret_1d"])
    # On a single date, mean≈0 std≈1
    g = out[out["trade_date"] == out["trade_date"].iloc[0]]["K1_ret_1d"]
    assert abs(g.mean()) < 1e-8
    assert abs(g.std() - 1.0) < 0.15


def test_neutralize_market_cap_runs():
    df = _fake_panel()
    out, flag = neutralize_market_cap(df, ["K1_ret_1d"])
    assert flag == "partial_neutral"
    assert "K1_ret_1d" in out.columns
    assert len(out) == len(df)


def test_apply_processors_meta():
    df = _fake_panel()
    out, meta = apply_processors(df, ["K1_ret_1d"], do_industry=False)
    assert meta["cszscore"] is True
    assert "partial_neutral" in meta["neutralization"]


def test_ic_gate_promote_flag():
    # Perfect rank correlation → promote
    n = 200
    dates = pd.Series([f"D{i//20}" for i in range(n)])
    pred = pd.Series(np.linspace(0, 1, n))
    label = pred.copy()
    m = rolling_ic_metrics(pred, label, dates, window=5)
    assert m["promote"] is True
    assert m["rank_ic"] is not None
    assert m["threshold"] == RANK_IC_PROMOTE_THRESHOLD

    # Noise → may not promote
    rng = np.random.default_rng(0)
    noise = pd.Series(rng.normal(size=n))
    m2 = rolling_ic_metrics(noise, label, dates, window=5)
    assert "promote" in m2


def test_alpha_expand_adds_k9_k10():
    df = _fake_panel(n_sym=5, n_days=80)
    df = add_alpha158_subset(df)
    df = add_wq_alpha_subset(df)
    k10 = [c for c in df.columns if c.startswith("K10_")]
    k9 = [c for c in df.columns if c.startswith("K9_")]
    assert len(k10) >= 10
    assert len(k9) >= 8
