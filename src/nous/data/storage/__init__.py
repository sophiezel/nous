"""Storage compatibility layer — bridges legacy storage.py API to nous.core.db.

All existing modules import ``from nous.data.storage import get_db``,
``connect_readonly``, or ``with_retry``. This module re-exports from
``nous.core.db`` with the same API for backward compatibility.

The Write Proxy daemon, SCHEMA management, and cold/hot table routing
from the original ``storage.py`` are preserved here.
"""

from __future__ import annotations

import functools
import sqlite3
import time
from pathlib import Path
from typing import Callable

# Re-export core functions with compatibility wrappers
from nous.core.db import _connect, _resolve_path, get_db as _core_get_db


# ── Schema (mirrors original storage.py) ────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_basic (
    symbol TEXT PRIMARY KEY,
    name   TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market IN ('a','hk')),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_daily (
    symbol    TEXT NOT NULL,
    trade_date DATE NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    REAL,
    amount    REAL,
    PRIMARY KEY (symbol, trade_date),
    FOREIGN KEY (symbol) REFERENCES stock_basic(symbol)
);

CREATE TABLE IF NOT EXISTS stock_fundamental (
    symbol          TEXT PRIMARY KEY,
    pe              REAL,
    pb              REAL,
    roe             REAL,
    dividend_yield  REAL,
    debt_ratio      REAL,
    total_mv        REAL,
    pe_dynamic      REAL,
    snapshot_date   DATE,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (symbol) REFERENCES stock_basic(symbol)
);

CREATE TABLE IF NOT EXISTS screen_results (
    symbol     TEXT NOT NULL,
    trade_date DATE NOT NULL,
    engine     TEXT NOT NULL,
    score      REAL,
    rank       INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trade_date, engine)
);

CREATE TABLE IF NOT EXISTS lhb_daily (
    trade_date   DATE NOT NULL,
    symbol       TEXT NOT NULL,
    name         TEXT,
    reason       TEXT,
    buy_amount   REAL,
    sell_amount  REAL,
    net_amount   REAL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, symbol)
);

CREATE TABLE IF NOT EXISTS index_daily (
    symbol     TEXT NOT NULL,
    trade_date DATE NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    volume     REAL,
    amount     REAL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_daily_unique ON stock_daily(symbol, trade_date);
CREATE INDEX IF NOT EXISTS idx_stock_daily_date ON stock_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_screen_results_date ON screen_results(trade_date);
CREATE INDEX IF NOT EXISTS idx_screen_results_engine ON screen_results(engine);
CREATE INDEX IF NOT EXISTS idx_index_daily_date ON index_daily(trade_date);
"""


# ── Compatibility Functions ─────────────────────────────────────────────

def get_db(write: bool = False) -> sqlite3.Connection:
    """Get a SQLite connection — compatible with legacy storage.py API.

    Unlike core.db.get_db(), this:
    - Always connects to ``screener.db``
    - Executes SCHEMA on write connections (auto-creates tables)
    - Sets row_factory = sqlite3.Row

    Args:
        write: If True, opens with write timeout and runs SCHEMA.

    Returns:
        A configured sqlite3.Connection.
    """
    from nous.core.db import _resolve_path

    path = _resolve_path("screener.db")
    if not write and not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found: {path}. Run: nous data bootstrap"
        )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)

    if write:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            conn.executescript(SCHEMA)
        except sqlite3.OperationalError:
            pass  # May fail if another connection is mid-write
    else:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")

    conn.row_factory = sqlite3.Row
    return conn


def connect_readonly() -> sqlite3.Connection:
    """Open a read-only connection (Dashboard-compatible).

    Returns a connection with query_only=ON for safety.
    """
    from nous.core.db import _resolve_path

    path = _resolve_path("screener.db")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_daily_range(symbol: str, start_date: str, end_date: str) -> list:
    """Fetch daily price data for a symbol between two dates.

    Used by stress_test.py and risk_decomp.py for historical analysis.
    Returns list of sqlite3.Row objects. Reads partitioned year tables + hot tail.
    """
    from nous.data.storage.daily_bars import daily_relation_sql

    conn = get_db(write=False)
    try:
        rel = daily_relation_sql(start_date, end_date, conn=conn)
        rows = conn.execute(
            f"SELECT * FROM {rel} WHERE symbol=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (symbol, start_date, end_date),
        ).fetchall()
        return rows
    finally:
        conn.close()


# Re-export partitioned daily helpers
from nous.data.storage.daily_bars import (  # noqa: E402
    STOCK_DAILY_ALL,
    approx_start_for_lookback,
    daily_from_clause,
    daily_relation_sql,
    daily_table_for,
    ensure_stock_daily_all_view,
    latest_trade_date_for_freshness,
)


def with_retry(max_attempts: int = 3, delay: float = 2.0) -> Callable:
    """Decorator: retry a function on DB errors with exponential backoff.

    Usage::

        @with_retry(max_attempts=3, delay=2.0)
        def my_db_operation():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        wait = delay * (2 ** attempt)
                        time.sleep(wait)
            raise last_exc  # type: ignore

        return wrapper

    return decorator
