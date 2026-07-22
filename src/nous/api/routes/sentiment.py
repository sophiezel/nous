"""情绪"""
from fastapi import APIRouter
from nous.core.db import safe_query, SCREENER_DB

router = APIRouter()

@router.get("/v1/sentiment/latest")
def sentiment_latest():
    rows = safe_query(SCREENER_DB,
        "SELECT * FROM sentiment_cache ORDER BY date DESC LIMIT 1")
    return rows[0] if rows else {}

@router.get("/v1/sentiment/history")
def sentiment_history(days: int = 60):
    return safe_query(SCREENER_DB,
        "SELECT * FROM sentiment_cache ORDER BY date DESC LIMIT ?", (days,))
