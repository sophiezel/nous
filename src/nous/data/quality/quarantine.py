"""Symbol quarantine — TTL blacklist for cross-validation / data-quality failures.

Shared by recommendation pipeline and (optionally) backtest universe filters.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Iterable


DDL = """
CREATE TABLE IF NOT EXISTS symbol_quarantine (
    symbol TEXT NOT NULL,
    reason TEXT,
    severity TEXT DEFAULT 'error',
    quarantined_on TEXT NOT NULL,
    expires_on TEXT NOT NULL,
    PRIMARY KEY (symbol, quarantined_on)
);
CREATE INDEX IF NOT EXISTS idx_quarantine_expires ON symbol_quarantine(expires_on);
"""

DEFAULT_TTL_DAYS = 5


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)


def quarantine_symbols(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
    reason: str,
    severity: str = "error",
    as_of: str | None = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> int:
    """Insert/refresh quarantine rows. Returns number of symbols written."""
    ensure_schema(conn)
    as_of = as_of or date.today().isoformat()
    expires = (date.fromisoformat(as_of) + timedelta(days=ttl_days)).isoformat()
    n = 0
    for sym in symbols:
        if not sym:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO symbol_quarantine
               (symbol, reason, severity, quarantined_on, expires_on)
               VALUES (?,?,?,?,?)""",
            (sym, reason, severity, as_of, expires),
        )
        n += 1
    conn.commit()
    return n


def get_quarantined(
    conn: sqlite3.Connection,
    as_of: str | None = None,
) -> set[str]:
    """Active quarantine set as of date (defaults to today)."""
    ensure_schema(conn)
    as_of = as_of or date.today().isoformat()
    rows = conn.execute(
        """SELECT DISTINCT symbol FROM symbol_quarantine
           WHERE expires_on >= ? AND quarantined_on <= ?""",
        (as_of, as_of),
    ).fetchall()
    return {r[0] for r in rows}


def is_quarantined(conn: sqlite3.Connection, symbol: str, as_of: str | None = None) -> bool:
    return symbol in get_quarantined(conn, as_of)
