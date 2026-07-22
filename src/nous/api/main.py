"""Data Service — 只读代理 for screener.db + reports.db"""
import os, sys, json, asyncio
from fastapi import FastAPI, HTTPException, Security, Depends, Request, Query
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from nous.core.db import get_readonly_db, SCREENER_DB, REPORTS_DB
from nous.api.sse_manager import sse_manager
from nous.api.routes import sentiment, macro, index_routes, flow, futures, theme, portfolio, risk, messages, quant_routes, reports

# ── Auth ────────────────────────────────────────────
API_KEY = os.environ.get("API_KEY", "")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(key: str = Security(api_key_header)):
    if API_KEY and key != API_KEY:
        raise HTTPException(401, "Invalid API key")
    return key

# ── Rate limiter ─────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── App ──────────────────────────────────────────────
app = FastAPI(title="Data Service", version="1.0")
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    raise HTTPException(429, "Rate limit exceeded")

# ── Health ───────────────────────────────────────────
@app.get("/v1/health")
async def health():
    return {"status": "ok", "db": "connected"}

# ── SSE Stream ───────────────────────────────────────
@app.get("/v1/sse/stream")
async def sse_stream(topics: str = Query(...), request: Request = None):
    topic_list = [t.strip() for t in topics.split(",")]
    
    async def generate():
        q = await sse_manager.connect(topic_list)
        try:
            yield f"event: connected\ndata: {json.dumps({'topics': topic_list})}\n\n"
            while True:
                try:
                    if request and await request.is_disconnected():
                        break
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"event: {event['topic']}\ndata: {json.dumps(event['data'])}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )

# ── Startup ──────────────────────────────────────────
@app.on_event("startup")
async def startup():
    from nous.api.sse_broadcaster import broadcast_loop
    asyncio.create_task(broadcast_loop())

# ── Router registration ──────────────────────────────
app.include_router(sentiment.router)
app.include_router(macro.router)
app.include_router(index_routes.router)
app.include_router(flow.router)
app.include_router(futures.router)
app.include_router(theme.router)
app.include_router(portfolio.router)
app.include_router(risk.router)
app.include_router(messages.router)
app.include_router(quant_routes.router)
app.include_router(reports.router)
