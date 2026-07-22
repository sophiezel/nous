"""量化"""
from fastapi import APIRouter, Query
from nous.core.db import safe_query, SCREENER_DB

router = APIRouter()

@router.get("/v1/quant/ic")
def quant_ic(limit: int = 30):
    return safe_query(SCREENER_DB,
        "SELECT * FROM quant_ic_history ORDER BY trade_date DESC LIMIT ?", (limit,))

@router.get("/v1/quant/factors")
def quant_factors(limit: int = 10):
    rows = safe_query(SCREENER_DB,
        "SELECT MAX(trade_date) as d FROM quant_factor_importance")
    if not rows or not rows[0]["d"]:
        return []
    return safe_query(SCREENER_DB,
        "SELECT * FROM quant_factor_importance WHERE trade_date = ? ORDER BY importance_enc DESC LIMIT ?",
        (rows[0]["d"], limit))

@router.get("/v1/quant/signals/latest")
def quant_signals_latest():
    rows = safe_query(SCREENER_DB,
        "SELECT * FROM quant_signals ORDER BY created_at DESC LIMIT 1")
    return rows[0] if rows else {}
