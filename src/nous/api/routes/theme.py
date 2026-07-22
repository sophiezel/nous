"""主线板块 + 筛选 + 个股"""
from fastapi import APIRouter, Query
from nous.core.db import safe_query, SCREENER_DB, REPORTS_DB

router = APIRouter()

@router.get("/v1/theme/stocks")
def theme_stocks():
    rows = safe_query(REPORTS_DB,
        "SELECT * FROM theme_pool_stocks ORDER BY theme, segment, symbol")
    themes = {}
    for r in rows:
        themes.setdefault(r["theme"], []).append(r)
    return [{"theme": k, "stocks": v} for k, v in themes.items()]

@router.get("/v1/theme/fund-flow")
def theme_fund_flow(symbols: str = Query(...)):
    sym_list = [s.strip() for s in symbols.split(",")]
    if not sym_list:
        return {"total": None}
    placeholders = ",".join(["?"] * len(sym_list))
    rows = safe_query(SCREENER_DB,
        f"SELECT SUM(main_net) as total FROM fund_flow_stock WHERE symbol IN ({placeholders}) AND trade_date = (SELECT MAX(trade_date) FROM fund_flow_stock)",
        sym_list)
    return {"total": rows[0]["total"] if rows else None}

@router.get("/v1/theme/trend")
def theme_trend(symbols: str = Query(...), days: int = 60):
    sym_list = [s.strip() for s in symbols.split(",")][:10]
    if not sym_list:
        return []
    placeholders = ",".join(["?"] * len(sym_list))
    limit = days + 1
    return safe_query(SCREENER_DB,
        f"""SELECT trade_date, AVG(chg) as avg_pct FROM (
            SELECT trade_date, (close - prev_close) / prev_close * 100 as chg FROM (
                SELECT trade_date, close, LAG(close) OVER (ORDER BY trade_date ASC) as prev_close
                FROM stock_daily WHERE symbol IN ({placeholders}) ORDER BY trade_date DESC LIMIT ?
            ) WHERE prev_close IS NOT NULL AND prev_close > 0 ORDER BY trade_date ASC
        ) GROUP BY trade_date ORDER BY trade_date ASC""",
        sym_list + [limit])

@router.get("/v1/stock/trend")
def stock_trend(symbol: str = Query(...), days: int = 30):
    limit = days + 1
    rows = safe_query(SCREENER_DB,
        f"""SELECT chg FROM (
            SELECT (close - prev_close) / prev_close * 100 as chg, trade_date FROM (
                SELECT trade_date, close, LAG(close) OVER (ORDER BY trade_date ASC) as prev_close
                FROM stock_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?
            ) WHERE prev_close IS NOT NULL AND prev_close > 0 ORDER BY trade_date ASC
        )""",
        (symbol, limit))
    return [r["chg"] for r in rows]

@router.get("/v1/stock/fundamental")
def stock_fundamental(symbol: str = Query(...)):
    rows = safe_query(SCREENER_DB,
        "SELECT * FROM stock_fundamental WHERE symbol = ? LIMIT 1", (symbol,))
    return rows[0] if rows else {}

@router.get("/v1/stock/fund-flow")
def stock_fund_flow(symbol: str = Query(...), limit: int = 5):
    return safe_query(SCREENER_DB,
        "SELECT trade_date, close, pct_chg, main_net_buy, super_large_net, large_net FROM stock_fund_flow WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?",
        (symbol, limit))

@router.get("/v1/stock/daily")
def stock_daily(symbol: str = Query(...), limit: int = 60):
    return safe_query(SCREENER_DB,
        "SELECT trade_date, open, high, low, close, volume, amount FROM stock_daily_all WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?",
        (symbol, limit))

@router.get("/v1/screen/latest")
def screen_latest(limit: int = 10):
    rows = safe_query(SCREENER_DB, "SELECT MAX(screen_date) as d FROM screen_results")
    if not rows or not rows[0]["d"]:
        return []
    return safe_query(SCREENER_DB,
        "SELECT symbol, name, score, pe, pb, roe FROM screen_results WHERE screen_date = ? AND score IS NOT NULL ORDER BY score DESC LIMIT ?",
        (rows[0]["d"], limit))

@router.get("/v1/rec/performance")
def rec_performance(symbol: str = Query(...)):
    return safe_query(SCREENER_DB,
        "SELECT rec_date, rec_close, last_close, return_1d, return_5d, return_20d, last_return FROM rec_performance WHERE symbol = ? ORDER BY rec_date DESC LIMIT 10",
        (symbol,))
