"""资金流"""
from fastapi import APIRouter, Query
from nous.core.db import safe_query, SCREENER_DB

router = APIRouter()

@router.get("/v1/flow/margin")
def flow_margin(days: int = 60):
    return {
        "margin_daily": safe_query(SCREENER_DB,
            "SELECT trade_date, margin_balance, margin_buy FROM margin_daily ORDER BY trade_date DESC LIMIT ?", (days,)),
        "margin_short": safe_query(SCREENER_DB,
            "SELECT trade_date, short_balance, short_volume, margin_balance FROM margin_short_daily ORDER BY trade_date DESC LIMIT ?", (days,)),
    }

@router.get("/v1/flow/hsgt")
def flow_hsgt(dir: str = "north", days: int = 60):
    return safe_query(SCREENER_DB,
        "SELECT trade_date, net_buy FROM hsgt_daily WHERE direction = ? ORDER BY trade_date DESC LIMIT ?",
        (dir, days))

@router.get("/v1/flow/hsgt/stocks")
def flow_hsgt_stocks(dir: str = "北向", limit: int = 10, date: str = None):
    if date:
        return safe_query(SCREENER_DB,
            "SELECT * FROM hsgt_stock_daily WHERE direction = ? AND trade_date = ? ORDER BY rank LIMIT ?",
            (dir, date, limit))
    # latest date
    rows = safe_query(SCREENER_DB,
        "SELECT MAX(trade_date) as d FROM hsgt_stock_daily WHERE direction = ?", (dir,))
    if not rows or not rows[0]["d"]:
        return []
    return safe_query(SCREENER_DB,
        "SELECT * FROM hsgt_stock_daily WHERE direction = ? AND trade_date = ? ORDER BY rank LIMIT ?",
        (dir, rows[0]["d"], limit))

@router.get("/v1/flow/hsgt/sectors")
def flow_hsgt_sectors(dir: str = "北向", limit: int = 5):
    rows = safe_query(SCREENER_DB,
        "SELECT MAX(trade_date) as d FROM hsgt_sector_daily WHERE direction = ?", (dir,))
    if not rows or not rows[0]["d"]:
        return []
    return safe_query(SCREENER_DB,
        "SELECT * FROM hsgt_sector_daily WHERE direction = ? AND trade_date = ? ORDER BY ABS(total_net_buy) DESC LIMIT ?",
        (dir, rows[0]["d"], limit))

@router.get("/v1/flow/lhb")
def flow_lhb(limit: int = 10, date: str = None):
    if date:
        return safe_query(SCREENER_DB,
            "SELECT * FROM lhb_daily WHERE trade_date = ? ORDER BY ABS(net_amount) DESC LIMIT ?",
            (date, limit))
    rows = safe_query(SCREENER_DB, "SELECT MAX(trade_date) as d FROM lhb_daily")
    if not rows or not rows[0]["d"]:
        return []
    return safe_query(SCREENER_DB,
        "SELECT * FROM lhb_daily WHERE trade_date = ? ORDER BY ABS(net_amount) DESC LIMIT ?",
        (rows[0]["d"], limit))

@router.get("/v1/flow/block")
def flow_block(limit: int = 10):
    rows = safe_query(SCREENER_DB, "SELECT MAX(trade_date) as d FROM block_trades")
    if not rows or not rows[0]["d"]:
        return []
    return safe_query(SCREENER_DB,
        """SELECT b.*, sd.close as daily_close
           FROM block_trades b
           LEFT JOIN stock_daily sd ON b.symbol = sd.symbol AND b.trade_date = sd.trade_date
           WHERE b.trade_date = ?
           ORDER BY b.amount DESC LIMIT ?""",
        (rows[0]["d"], limit))

@router.get("/v1/flow/etf")
def flow_etf(limit: int = 6):
    rows = safe_query(SCREENER_DB, "SELECT MAX(trade_date) as d FROM etf_flow_daily")
    if not rows or not rows[0]["d"]:
        return []
    return safe_query(SCREENER_DB,
        "SELECT * FROM etf_flow_daily WHERE trade_date = ? ORDER BY ABS(pct_change) DESC LIMIT ?",
        (rows[0]["d"], limit))

@router.get("/v1/market/turnover")
def market_turnover():
    rows = safe_query(SCREENER_DB,
        "SELECT SUM(volume * close) as total FROM stock_daily WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily) AND volume > 0 AND close > 0")
    return {"total": rows[0]["total"] if rows else None}
