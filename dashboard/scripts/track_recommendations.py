#!/usr/bin/env python3
"""
Track stock recommendations from daily_picks reports.

Reads reports from ~/code/dashboard/data/reports.db (type='daily_picks'),
extracts structured stock picks from markdown content, and stores them
in screener.db rec_performance table for return tracking.

Usage:
    python scripts/track_recommendations.py

Creates/updates:
    screener.db -> rec_performance table
"""

import re
import sqlite3
from pathlib import Path
from datetime import datetime, date

# ── Paths ────────────────────────────────────────────────────────────────────
REPORTS_DB = Path.home() / "code" / "dashboard" / "data" / "reports.db"
SCREENER_DB = Path.home() / "code" / "stock-screener" / "data" / "screener.db"

# ── Regex patterns ──────────────────────────────────────────────────────────

# Parse pick lines like:
#   - **长盛轴承**(300718) — 评分9.0 | PE90.2 | 量比2.0 | RSI66.56
PICK_RE = re.compile(
    r'\*\*(?P<name>[^*]+)\*\*\((?P<symbol>\d+)\)\s*—\s*'
    r'评分(?P<score>[\d.]+)\s*\|\s*'
    r'PE(?P<pe>[\d.]+)\s*\|\s*'
    r'量比(?P<volume_ratio>[\d.]+)\s*\|\s*'
    r'RSI(?P<rsi>[\d.]+)'
)

# Extract date from title: "每日荐股 2026-05-15"
DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')


def extract_date_from_report(title: str, created_at: str) -> str:
    """Extract the recommendation date from the report title or created_at."""
    m = DATE_RE.search(title or '')
    if m:
        return m.group(1)
    # Fall back to the date portion of created_at
    return created_at[:10]


def extract_picks(content: str) -> list[dict]:
    """Extract stock picks from the 量化补遗 section of a report."""
    picks = []
    for m in PICK_RE.finditer(content):
        picks.append({
            'name': m.group('name'),
            'symbol': m.group('symbol'),
            'score': float(m.group('score')),
            'pe': float(m.group('pe')),
            'volume_ratio': float(m.group('volume_ratio')),
            'rsi': float(m.group('rsi')),
        })
    return picks


def get_future_close(cur, symbol: str, base_date: str, offset_days: int):
    """
    Find the close price N trading days AFTER base_date.
    Returns (close_price, trade_date) or (None, None).
    """
    cur.execute("""
        SELECT trade_date, close FROM stock_daily
        WHERE symbol = ? AND trade_date > ?
        ORDER BY trade_date
        LIMIT 1 OFFSET ?
    """, (symbol, base_date, offset_days - 1))
    row = cur.fetchone()
    if row:
        return row[1], row[0]
    return None, None


def get_latest_close(cur, symbol: str):
    """Get the latest available close price for a symbol."""
    cur.execute("""
        SELECT close FROM stock_daily
        WHERE symbol = ?
        ORDER BY trade_date DESC
        LIMIT 1
    """, (symbol,))
    row = cur.fetchone()
    return row[0] if row else None


def create_table(cur):
    """Create rec_performance table if not exists."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rec_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rec_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            rec_score REAL,
            rec_close REAL,
            pe REAL,
            volume_ratio REAL,
            rsi REAL,
            return_1d REAL,
            return_5d REAL,
            return_20d REAL,
            last_close REAL,
            last_return REAL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(rec_date, symbol)
        )
    """)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Track stock recommendations from daily_picks reports")
    parser.add_argument('--dry-run', action='store_true', help="Print what would be done without modifying databases")
    args = parser.parse_args()

    # ── Connect to databases ──────────────────────────────────────────────
    rdb = sqlite3.connect(str(REPORTS_DB))
    rdb.row_factory = sqlite3.Row
    rcur = rdb.cursor()

    sdb = sqlite3.connect(str(SCREENER_DB))
    sdb.row_factory = sqlite3.Row
    scur = sdb.cursor()

    # ── Create table ──────────────────────────────────────────────────────
    if not args.dry_run:
        create_table(scur)
        sdb.commit()
    else:
        print("[DRY-RUN] Would create rec_performance table if not exists")

    # ── Read daily_picks reports ──────────────────────────────────────────
    rcur.execute("""
        SELECT id, type, title, content, created_at
        FROM reports
        WHERE type = 'daily_picks'
        ORDER BY created_at
    """)
    reports = rcur.fetchall()

    if not reports:
        print("No daily_picks reports found.")
        return

    processed = 0
    updated = 0

    for report in reports:
        report_id = report['id']
        title = report['title'] or ''
        content = report['content'] or ''
        created_at = report['created_at']

        rec_date = extract_date_from_report(title, created_at)
        picks = extract_picks(content)

        if not picks:
            print(f"  Report #{report_id} ('{title}'): no picks found in 量化补遗 section")
            continue

        print(f"Report #{report_id}: {title}")
        print(f"  Date: {rec_date}, Picks: {len(picks)}")

        for pick in picks:
            symbol = pick['symbol']
            name = pick['name']
            score = pick['score']
            pe = pick['pe']
            volume_ratio = pick['volume_ratio']
            rsi = pick['rsi']

            # Look up close price on report date
            scur.execute(
                "SELECT close FROM stock_daily WHERE symbol = ? AND trade_date = ?",
                (symbol, rec_date)
            )
            row = scur.fetchone()
            rec_close = row[0] if row else None

            if rec_close is None:
                print(f"    {symbol} {name}: no close on {rec_date} — inserting with rec_close=NULL")
            else:
                print(f"    {symbol} {name}: rec_close={rec_close}")

            if not args.dry_run:
                # Upsert into rec_performance
                scur.execute("""
                    INSERT OR IGNORE INTO rec_performance
                        (rec_date, symbol, name, rec_score, rec_close,
                         pe, volume_ratio, rsi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (rec_date, symbol, name, score, rec_close,
                      pe, volume_ratio, rsi))
                processed += 1

    sdb.commit()

    # ── Update forward returns ────────────────────────────────────────────
    print("\n── Updating forward returns ──")

    scur.execute("""
        SELECT id, rec_date, symbol, rec_close
        FROM rec_performance
        WHERE rec_close IS NOT NULL
        ORDER BY rec_date, symbol
    """)
    records = scur.fetchall()

    for rec in records:
        rec_id = rec['id']
        rec_date = rec['rec_date']
        symbol = rec['symbol']
        rec_close = rec['rec_close']

        # Check if we need to update any NULL return fields
        scur.execute("""
            SELECT return_1d, return_5d, return_20d, last_close, last_return
            FROM rec_performance WHERE id = ?
        """, (rec_id,))
        current = scur.fetchone()

        if current is None:
            continue

        needs_update = (
            current['return_1d'] is None or
            current['return_5d'] is None or
            current['return_20d'] is None or
            current['last_close'] is None or
            current['last_return'] is None
        )

        if not needs_update:
            continue

        # Get forward returns
        return_1d = current['return_1d']
        return_5d = current['return_5d']
        return_20d = current['return_20d']
        last_close = current['last_close']
        last_return = current['last_return']

        # Check if enough time has passed for 1-day return
        if return_1d is None:
            future_close, future_date = get_future_close(scur, symbol, rec_date, 1)
            if future_close is not None:
                return_1d = (future_close - rec_close) / rec_close
                print(f"  {symbol}: return_1d = {return_1d:.4f} (close on {future_date})")

        # 5-day return
        if return_5d is None:
            future_close, future_date = get_future_close(scur, symbol, rec_date, 5)
            if future_close is not None:
                return_5d = (future_close - rec_close) / rec_close
                print(f"  {symbol}: return_5d = {return_5d:.4f} (close on {future_date})")

        # 20-day return
        if return_20d is None:
            future_close, future_date = get_future_close(scur, symbol, rec_date, 20)
            if future_close is not None:
                return_20d = (future_close - rec_close) / rec_close
                print(f"  {symbol}: return_20d = {return_20d:.4f} (close on {future_date})")

        # Latest close and last return
        if last_close is None or last_return is None:
            latest_close = get_latest_close(scur, symbol)
            if latest_close is not None:
                last_close = latest_close
                last_return = (latest_close - rec_close) / rec_close
                print(f"  {symbol}: last_close = {last_close}, last_return = {last_return:.4f}")

        # Update if anything changed
        if not args.dry_run and (
            return_1d != current['return_1d'] or
            return_5d != current['return_5d'] or
            return_20d != current['return_20d'] or
            last_close != current['last_close'] or
            last_return != current['last_return']
        ):
            scur.execute("""
                UPDATE rec_performance
                SET return_1d = ?, return_5d = ?, return_20d = ?,
                    last_close = ?, last_return = ?
                WHERE id = ?
            """, (return_1d, return_5d, return_20d, last_close, last_return, rec_id))
            updated += 1

    sdb.commit()

    # ── Summary ───────────────────────────────────────────────────────────
    scur.execute("SELECT COUNT(*) FROM rec_performance")
    total = scur.fetchone()[0]

    scur.execute("""
        SELECT COUNT(*) FROM rec_performance
        WHERE return_1d IS NOT NULL OR return_5d IS NOT NULL OR return_20d IS NOT NULL
    """)
    with_returns = scur.fetchone()[0]

    print(f"\nSummary: Processed {processed} recommendations, {updated} updated with returns")
    print(f"Total records in rec_performance: {total}, of which {with_returns} have forward returns calculated")

    # Show table contents
    print("\n── rec_performance contents ──")
    scur.execute("""
        SELECT rec_date, symbol, name, rec_score, rec_close,
               return_1d, return_5d, return_20d, last_close, last_return
        FROM rec_performance
        ORDER BY rec_date, symbol
    """)
    for row in scur.fetchall():
        print(f"  {row['rec_date']} | {row['symbol']} {row['name']:>8s} | "
              f"score={row['rec_score']} close={row['rec_close']} | "
              f"r1d={row['return_1d']} r5d={row['return_5d']} r20d={row['return_20d']} | "
              f"last={row['last_close']} last_r={row['last_return']}")

    rdb.close()
    sdb.close()


if __name__ == '__main__':
    main()
