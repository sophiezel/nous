"""消息"""
from fastapi import APIRouter, Query
from nous.core.db import safe_query, SCREENER_DB

router = APIRouter()

@router.get("/v1/messages")
def messages_list(limit: int = 50, type: str = None):
    if type:
        return safe_query(SCREENER_DB,
            "SELECT *, read as is_read FROM messages WHERE type = ? ORDER BY created_at DESC LIMIT ?",
            (type, limit))
    return safe_query(SCREENER_DB,
        "SELECT *, read as is_read FROM messages ORDER BY created_at DESC LIMIT ?", (limit,))

@router.get("/v1/weixin/health")
def weixin_health():
    from datetime import date
    today = date.today().isoformat()
    total = safe_query(SCREENER_DB,
        "SELECT COUNT(*) as c FROM messages WHERE created_at >= ?", (today,))
    pushed = safe_query(SCREENER_DB,
        "SELECT COUNT(*) as c FROM messages WHERE created_at >= ? AND pushed_weixin = 1", (today,))
    return {
        "today_total": total[0]["c"] if total else 0,
        "today_pushed": pushed[0]["c"] if pushed else 0,
    }
