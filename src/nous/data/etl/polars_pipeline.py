"""Polars ETL pipeline — accelerates stock data cleaning & storage.

Key components:
  daily_to_polars()    — convert akshare pandas DataFrame → Polars with standard columns
  compute_indicators_batch() — lazy batch indicator computation
  batch_to_sqlite()    — fast bulk write to SQLite via executemany
  clean_and_store()    — single-stock pipeline: pandas → polars → clean → SQLite
"""

import sqlite3
from typing import Optional

import pandas as pd
import polars as pl

# ── column name mapping ──────────────────────────────────────────────
# Sina / akshare may return either Chinese or English column names.
_COLUMN_MAP: dict[str, str] = {
    # Chinese (akshare Sina source)
    "日期": "trade_date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    # English (akshare standard source)
    "date": "trade_date",
    # open / close / high / low / volume / amount — already canonical
}

# Canonical daily column order matching stock_daily table
_DAILY_COLS = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
_DB_COLS = ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"]


# ═══════════════════════════════════════════════════════════════════
#  Conversion
# ═══════════════════════════════════════════════════════════════════

def daily_to_polars(df: pd.DataFrame) -> pl.DataFrame:
    """Convert akshare-returned pandas DataFrame → Polars with standard column names.

    Handles both Chinese (Sina) and English column naming conventions.
    Output columns: trade_date, open, high, low, close, volume, amount.
    """
    if df is None or df.empty:
        return pl.DataFrame(schema=_DAILY_COLS)

    pl_df = pl.from_pandas(df)

    # Build rename mapping for columns that exist in the data
    rename_map = {}
    for col in pl_df.columns:
        mapped = _COLUMN_MAP.get(col)
        if mapped is not None:
            rename_map[col] = mapped

    if rename_map:
        pl_df = pl_df.rename(rename_map)

    # Keep only recognised daily columns (drop extras like `代码`, `名称`, etc.)
    keep = [c for c in _DAILY_COLS if c in pl_df.columns]
    if not keep:
        return pl.DataFrame(schema=_DAILY_COLS)
    pl_df = pl_df.select(keep)

    # Ensure trade_date is a proper date
    if "trade_date" in pl_df.columns:
        pl_df = pl_df.with_columns(
            pl.col("trade_date").cast(pl.Date, strict=False)
        )

    return pl_df


# ═══════════════════════════════════════════════════════════════════
#  Batch indicator computation (lazy)
# ═══════════════════════════════════════════════════════════════════

def compute_indicators_batch(lf: pl.LazyFrame) -> pl.DataFrame:
    """Compute technical indicators using Polars lazy evaluation.

    Calculates batch-wise (per ``symbol`` group):
      - ma5 / ma20         — 5- and 20-day simple moving average
      - pct_chg_1d / 5d / 20d  — 1, 5, 20 period percentage change
      - vol_ma5             — 5-day volume SMA
      - pct_from_60d_high   — how far close is from the 60-day high (%)

    Accepts a LazyFrame with at least columns:
      symbol, trade_date, close, volume
    """
    lf = lf.with_columns([
        pl.col("close").rolling_mean(window_size=5).over("symbol").alias("ma5"),
        pl.col("close").rolling_mean(window_size=20).over("symbol").alias("ma20"),
        (pl.col("close") / pl.col("close").shift(1) - 1.0).over("symbol").alias("pct_chg_1d"),
        (pl.col("close") / pl.col("close").shift(5) - 1.0).over("symbol").alias("pct_chg_5d"),
        (pl.col("close") / pl.col("close").shift(20) - 1.0).over("symbol").alias("pct_chg_20d"),
        pl.col("volume").rolling_mean(window_size=5).over("symbol").alias("vol_ma5"),
        (pl.col("close")
         / pl.col("close").rolling_max(window_size=60).over("symbol")
         * 100.0
        ).alias("pct_from_60d_high"),
    ])
    return lf.collect()


# ═══════════════════════════════════════════════════════════════════
#  SQLite bulk write
# ═══════════════════════════════════════════════════════════════════

def batch_to_sqlite(df: pl.DataFrame, table: str, conn: sqlite3.Connection,
                    chunk_size: int = 500) -> None:
    """Write a Polars DataFrame to a SQLite table via executemany.

    Uses ``INSERT OR REPLACE`` semantics (upsert).
    Splits large DataFrames into chunks to keep memory low.
    """
    if df.is_empty():
        return

    # 设置超时避免 DB lock 错误
    conn.execute("PRAGMA busy_timeout = 10000")

    columns = df.columns
    col_names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"

    for chunk in df.iter_slices(n_rows=chunk_size):
        rows = chunk.rows()
        for attempt in range(3):
            try:
                conn.executemany(sql, rows)
                break
            except sqlite3.OperationalError:
                if attempt == 2: raise
                import time; time.sleep(1)

    conn.commit()


# ═══════════════════════════════════════════════════════════════════
#  Single-stock pipeline
# ═══════════════════════════════════════════════════════════════════

def clean_and_store(raw_df: pd.DataFrame, symbol: str,
                    conn: sqlite3.Connection, keep_all: bool = False) -> int:
    """Single-stock ETL pipeline: pandas → polars → clean → write to SQLite.

    Designed as the drop-in replacement for the inner loop of
    ``update_all_daily()`` — replaces:
        df = fetch_daily(sym, days=120)
        rows = df.to_dict(orient="records")
        rows = [{**r, "symbol": sym} for r in rows]
        storage.upsert_daily(rows)

    Parameters
    ----------
    raw_df : pd.DataFrame
        Raw DataFrame as returned by ``ak.stock_zh_a_daily()`` or
        ``ak.stock_hk_daily()``.
    symbol : str
        Stock symbol (e.g. ``"600519"``).
    conn : sqlite3.Connection
        A shared database connection (avoids per-stock open/close overhead).

    Returns
    -------
    int
        Number of rows written to SQLite.
    """
    if raw_df is None or raw_df.empty:
        return 0

    pl_df = daily_to_polars(raw_df)

    if pl_df.is_empty():
        return 0

    # Add symbol column
    pl_df = pl_df.with_columns(pl.lit(symbol).alias("symbol"))

    # Take the most recent 120 trading days (matching fetch_daily behaviour)
    # Skip truncation when keep_all=True (backfill mode)
    if not keep_all:
        pl_df = pl_df.sort("trade_date", descending=True).head(120)
    else:
        pl_df = pl_df.sort("trade_date", descending=True)

    # Select columns in DB order
    cols = [c for c in _DB_COLS if c in pl_df.columns]
    pl_df = pl_df.select(cols)

    # Write to SQLite
    batch_to_sqlite(pl_df, "stock_daily", conn)

    return len(pl_df)
