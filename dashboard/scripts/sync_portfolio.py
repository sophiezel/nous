#!/usr/bin/env python3
"""
sync_portfolio.py - Sync portfolio from state.yaml to reports.db.

Input: ~/wiki/finance/portfolio/state.yaml
Output: reports.db tables live_portfolio + portfolio_nav
"""

import sqlite3
import sys
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

REPORTS_DB = Path.home() / "code/dashboard/data/reports.db"
SCREENER_DB = Path.home() / "code/stock-screener/data/screener.db"
STATE_YAML = Path.home() / "wiki/finance/portfolio/state.yaml"

CREATE_LIVE_PORTFOLIO = """
CREATE TABLE IF NOT EXISTS live_portfolio (
    symbol TEXT,
    name TEXT,
    market TEXT,
    sector TEXT,
    pnl_pct REAL,
    weight_pct REAL,
    hold_days INTEGER,
    tier TEXT,
    account TEXT,
    update_date TEXT
);
"""

# Note: portfolio_nav already exists in screener.db with columns:
# trade_date, nav, daily_return, cash, total_asset, created_at
# We need to add portfolio_type and pnl_pct if missing
PORTFOLIO_NAV_INSERT = """
INSERT OR IGNORE INTO portfolio_nav
(trade_date, nav, daily_return, cash, total_asset, portfolio_type, pnl_pct)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def ensure_portfolio_nav_columns(conn: sqlite3.Connection):
    """Ensure portfolio_nav has the required columns, adding them if missing."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(portfolio_nav)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    
    needed_cols = {
        "portfolio_type": "TEXT DEFAULT 'live'",
        "pnl_pct": "REAL",
    }
    
    for col_name, col_def in needed_cols.items():
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE portfolio_nav ADD COLUMN {col_name} {col_def}")
                print(f"  Added column '{col_name}' to portfolio_nav")
            except Exception as e:
                print(f"  Note: Could not add column '{col_name}': {e}")


def main():
    print("=" * 60)
    print("sync_portfolio.py - Sync portfolio to reports.db")
    print("=" * 60)
    
    # Check state.yaml exists
    if not STATE_YAML.exists():
        print(f"ERROR: State file not found: {STATE_YAML}")
        sys.exit(1)
    
    # Load YAML
    print(f"\nLoading: {STATE_YAML}")
    with open(STATE_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    today = date.today().isoformat()
    
    # --- Extract holdings ---
    holdings = []
    
    # Accounts holdings
    accounts = data.get("accounts", {})
    for account_name, account_data in accounts.items():
        for holding in account_data.get("holdings", []):
            h = {
                "symbol": str(holding.get("symbol", "")),
                "name": holding.get("name", ""),
                "market": holding.get("market", ""),
                "sector": holding.get("sector", ""),
                "pnl_pct": holding.get("pnl_pct", 0.0) or 0.0,
                "weight_pct": holding.get("weight_pct", 0.0) or 0.0,
                "hold_days": holding.get("hold_days"),
                "tier": holding.get("tier", ""),
                "account": account_name,
            }
            holdings.append(h)
            print(f"  Holding: {h['symbol']} {h['name']} ({account_name})")
    
    # Funds holdings
    funds_data = data.get("funds", {})
    for holding in funds_data.get("holdings", []):
        h = {
            "symbol": str(holding.get("symbol", "")),
            "name": holding.get("name", ""),
            "market": "fund",
            "sector": holding.get("style", ""),
            "pnl_pct": holding.get("pnl_pct", 0.0) or 0.0,
            "weight_pct": holding.get("weight_pct", 0.0) or 0.0,
            "hold_days": None,
            "tier": "fund",
            "account": "funds",
        }
        holdings.append(h)
        print(f"  Fund: {h['symbol']} {h['name']}")
    
    if not holdings:
        print("No holdings found in state.yaml")
        sys.exit(1)
    
    # --- Compute aggregate P&L ---
    total_weight = sum(h["weight_pct"] for h in holdings)
    weighted_pnl_sum = sum(h["pnl_pct"] * h["weight_pct"] for h in holdings)
    
    if total_weight > 0:
        aggregate_pnl_pct = weighted_pnl_sum / total_weight
    else:
        aggregate_pnl_pct = 0.0
    
    print(f"\n  Total holdings: {len(holdings)}")
    print(f"  Total weight: {total_weight:.1f}%")
    print(f"  Aggregate P&L (weighted): {aggregate_pnl_pct:.2f}%")
    
    # --- Write to reports.db (live_portfolio) ---
    print(f"\nWriting to reports.db: {REPORTS_DB}")
    conn1 = sqlite3.connect(str(REPORTS_DB))
    cursor1 = conn1.cursor()
    
    cursor1.execute(CREATE_LIVE_PORTFOLIO)
    
    # Clear previous records
    cursor1.execute("DELETE FROM live_portfolio")
    print("  Cleared live_portfolio table")
    
    # Insert all holdings
    insert_portfolio = """
        INSERT INTO live_portfolio
        (symbol, name, market, sector, pnl_pct, weight_pct, hold_days, tier, account, update_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    inserted = 0
    for h in holdings:
        cursor1.execute(insert_portfolio, (
            h["symbol"],
            h["name"],
            h["market"],
            h["sector"],
            h["pnl_pct"],
            h["weight_pct"],
            h["hold_days"],
            h["tier"],
            h["account"],
            today,
        ))
        inserted += 1
    
    conn1.commit()
    conn1.close()
    print(f"  Inserted {inserted} records into live_portfolio")
    
    # --- Write to screener.db (portfolio_nav) ---
    print(f"\nWriting to screener.db: {SCREENER_DB}")
    conn2 = sqlite3.connect(str(SCREENER_DB))
    ensure_portfolio_nav_columns(conn2)
    
    # Compute nav approximation (normalized to 1.0 with pnl applied)
    # Using 1.0 as base nav, apply aggregate P&L
    base_nav = 1.0
    nav = base_nav * (1 + aggregate_pnl_pct / 100)
    daily_return = 0.0  # We don't have daily return data
    
    cursor2 = conn2.cursor()
    cursor2.execute(PORTFOLIO_NAV_INSERT, (
        today,
        round(nav, 4),
        daily_return,
        0.0,         # cash - unknown
        0.0,         # total_asset - unknown
        "live",
        round(aggregate_pnl_pct, 2),
    ))
    conn2.commit()
    
    # Verify
    cursor2.execute("SELECT COUNT(*) FROM portfolio_nav WHERE trade_date = ?", (today,))
    count = cursor2.fetchone()[0]
    conn2.close()
    
    print(f"  Inserted/verified portfolio_nav for {today} (rows: {count})")
    
    print(f"\n{'=' * 60}")
    print(f"Summary:")
    print(f"  State file: {STATE_YAML.name}")
    print(f"  Holdings synced: {inserted}")
    print(f"  Portfolio NAV: {nav:.4f} (pnl: {aggregate_pnl_pct:.2f}%)")
    print(f"  Trade date: {today}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
