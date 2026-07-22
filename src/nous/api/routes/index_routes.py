"""指数日线 + 全球指数"""
from fastapi import APIRouter, Query
from nous.core.db import safe_query, SCREENER_DB

router = APIRouter()

@router.get("/v1/index/daily")
def index_daily(codes: str = "000001", days: int = 30):
    code_list = [c.strip() for c in codes.split(",")]
    results = {}
    for code in code_list:
        results[code] = safe_query(SCREENER_DB,
            "SELECT trade_date, close FROM index_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?",
            (code, days))
    return results

@router.get("/v1/global/index")
def global_index(symbols: str = "VIX,KWEB"):
    sym_list = [s.strip() for s in symbols.split(",")]
    results = {}
    for sym in sym_list:
        results[sym] = safe_query(SCREENER_DB,
            "SELECT trade_date, close FROM index_global_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT 30",
            (sym,))
    return results

@router.get("/v1/global/latest")
def global_latest(symbol: str = Query(...)):
    rows = safe_query(SCREENER_DB,
        "SELECT trade_date, close FROM index_global_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
        (symbol,))
    return rows[0] if rows else {}
