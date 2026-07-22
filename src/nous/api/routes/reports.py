"""报告"""
from fastapi import APIRouter, Query
from nous.core.db import safe_query, REPORTS_DB

router = APIRouter()

@router.get("/v1/reports/latest")
def reports_latest(type: str = Query(None)):
    if type:
        rows = safe_query(REPORTS_DB,
            "SELECT * FROM reports WHERE type = ? ORDER BY created_at DESC LIMIT 1", (type,))
        return rows[0] if rows else {}
    rows = safe_query(REPORTS_DB,
        "SELECT * FROM reports ORDER BY created_at DESC LIMIT 20")
    return rows

@router.get("/v1/reports/count")
def reports_count():
    rows = safe_query(REPORTS_DB, "SELECT COUNT(*) as c FROM reports")
    return rows[0] if rows else {"c": 0}
