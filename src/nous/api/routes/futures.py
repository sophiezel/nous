"""期货"""
from fastapi import APIRouter, Query
from nous.core.db import safe_query, SCREENER_DB

router = APIRouter()

@router.get("/v1/futures")
def futures_latest(codes: str = Query(...)):
    code_list = [c.strip() for c in codes.split(",")]
    results = {}
    for code in code_list:
        rows = safe_query(SCREENER_DB,
            "SELECT trade_date, close FROM futures_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT 2",
            (code,))
        if rows:
            results[code] = {"latest": rows[0], "prev": rows[1] if len(rows) > 1 else None}
    return results
