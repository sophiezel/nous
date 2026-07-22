"""宏观评分"""
from fastapi import APIRouter
from nous.core.db import safe_query, REPORTS_DB, SCREENER_DB

router = APIRouter()

@router.get("/v1/macro/score")
def macro_score():
    rows = safe_query(REPORTS_DB,
        "SELECT * FROM macro_scores ORDER BY date DESC LIMIT 1")
    return rows[0] if rows else {}

@router.get("/v1/macro/scores")
def macro_scores(limit: int = 60):
    return safe_query(REPORTS_DB,
        "SELECT * FROM macro_scores ORDER BY date DESC LIMIT ?", (limit,))

@router.get("/v1/macro/indicators")
def macro_indicators():
    return {
        "cpi": safe_query(SCREENER_DB, "SELECT trade_date, cpi_yoy as value FROM macro_cpi ORDER BY trade_date DESC LIMIT 60"),
        "ppi": safe_query(SCREENER_DB, "SELECT trade_date, ppi_yoy as value FROM macro_ppi ORDER BY trade_date DESC LIMIT 60"),
        "pmi": safe_query(SCREENER_DB, 'SELECT "月份" as trade_date, "制造业-指数" as value FROM macro_pmi ORDER BY "月份" DESC LIMIT 60'),
        "m2": safe_query(SCREENER_DB, 'SELECT "月份" as trade_date, "货币和准货币(M2)-同比增长" as value FROM macro_m2 ORDER BY "月份" DESC LIMIT 60'),
        "shibor": safe_query(SCREENER_DB, 'SELECT "日期" as trade_date, "O/N-定价" as value FROM macro_shibor ORDER BY "日期" DESC LIMIT 60'),
    }
