"""SSE 广播器 — 交易时段 5s 轮询, 非交易时段 60s"""
import asyncio, datetime, json
from nous.core.db import get_readonly_db, SCREENER_DB
from nous.api.sse_manager import sse_manager

def is_trading_hours() -> bool:
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 540 <= t <= 905  # 09:00-15:05

async def broadcast_loop():
    db = get_readonly_db(SCREENER_DB)
    while True:
        try:
            if is_trading_hours():
                # 涨跌家数
                row = db.execute(
                    "SELECT * FROM market_breadth_snapshot ORDER BY datetime DESC LIMIT 1"
                ).fetchone()
                if row:
                    await sse_manager.broadcast("breadth", dict(row))

                # 北向实时
                nb = db.execute(
                    "SELECT * FROM northbound_intraday ORDER BY datetime DESC LIMIT 1"
                ).fetchone()
                if nb:
                    await sse_manager.broadcast("northbound", dict(nb))

                # 指数快照
                indices = db.execute(
                    """SELECT symbol, close FROM index_daily 
                       WHERE trade_date = (SELECT MAX(trade_date) FROM index_daily) LIMIT 10"""
                ).fetchall()
                if indices:
                    await sse_manager.broadcast("quote", {
                        "indices": [dict(r) for r in indices]
                    })

                # 消息
                msgs = db.execute(
                    "SELECT * FROM messages ORDER BY created_at DESC LIMIT 5"
                ).fetchall()
                if msgs:
                    await sse_manager.broadcast("messages", {
                        "messages": [dict(r) for r in msgs]
                    })

                await asyncio.sleep(5)
            else:
                await asyncio.sleep(60)
        except Exception as e:
            print(f"[SSE] broadcast error: {e}")
            await asyncio.sleep(10)
