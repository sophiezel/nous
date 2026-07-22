#!/usr/bin/env python3
"""Sync hot stock_daily rows into a year partition (no network)."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nous.data.storage.daily_bars import ensure_stock_daily_all_view  # noqa: E402

DB = Path.home() / "nous-data" / "screener.db"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--rebuild-view", action="store_true", default=True)
    args = p.parse_args()

    year = args.year
    start = args.start or f"{year}-01-01"
    end = args.end or f"{year}-12-31"
    tbl = f"stock_daily_{year}"

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {tbl} (
            symbol TEXT NOT NULL, trade_date DATE NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
            PRIMARY KEY (symbol, trade_date)
        )"""
    )

    before = conn.execute(f"SELECT MAX(trade_date), COUNT(*) FROM {tbl}").fetchone()
    cur = conn.execute(
        f"""
        INSERT OR REPLACE INTO {tbl}(symbol, trade_date, open, high, low, close, volume, amount)
        SELECT symbol, trade_date, open, high, low, close, volume, amount
        FROM stock_daily
        WHERE trade_date >= ? AND trade_date <= ?
        """,
        (start, end),
    )
    conn.commit()
    after = conn.execute(f"SELECT MAX(trade_date), COUNT(*), MIN(trade_date) FROM {tbl}").fetchone()
    print(
        f"synced hot→{tbl} [{start}..{end}] rowcount={cur.rowcount} "
        f"before_max={before[0]} rows={before[1]} → after_max={after[0]} rows={after[1]} min={after[2]}"
    )

    if args.rebuild_view:
        sql = ensure_stock_daily_all_view(conn)
        conn.commit()
        print("rebuilt stock_daily_all")
        # sanity: no need to print full SQL
        _ = sql

    # sample overlap check
    hot_max = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
    print(f"hot_max={hot_max} year_max={after[0]}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
