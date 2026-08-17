"""因子计算引擎 — pandas(默认,快速) + Polars(可选,大数据)

因子分类 (对标 Alpha158, 精选 ~50个核心因子):
  K1: 动量因子 (5个) — ret_1d/5d/10d/20d/60d
  K2: 反转因子 (2个) — -ret_1d, -ret_5d
  K3: 波动率因子 (5个) — std_5d/10d/20d/60d, vol_ratio
  K4: 成交量因子 (5个) — vol_ratio/ma/chg, vwap
  K5: 量价相关 (1个) — corr_cv_20d
  K6: 技术指标 (8个) — ma_gap_5/20/60, ma_cross, amplitude, price_position
  K7: 基本面因子 (4个) — PE/PB/ROE/市值 (从stock_fundamental)

用法:
  python -m nous.engine.ml.factor_compute save                    # 全量计算+快照
  python -m nous.engine.ml.factor_compute save --limit 500        # 500只测试
  python -m nous.engine.ml.factor_compute save --engine polars    # Polars引擎
"""

from __future__ import annotations

import time
import logging
from pathlib import Path
from typing import Optional
from datetime import date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────
DB_PATH = Path.home() / "nous-data" / "screener.db"
FACTOR_DIR = Path.home() / "nous-data" / "factors"
FACTOR_SNAPSHOT_DIR = FACTOR_DIR / "snapshots"


# ═══════════════════════════════════════════════════════════════════════════
# Pandas engine (default — fast for ≤5M rows)
# ═══════════════════════════════════════════════════════════════════════════

def _compute_factors_pandas(df: pd.DataFrame) -> pd.DataFrame:
    """pandas groupby + rolling — proven fast for <5M rows."""
    g = df.groupby("symbol")

    # K1: 动量
    df["K1_ret_1d"] = g["close"].pct_change(1)
    df["K1_ret_5d"] = g["close"].pct_change(5)
    df["K1_ret_10d"] = g["close"].pct_change(10)
    df["K1_ret_20d"] = g["close"].pct_change(20)
    df["K1_ret_60d"] = g["close"].pct_change(60)

    # K2: 反转
    df["K2_reverse_1d"] = -df["K1_ret_1d"]
    df["K2_reverse_5d"] = -df["K1_ret_5d"]

    # K3: 波动率
    df["K3_std_5d"] = g["K1_ret_1d"].transform(lambda x: x.rolling(5, min_periods=3).std())
    df["K3_std_10d"] = g["K1_ret_1d"].transform(lambda x: x.rolling(10, min_periods=5).std())
    df["K3_std_20d"] = g["K1_ret_1d"].transform(lambda x: x.rolling(20, min_periods=10).std())
    df["K3_std_60d"] = g["K1_ret_1d"].transform(lambda x: x.rolling(60, min_periods=30).std())
    df["K3_vol_ratio"] = df["K3_std_5d"] / df["K3_std_20d"].replace(0, np.nan)

    # K4: 成交量
    df["K4_vol_ma5"] = g["volume"].transform(lambda x: x.rolling(5, min_periods=3).mean())
    df["K4_vol_ma20"] = g["volume"].transform(lambda x: x.rolling(20, min_periods=10).mean())
    df["K4_vol_ratio"] = df["volume"] / df["K4_vol_ma5"].replace(0, np.nan)
    df["K4_vol_chg_5d"] = g["volume"].pct_change(5)
    df["K4_vwap"] = df["amount"] / df["volume"].replace(0, np.nan)

    # K5: 量价相关
    df["K5_corr_cv_20d"] = g.apply(
        lambda x: x["close"].rolling(20, min_periods=10).corr(x["volume"])
    ).reset_index(level=0, drop=True)

    # K6: 技术指标
    df["K6_ma5"] = g["close"].transform(lambda x: x.rolling(5, min_periods=3).mean())
    df["K6_ma20"] = g["close"].transform(lambda x: x.rolling(20, min_periods=10).mean())
    df["K6_ma60"] = g["close"].transform(lambda x: x.rolling(60, min_periods=30).mean())
    df["K6_ma_gap_5"] = (df["close"] - df["K6_ma5"]) / df["K6_ma5"]
    df["K6_ma_gap_20"] = (df["close"] - df["K6_ma20"]) / df["K6_ma20"]
    df["K6_ma_gap_60"] = (df["close"] - df["K6_ma60"]) / df["K6_ma60"]
    df["K6_ma_cross_5_20"] = (df["K6_ma5"] - df["K6_ma20"]) / df["K6_ma20"]
    df["K6_amplitude"] = (df["high"] - df["low"]) / df["open"]
    df["high_20d"] = g["close"].transform(lambda x: x.rolling(20, min_periods=10).max())
    df["low_20d"] = g["close"].transform(lambda x: x.rolling(20, min_periods=10).min())
    df["K6_price_position"] = (df["close"] - df["low_20d"]) / (df["high_20d"] - df["low_20d"]).replace(0, np.nan)

    # K7: 基本面 (merged later in compute_all_factors)
    return df


# ═══════════════════════════════════════════════════════════════════════════
# Polars engine (optional — better for ≥5M rows)
# ═══════════════════════════════════════════════════════════════════════════

def _compute_factors_polars(df_pd: pd.DataFrame) -> pd.DataFrame:
    """Polars lazy evaluation — efficient for large datasets but needs pyarrow."""
    import polars as pl

    df = pl.from_pandas(df_pd)
    lf = df.lazy().sort(["symbol", "trade_date"])

    # K1 + lag columns
    for n in [1, 5, 10, 20, 60]:
        lf = lf.with_columns(
            pl.col("close").shift(n).over("symbol").alias(f"_lag{n}")
        )
    lf = lf.with_columns([
        (pl.col("close") / pl.col("_lag1") - 1).alias("K1_ret_1d"),
        (pl.col("close") / pl.col("_lag5") - 1).alias("K1_ret_5d"),
        (pl.col("close") / pl.col("_lag10") - 1).alias("K1_ret_10d"),
        (pl.col("close") / pl.col("_lag20") - 1).alias("K1_ret_20d"),
        (pl.col("close") / pl.col("_lag60") - 1).alias("K1_ret_60d"),
        (-pl.col("close").pct_change(1).over("symbol")).alias("K2_reverse_1d"),
        (-pl.col("close").pct_change(5).over("symbol")).alias("K2_reverse_5d"),
    ])

    # K3: volatility
    lf = lf.with_columns([
        pl.col("close").pct_change(1).over("symbol")
          .rolling_std(n, min_samples=max(2, n//2)).over("symbol")
          .alias(f"K3_std_{n}d")
        for n in [5, 10, 20, 60]
    ])
    lf = lf.with_columns(
        (pl.col("K3_std_5d") / pl.col("K3_std_20d").replace(0, None)).alias("K3_vol_ratio")
    )

    # K4: volume
    for n in [5, 20]:
        lf = lf.with_columns(
            pl.col("volume").rolling_mean(n, min_samples=max(2, n//2)).over("symbol").alias(f"_vm{n}")
        )
    lf = lf.with_columns([
        (pl.col("volume") / pl.col("_vm5")).alias("K4_vol_ratio"),
        (pl.col("volume").pct_change(5).over("symbol")).alias("K4_vol_chg_5d"),
        (pl.col("amount") / pl.col("volume")).alias("K4_vwap"),
    ])

    # K5
    lf = lf.with_columns(
        pl.rolling_corr("close", "volume", window_size=20, min_samples=10).over("symbol").alias("K5_corr_cv_20d")
    )

    # K6: technicals
    for n in [5, 20, 60]:
        lf = lf.with_columns(
            pl.col("close").rolling_mean(n, min_samples=max(2, n//2)).over("symbol").alias(f"_ma{n}")
        )
    lf = lf.with_columns([
        ((pl.col("close") - pl.col("_ma5")) / pl.col("_ma5")).alias("K6_ma_gap_5"),
        ((pl.col("close") - pl.col("_ma20")) / pl.col("_ma20")).alias("K6_ma_gap_20"),
        ((pl.col("close") - pl.col("_ma60")) / pl.col("_ma60")).alias("K6_ma_gap_60"),
        ((pl.col("_ma5") - pl.col("_ma20")) / pl.col("_ma20")).alias("K6_ma_cross_5_20"),
        ((pl.col("high") - pl.col("low")) / pl.col("open")).alias("K6_amplitude"),
    ])
    # Price position
    lf = lf.with_columns([
        pl.col("close").rolling_max(20, min_samples=10).over("symbol").alias("_h20"),
        pl.col("close").rolling_min(20, min_samples=10).over("symbol").alias("_l20"),
    ]).with_columns(
        ((pl.col("close") - pl.col("_l20")) / (pl.col("_h20") - pl.col("_l20")).replace(0, None))
        .alias("K6_price_position")
    )

    result = lf.collect()
    factor_cols = [c for c in result.columns if c.startswith("K")]
    return result.select(["trade_date", "symbol"] + factor_cols + ["close"]).to_pandas()


# ═══════════════════════════════════════════════════════════════════════════
# Unified API
# ═══════════════════════════════════════════════════════════════════════════

def compute_all_factors(
    start_date: str = "2020-01-01",
    end_date: Optional[str] = None,
    symbols: Optional[list[str]] = None,
    market: str = "a",
    engine: str = "pandas",
) -> pd.DataFrame:
    """全量因子计算。

    Args:
        start_date: 起始日期
        end_date: 结束日期
        symbols: 股票列表(None=全量)
        market: a/hk
        engine: pandas(快速,默认) / polars(大数据优化)

    Returns:
        DataFrame: [trade_date, symbol, K1_ret_1d, ..., close]
    """
    import sqlite3

    t0 = time.time()

    # 1. Load data
    conn = sqlite3.connect(str(DB_PATH))
    from nous.data.storage.daily_bars import daily_relation_sql

    where = [f"b.market = '{market}'"]
    if start_date:
        where.append(f"d.trade_date >= '{start_date}'")
    if end_date:
        where.append(f"d.trade_date <= '{end_date}'")
    if symbols:
        where.append(f"d.symbol IN ({','.join(repr(s) for s in symbols)})")
    where_clause = " AND ".join(where)

    rel = daily_relation_sql(start_date, end_date, conn=conn)
    query = f"""
        SELECT d.symbol, d.trade_date, d.open, d.high, d.low, d.close, d.volume, d.amount
        FROM {rel} d
        JOIN stock_basic b ON d.symbol = b.symbol
        WHERE {where_clause}
        ORDER BY d.symbol, d.trade_date
    """
    logger.info("加载日线数据...")
    df = pd.read_sql_query(query, conn)
    conn.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    n_symbols = df["symbol"].nunique()
    n_dates = df["trade_date"].nunique()
    logger.info(f"加载: {n_symbols}只, {n_dates}天, {len(df)}行 ({time.time()-t0:.1f}s)")

    # 2. Compute factors
    t1 = time.time()
    logger.info(f"计算因子 [{engine}]...")

    if engine == "polars":
        result = _compute_factors_polars(df)
    else:
        result = _compute_factors_pandas(df)

    # Alpha158 subset + WQ formulaic alphas (K9/K10)
    try:
        from nous.engine.ml.alpha_expand import add_alpha158_subset, add_wq_alpha_subset
        result = add_alpha158_subset(result)
        result = add_wq_alpha_subset(result)
        logger.info("Alpha expand: K9/K10 factors attached")
    except Exception as e:
        logger.warning(f"Alpha expand skipped: {e}")

    elapsed = time.time() - t1
    factor_cols = [c for c in result.columns if c.startswith("K")]
    logger.info(f"完成: {len(factor_cols)}因子, {elapsed:.1f}s")

    # 3. Merge fundamentals (K7)
    t2 = time.time()
    conn = sqlite3.connect(str(DB_PATH))
    fund = pd.read_sql_query(
        "SELECT symbol, pe, pb, roe, dividend_yield, total_mv, snapshot_date "
        "FROM stock_fundamental ORDER BY snapshot_date",
        conn
    )
    conn.close()

    if not fund.empty:
        fund = fund.sort_values("snapshot_date").drop_duplicates("symbol", keep="last")
        fund_map = fund.set_index("symbol")
        result["K7_pe"] = result["symbol"].map(fund_map["pe"])
        result["K7_pb"] = result["symbol"].map(fund_map["pb"])
        result["K7_roe"] = result["symbol"].map(fund_map["roe"])
        result["K7_mv"] = result["symbol"].map(fund_map["total_mv"])
        logger.info(f"基本面合并: {len(fund)}只 ({time.time()-t2:.1f}s)")

    # 4. Finalize
    factor_cols = [c for c in result.columns if c.startswith("K")]
    for col in factor_cols:
        result[col] = result[col].fillna(0)

    logger.info(f"总耗时: {time.time()-t0:.1f}s")
    return result


def save_factor_snapshot(
    factors_df: pd.DataFrame,
    snapshot_date: Optional[str] = None,
    market: str = "a",
    *,
    merge_into_latest: bool = False,
) -> str:
    """保存因子快照到 Parquet，同时更新 latest + as_of meta。

    Versioned bundle (Zipline-style):
      snapshots/{date}.parquet
      snapshots/factors_{date}.parquet  (compat)
      latest.parquet + latest.meta.json

    If merge_into_latest=True, replace overlapping (symbol, trade_date) in existing
    latest.parquet and keep older history (daily incremental path).
    """
    import json

    FACTOR_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    FACTOR_DIR.mkdir(parents=True, exist_ok=True)

    if snapshot_date is None:
        if "trade_date" in factors_df.columns:
            snapshot_date = str(pd.to_datetime(factors_df["trade_date"]).max())[:10]
        else:
            snapshot_date = date.today().isoformat()

    prefix = "hk_" if market == "hk" else ""
    out_df = factors_df

    if merge_into_latest and "trade_date" in factors_df.columns and "symbol" in factors_df.columns:
        latest_path = FACTOR_DIR / f"{prefix}latest.parquet"
        if latest_path.exists():
            try:
                old = pd.read_parquet(latest_path)
                old["trade_date"] = pd.to_datetime(old["trade_date"])
                new = factors_df.copy()
                new["trade_date"] = pd.to_datetime(new["trade_date"])
                min_new = new["trade_date"].min()
                kept = old[old["trade_date"] < min_new]
                out_df = pd.concat([kept, new], ignore_index=True)
                out_df = out_df.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
                out_df = out_df.sort_values(["symbol", "trade_date"])
                logger.info(
                    f"增量合并: old={len(old)} + new={len(new)} → {len(out_df)} "
                    f"(cut<{min_new.date()})"
                )
            except Exception as e:
                logger.warning(f"增量合并失败, 覆盖写入: {e}")
                out_df = factors_df

    # Dated snapshot: full merged bundle (compat with assert as_of naming)
    path = FACTOR_SNAPSHOT_DIR / f"{prefix}{snapshot_date}.parquet"
    out_df.to_parquet(path, index=False)
    compat = FACTOR_SNAPSHOT_DIR / f"{prefix}factors_{snapshot_date}.parquet"
    if compat != path:
        out_df.to_parquet(compat, index=False)

    latest_path = FACTOR_DIR / f"{prefix}latest.parquet"
    out_df.to_parquet(latest_path, index=False)
    meta = {
        "as_of": snapshot_date,
        "market": market,
        "n_rows": int(len(out_df)),
        "n_factors": int(len([c for c in out_df.columns if str(c).startswith("K")])),
        "saved_at": date.today().isoformat(),
        "merge": bool(merge_into_latest),
    }
    (FACTOR_DIR / f"{prefix}latest.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    path.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    n_factors = meta["n_factors"]
    logger.info(f"快照: {path} ({len(out_df)}行, {n_factors}因子, as_of={snapshot_date})")
    return str(path)


def daily_factor_update(
    lookback_calendar_days: int = 150,
    market: str = "a",
    engine: str = "pandas",
) -> str:
    """日更：重算近窗因子并合并进 latest（供 Provider DAG S2）。"""
    from datetime import timedelta

    end = date.today()
    start = (end - timedelta(days=lookback_calendar_days)).isoformat()
    logger.info(f"因子日更: market={market} start={start} lookback={lookback_calendar_days}d")
    df = compute_all_factors(start_date=start, end_date=None, market=market, engine=engine)
    return save_factor_snapshot(df, market=market, merge_into_latest=True)


def compute_ic(factors_df: pd.DataFrame, forward_returns: pd.Series) -> dict:
    """计算每个因子的 Rank IC。"""
    from scipy.stats import spearmanr

    results = {}
    factor_names = [c for c in factors_df.columns if c.startswith("K")]

    for factor in factor_names:
        valid = factors_df[factor].notna() & forward_returns.notna()
        if valid.sum() < 30:
            continue
        ic = np.corrcoef(factors_df.loc[valid, factor], forward_returns.loc[valid])[0, 1]
        rank_ic, _ = spearmanr(factors_df.loc[valid, factor], forward_returns.loc[valid])
        results[factor] = {
            "ic": round(float(ic), 4),
            "rank_ic": round(float(rank_ic), 4),
            "n_samples": int(valid.sum()),
        }
    return results


def _build_gplearn_features(df: pd.DataFrame) -> pd.DataFrame | None:
    """为 gplearn 构建基础特征矩阵 (保留兼容)."""
    base_cols = [
        'K1_ret_1d', 'K1_ret_5d', 'K1_ret_10d', 'K1_ret_20d',
        'K3_std_5d', 'K3_std_10d', 'K3_std_20d',
        'K4_vol_ratio', 'K4_vwap',
        'K5_corr_cv_20d',
        'K6_ma_gap_5', 'K6_ma_gap_20', 'K6_amplitude',
    ]
    available = [c for c in base_cols if c in df.columns]
    if not available:
        return None
    feats = df[available].fillna(0).copy()
    for col in feats.columns:
        lower, upper = np.percentile(feats[col].values, [0.5, 99.5])
        feats[col] = np.clip(feats[col].values, lower, upper)
    return feats


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    import argparse
    parser = argparse.ArgumentParser(description="因子计算引擎")
    parser.add_argument("action", choices=["compute", "save", "daily"], default="compute", nargs="?")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--limit", type=int, default=0, help="限制股票数(0=全量)")
    parser.add_argument("--engine", choices=["pandas", "polars"], default="pandas",
                        help="pandas=快速(默认) / polars=大数据优化")
    parser.add_argument("--market", default="a")
    parser.add_argument("--lookback", type=int, default=150, help="daily 模式日历回看天数")
    args = parser.parse_args()

    if args.action == "daily":
        path = daily_factor_update(
            lookback_calendar_days=args.lookback,
            market=args.market,
            engine=args.engine,
        )
        print(f"Saved: {path}")
        raise SystemExit(0)

    # Resolve symbols
    symbols = None
    if args.limit > 0:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        latest = conn.execute(
            "SELECT trade_date FROM stock_daily_all d JOIN stock_basic b ON d.symbol=b.symbol "
            "WHERE b.market=? GROUP BY trade_date HAVING COUNT(*) > 100 "
            "ORDER BY trade_date DESC LIMIT 1", (args.market,)
        ).fetchone()
        if latest:
            top = [r[0] for r in conn.execute(
                "SELECT d.symbol FROM stock_daily_all d JOIN stock_basic b ON d.symbol=b.symbol "
                "WHERE b.market=? AND d.trade_date=? ORDER BY d.amount DESC LIMIT ?",
                (args.market, latest[0], args.limit)
            ).fetchall()]
            symbols = top
            logger.info(f"限制{args.limit}只: {len(top)}只 (基准日{latest[0]})")
        conn.close()

    df = compute_all_factors(
        start_date=args.start, end_date=args.end,
        symbols=symbols, market=args.market, engine=args.engine,
    )

    if args.action == "save":
        path = save_factor_snapshot(df, market=args.market)
        print(f"Saved: {path}")
