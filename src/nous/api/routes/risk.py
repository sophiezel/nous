"""风控"""
from fastapi import APIRouter, Query
from nous.core.db import safe_query, SCREENER_DB

router = APIRouter()

@router.get("/v1/risk/overview")
def risk_overview():
    rows = safe_query(SCREENER_DB,
        "SELECT * FROM risk_metrics ORDER BY trade_date DESC LIMIT 1")
    return rows[0] if rows else {}

@router.get("/v1/risk/events")
def risk_events(limit: int = 20):
    return safe_query(SCREENER_DB,
        "SELECT * FROM sim_drawdown_alerts ORDER BY alert_time DESC LIMIT ?", (limit,))

@router.get("/v1/benchmark")
def benchmark():
    return safe_query(SCREENER_DB,
        "SELECT * FROM benchmark_comparison")
