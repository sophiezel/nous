"""Cross-sectional processors — Qlib-style CSZScoreNorm + neutralization."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

RANK_IC_PROMOTE_THRESHOLD = 0.02


def winsorize_cs(s: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    if s.notna().sum() < 5:
        return s
    lo, hi = s.quantile([lower, upper])
    return s.clip(lo, hi)


def cszscore_norm(
    df: pd.DataFrame,
    factor_cols: list[str],
    date_col: str = "trade_date",
) -> pd.DataFrame:
    """Per-date cross-sectional winsorize → z-score for each factor column."""
    out = df.copy()
    for col in factor_cols:
        if col not in out.columns:
            continue

        def _z(g: pd.Series) -> pd.Series:
            w = winsorize_cs(g)
            mu = w.mean()
            sd = w.std()
            if sd is None or not np.isfinite(sd) or sd == 0:
                return pd.Series(0.0, index=g.index)
            return (w - mu) / sd

        out[col] = out.groupby(date_col, group_keys=False)[col].transform(_z)
    return out


def neutralize_market_cap(
    df: pd.DataFrame,
    factor_cols: list[str],
    mv_col: str = "K7_mv",
    date_col: str = "trade_date",
) -> tuple[pd.DataFrame, str]:
    """Residualize factors against log(market_cap) within each date."""
    out = df.copy()
    if mv_col not in out.columns:
        return out, "partial_neutral"

    out["_log_mv"] = np.log(out[mv_col].replace(0, np.nan).abs())

    for col in factor_cols:
        if col not in out.columns or col == mv_col:
            continue
        resid_parts = []
        for _, g in out.groupby(date_col):
            y = g[col]
            x = g["_log_mv"]
            valid = y.notna() & x.notna()
            if valid.sum() < 10:
                resid_parts.append(y)
                continue
            xv = x[valid].values.astype(float)
            yv = y[valid].values.astype(float)
            var_x = np.var(xv)
            if var_x < 1e-18:
                resid_parts.append(y)
                continue
            b = np.cov(xv, yv)[0, 1] / var_x
            a = yv.mean() - b * xv.mean()
            resid = y.copy()
            resid.loc[valid] = yv - (a + b * xv)
            resid_parts.append(resid)
        out[col] = pd.concat(resid_parts).sort_index()

    out = out.drop(columns=["_log_mv"], errors="ignore")
    return out, "partial_neutral"


def neutralize_industry(
    df: pd.DataFrame,
    factor_cols: list[str],
    industry_col: str = "industry",
    date_col: str = "trade_date",
) -> tuple[pd.DataFrame, bool]:
    if industry_col not in df.columns:
        return df, False
    out = df.copy()
    for col in factor_cols:
        if col not in out.columns:
            continue
        out[col] = out[col] - out.groupby([date_col, industry_col])[col].transform("mean")
    return out, True


def apply_processors(
    df: pd.DataFrame,
    factor_cols: list[str] | None = None,
    do_industry: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Neutralize → CSZScore. Returns (df, meta)."""
    factor_cols = factor_cols or [c for c in df.columns if c.startswith("K")]
    meta: dict = {"neutralization": [], "n_factors": len(factor_cols)}

    out, flag = neutralize_market_cap(df, factor_cols)
    meta["neutralization"].append(flag)

    if do_industry:
        out, applied = neutralize_industry(out, factor_cols)
        if applied:
            meta["neutralization"].append("industry")

    out = cszscore_norm(out, factor_cols)
    meta["cszscore"] = True
    return out, meta


def rolling_ic_metrics(
    pred: pd.Series,
    label: pd.Series,
    dates: pd.Series,
    window: int = 20,
) -> dict:
    """IC / RankIC / ICIR + promote gate."""
    from scipy.stats import spearmanr, pearsonr

    frame = pd.DataFrame({"pred": pred, "label": label, "date": dates}).dropna()
    if len(frame) < 30:
        return {"ic": None, "rank_ic": None, "icir": None, "promote": False}

    daily = []
    for _, g in frame.groupby("date"):
        if len(g) < 5:
            continue
        try:
            ic, _ = pearsonr(g["pred"], g["label"])
            ric, _ = spearmanr(g["pred"], g["label"])
            if np.isfinite(ic) and np.isfinite(ric):
                daily.append({"ic": ic, "rank_ic": ric})
        except Exception:
            continue

    if not daily:
        return {"ic": None, "rank_ic": None, "icir": None, "promote": False}

    ics = pd.DataFrame(daily)
    mean_ic = float(ics["ic"].mean())
    mean_ric = float(ics["rank_ic"].mean())
    ic_std = float(ics["ic"].std()) + 1e-12
    icir = float(mean_ic / ic_std * np.sqrt(252 / max(window, 1)))
    recent_ric = float(ics["rank_ic"].tail(window).mean())
    promote = recent_ric >= RANK_IC_PROMOTE_THRESHOLD
    return {
        "ic": round(mean_ic, 4),
        "rank_ic": round(mean_ric, 4),
        "icir": round(icir, 4),
        "recent_rank_ic": round(recent_ric, 4),
        "promote": promote,
        "threshold": RANK_IC_PROMOTE_THRESHOLD,
    }


def cs_rank_label(
    df: pd.DataFrame,
    ret_col: str = "forward_ret",
    date_col: str = "trade_date",
) -> pd.Series:
    """Cross-sectional percentile rank label."""
    return df.groupby(date_col)[ret_col].rank(pct=True)
