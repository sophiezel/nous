"""Partitioned A-share daily bar routing (hot + year tables).

Architecture:
  - ``stock_daily``          hot / ingest target (freshness assert)
  - ``stock_daily_YYYY``     year partitions (history authority)
  - ``stock_daily_all``      view = years UNION + hot tail beyond year max

Prefer ``daily_relation_sql(start, end)`` for ranged queries to avoid
scanning the full multi-year UNION when the window is known.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Optional

YEAR_TABLE_MIN = 2009
HOT_TABLE = "stock_daily"
STOCK_DAILY_ALL = "stock_daily_all"
DAILY_COLUMNS = "symbol, trade_date, open, high, low, close, volume, amount"


def _existing_year_tables(conn: Optional[sqlite3.Connection] = None) -> list[int]:
    """Return sorted years that have a stock_daily_YYYY table."""
    years: list[int] = []
    own = False
    if conn is None:
        from nous.data.storage import get_db

        conn = get_db(write=False)
        own = True
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'stock_daily_[0-9][0-9][0-9][0-9]'"
        ).fetchall()
        for (name,) in rows:
            try:
                y = int(name.rsplit("_", 1)[-1])
            except ValueError:
                continue
            if YEAR_TABLE_MIN <= y <= 2100:
                years.append(y)
        return sorted(set(years))
    finally:
        if own:
            conn.close()


def daily_table_for(trade_date: str, *, for_write: bool = False) -> str:
    """Route a single trade_date to the physical table.

    - Writes for the rolling hot window stay on ``stock_daily`` when
      ``for_write=True`` (ingest path).
    - Historical reads use ``stock_daily_YYYY`` when the year partition exists;
      otherwise fall back to hot.
    """
    y = int(str(trade_date)[:4])
    if for_write:
        # Ingest always lands on hot; nightly/ops may sync into year tables.
        return HOT_TABLE
    # Prefer year partition for any calendar year that has a table.
    # Callers that need "latest bar for freshness" should use HOT_TABLE explicitly.
    return f"stock_daily_{y}"


def _years_in_range(start: str | None, end: str | None, available: list[int]) -> list[int]:
    if not available:
        return []
    y0 = int(start[:4]) if start else available[0]
    y1 = int(end[:4]) if end else available[-1]
    return [y for y in available if y0 <= y <= y1]


def _need_hot_tail(end: str | None, year_max: Optional[str]) -> bool:
    """Include hot table when the query window may extend past year partitions."""
    if end is None:
        return True
    if not year_max:
        return True
    return str(end) > str(year_max)


def year_partition_max_date(conn: sqlite3.Connection, year: Optional[int] = None) -> Optional[str]:
    """MAX(trade_date) for a year table (default: latest year partition present)."""
    years = _existing_year_tables(conn)
    if not years:
        return None
    y = year if year is not None else max(years)
    tbl = f"stock_daily_{y}"
    try:
        row = conn.execute(f"SELECT MAX(trade_date) FROM {tbl}").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def daily_relation_sql(
    start: str | None = None,
    end: str | None = None,
    *,
    include_hot: bool | None = None,
    conn: Optional[sqlite3.Connection] = None,
) -> str:
    """SQL relation (bare table name or parenthesized UNION) for daily OHLCV.

    Example::

        rel = daily_relation_sql("2015-01-01", "2024-12-31")
        cur.execute(f"SELECT * FROM {rel} WHERE symbol=?", (sym,))
    """
    own = False
    if conn is None:
        from nous.data.storage import get_db

        conn = get_db(write=False)
        own = True
    try:
        available = _existing_year_tables(conn)
        years = _years_in_range(start, end, available)
        year_max = year_partition_max_date(conn) if available else None
        use_hot = include_hot if include_hot is not None else _need_hot_tail(end, year_max)

        parts: list[str] = [
            f"SELECT {DAILY_COLUMNS} FROM stock_daily_{y}" for y in years
        ]
        if use_hot:
            if year_max:
                parts.append(
                    f"SELECT {DAILY_COLUMNS} FROM {HOT_TABLE} "
                    f"WHERE trade_date > '{year_max}'"
                )
            else:
                parts.append(f"SELECT {DAILY_COLUMNS} FROM {HOT_TABLE}")

        if not parts:
            return HOT_TABLE
        if len(parts) == 1 and parts[0].startswith("SELECT") and " WHERE " not in parts[0]:
            # single year table, no filter
            return f"stock_daily_{years[0]}"
        if len(parts) == 1 and not parts[0].startswith("SELECT"):
            return parts[0]
        if len(years) == 1 and not use_hot:
            return f"stock_daily_{years[0]}"
        return "(" + " UNION ALL ".join(parts) + ")"
    finally:
        if own:
            conn.close()


def daily_from_clause(
    start: str | None = None,
    end: str | None = None,
    alias: str = "d",
    **kwargs,
) -> str:
    """``FROM <relation> AS alias`` fragment."""
    rel = daily_relation_sql(start, end, **kwargs)
    return f"FROM {rel} AS {alias}"


def latest_trade_date_for_freshness(conn: sqlite3.Connection) -> Optional[str]:
    """Freshness / assert: prefer hot table max (ingest surface)."""
    try:
        row = conn.execute(f"SELECT MAX(trade_date) FROM {HOT_TABLE}").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def ensure_stock_daily_all_view(conn: sqlite3.Connection) -> str:
    """Rebuild ``stock_daily_all``: year tables 2009+ UNION ALL + hot tail.

    Safe when early year tables are empty/thin. Dedupes hot vs year overlap by
    only attaching hot rows after MAX(latest year partition).
    """
    years = _existing_year_tables(conn)
    if not years:
        years = list(range(YEAR_TABLE_MIN, date.today().year + 1))

    year_parts = [
        f"SELECT {DAILY_COLUMNS} FROM stock_daily_{y}" for y in years
    ]
    latest_year = max(years)
    # Hot tail beyond the latest year partition's max date (correlated subquery
    # keeps the view correct as sync progresses).
    hot_part = (
        f"SELECT {DAILY_COLUMNS} FROM {HOT_TABLE} "
        f"WHERE trade_date > IFNULL((SELECT MAX(trade_date) FROM stock_daily_{latest_year}), '0000-01-01')"
    )
    body = "\n  UNION ALL ".join(year_parts + [hot_part])
    sql = f"CREATE VIEW stock_daily_all AS\n  {body}"
    conn.execute("DROP VIEW IF EXISTS stock_daily_all")
    conn.execute(sql)
    return sql


def approx_start_for_lookback(as_of: str, lookback_days: int = 400) -> str:
    """Calendar approximation for lookback windows (trading days ≈ calendar*1.5)."""
    d = date.fromisoformat(str(as_of)[:10]) - timedelta(days=int(lookback_days * 1.6) + 14)
    floor = date(YEAR_TABLE_MIN, 1, 1)
    if d < floor:
        d = floor
    return d.isoformat()
