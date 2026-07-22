"""Trading calendar — Qlib CalendarProvider semantics (method transplant).

Lag and freshness are measured in **trading days**, not calendar days.
Sources (in order):
  1. Distinct dates from index_daily (IDX_000001) when DB available
  2. ~/.cache/trading_calendar.json
  3. Weekday fallback (Mon–Fri)
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

MARKET_CLOSE = {
    "a": time(15, 0),
    "hk": time(16, 10),
}

_CACHE_PATH = Path.home() / ".cache" / "trading_calendar.json"


def _weekday_fallback(start: date, end: date) -> list[str]:
    days: list[str] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def _load_cache() -> list[str]:
    if not _CACHE_PATH.exists():
        return []
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return sorted(data.get("days") or [])
    except Exception:
        return []


def _load_from_db(db_path: Optional[Path] = None) -> list[str]:
    try:
        import sqlite3

        path = db_path or (Path.home() / "nous-data" / "screener.db")
        if not path.exists():
            return []
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM index_daily "
                "WHERE symbol IN ('IDX_000001','000001.SH','sh000001') "
                "ORDER BY trade_date"
            ).fetchall()
            if not rows:
                # Prefer partitioned all-view for calendar span; fall back to hot.
                try:
                    rows = conn.execute(
                        "SELECT DISTINCT trade_date FROM stock_daily_all ORDER BY trade_date"
                    ).fetchall()
                except Exception:
                    rows = conn.execute(
                        "SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date"
                    ).fetchall()
            return [r[0] for r in rows if r and r[0]]
        finally:
            conn.close()
    except Exception:
        return []


@lru_cache(maxsize=4)
def get_trading_days(
    start: str | None = None,
    end: str | None = None,
    db_path: str | None = None,
) -> list[str]:
    """Return sorted ISO trading-day strings in [start, end] (inclusive)."""
    today = date.today()
    start_d = date.fromisoformat(start) if start else today - timedelta(days=400)
    end_d = date.fromisoformat(end) if end else today + timedelta(days=30)

    days = _load_from_db(Path(db_path) if db_path else None)
    if not days:
        days = _load_cache()
    if not days:
        days = _weekday_fallback(start_d, end_d)

    return [d for d in days if start_d.isoformat() <= d <= end_d.isoformat()]


def is_trading_day(d: date | str, db_path: str | None = None) -> bool:
    s = d.isoformat() if isinstance(d, date) else d
    days = get_trading_days(
        (date.fromisoformat(s) - timedelta(days=14)).isoformat(),
        (date.fromisoformat(s) + timedelta(days=14)).isoformat(),
        db_path=db_path,
    )
    return s in days


def previous_trading_day(
    as_of: date | str | None = None,
    n: int = 1,
    db_path: str | None = None,
) -> str:
    """Nth trading day on or before as_of (n=1 → last session ≤ as_of)."""
    if as_of is None:
        as_of_d = date.today()
    elif isinstance(as_of, str):
        as_of_d = date.fromisoformat(as_of)
    else:
        as_of_d = as_of

    days = get_trading_days(
        (as_of_d - timedelta(days=400)).isoformat(),
        as_of_d.isoformat(),
        db_path=db_path,
    )
    if not days:
        d = as_of_d
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        for _ in range(n - 1):
            d -= timedelta(days=1)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
        return d.isoformat()

    # days already ≤ as_of
    if n > len(days):
        return days[0]
    return days[-n]


def trading_day_lag(
    latest: date | str | None,
    as_of: date | str | None = None,
    db_path: str | None = None,
) -> int:
    """How many trading sessions as_of is ahead of latest. 0 = same session."""
    if latest is None:
        return 9999
    latest_s = latest if isinstance(latest, str) else latest.isoformat()
    as_of_s = (
        date.today().isoformat()
        if as_of is None
        else (as_of if isinstance(as_of, str) else as_of.isoformat())
    )
    # Expected last session on/before as_of
    expected = previous_trading_day(as_of_s, n=1, db_path=db_path)
    if latest_s >= expected:
        return 0

    days = get_trading_days(latest_s, expected, db_path=db_path)
    # count sessions strictly after latest up to expected
    after = [d for d in days if d > latest_s]
    return len(after)


def clear_cache() -> None:
    get_trading_days.cache_clear()
