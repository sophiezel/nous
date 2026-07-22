#!/usr/bin/env python3
"""
sync_risk_report.py - Sync latest risk report JSON to screener.db.

Input: ~/code/stock-screener/data/reports/risk_report_*.json (latest)
Output: screener.db table risk_metrics
"""

import json
import sqlite3
import sys
from pathlib import Path

SCREENER_DB = Path.home() / "code/stock-screener/data/screener.db"
REPORTS_DIR = Path.home() / "code/stock-screener/data/reports"

CREATE_RISK_METRICS = """
CREATE TABLE IF NOT EXISTS risk_metrics (
    trade_date TEXT PRIMARY KEY,
    var_95 REAL,
    var_99 REAL,
    cvar_95 REAL,
    max_drawdown REAL,
    sector_concentration REAL,
    factor_exposures TEXT,
    stress_tests TEXT,
    alerts TEXT
);
"""


def find_latest_risk_report() -> Path:
    """Find the latest risk_report_*.json file."""
    json_files = sorted(REPORTS_DIR.glob("risk_report_*.json"))
    if not json_files:
        return None
    return json_files[-1]  # Latest by filename sort (ISO dates)


def extract_alerts(data: dict) -> list:
    """Extract alerts from the report data."""
    alerts = []
    
    # Factor alerts
    factor_alerts = data.get("factor_alerts", [])
    if factor_alerts:
        for a in factor_alerts:
            alerts.append(str(a))
    
    # Sector concentration alerts
    sector_data = data.get("sector_concentration", {})
    sector_alerts = sector_data.get("alerts", [])
    if sector_alerts:
        for a in sector_alerts:
            alerts.append(str(a))
    
    # Liquidity alerts
    liquidity_alerts = data.get("liquidity_alerts", [])
    if liquidity_alerts:
        for a in liquidity_alerts:
            alerts.append(str(a))
    
    return alerts


def main():
    print("=" * 60)
    print("sync_risk_report.py - Sync risk report to screener.db")
    print("=" * 60)
    
    # Find latest risk report
    report_path = find_latest_risk_report()
    if not report_path:
        print("No risk_report_*.json files found in:", REPORTS_DIR)
        print("Skipping. This is expected if no risk report exists for today.")
        sys.exit(0)
    
    print(f"\nFound: {report_path.name}")
    
    # Read JSON
    with open(report_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Extract fields
    report_date = data.get("report_date", "")
    if not report_date:
        print("ERROR: No report_date in JSON")
        sys.exit(1)
    
    var_data = data.get("var", {})
    var_95 = var_data.get("var_95")
    var_99 = var_data.get("var_99")
    cvar_95 = var_data.get("cvar_95")
    
    mdd_data = var_data.get("mdd", {})
    max_drawdown = mdd_data.get("mdd") if isinstance(mdd_data, dict) else None
    
    sector_data = data.get("sector_concentration", {})
    sector_concentration = sector_data.get("max_sector_pct") if isinstance(sector_data, dict) else None
    
    factor_exposures = data.get("factor_exposures", {})
    stress_tests = data.get("stress_tests", {})
    alerts = extract_alerts(data)
    
    print(f"  Report date: {report_date}")
    print(f"  VaR 95: {var_95}")
    print(f"  VaR 99: {var_99}")
    print(f"  CVaR 95: {cvar_95}")
    print(f"  Max Drawdown: {max_drawdown}")
    print(f"  Sector Concentration: {sector_concentration}")
    print(f"  Factor Exposures: {len(factor_exposures)} factors")
    print(f"  Stress Tests: {len(stress_tests)} scenarios")
    print(f"  Alerts: {len(alerts)}")
    
    # Write to screener.db
    print(f"\nWriting to screener.db: {SCREENER_DB}")
    conn = sqlite3.connect(str(SCREENER_DB))
    cursor = conn.cursor()
    
    cursor.execute(CREATE_RISK_METRICS)
    
    insert_sql = """
        INSERT OR REPLACE INTO risk_metrics
        (trade_date, var_95, var_99, cvar_95, max_drawdown, sector_concentration,
         factor_exposures, stress_tests, alerts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    cursor.execute(insert_sql, (
        report_date,
        var_95,
        var_99,
        cvar_95,
        max_drawdown,
        sector_concentration,
        json.dumps(factor_exposures, ensure_ascii=False) if factor_exposures else None,
        json.dumps(stress_tests, ensure_ascii=False) if stress_tests else None,
        json.dumps(alerts, ensure_ascii=False) if alerts else None,
    ))
    
    conn.commit()
    conn.close()
    
    print(f"  Inserted/updated risk_metrics for {report_date}")
    
    print(f"\n{'=' * 60}")
    print(f"Summary:")
    print(f"  Report: {report_path.name}")
    print(f"  Trade date: {report_date}")
    print(f"  Risk metrics written to risk_metrics table")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
