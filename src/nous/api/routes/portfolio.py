"""持仓"""
from fastapi import APIRouter, Query
from nous.core.db import safe_query, SCREENER_DB, REPORTS_DB

router = APIRouter()

@router.get("/v1/portfolio/paper")
def portfolio_paper(market: str = "a"):
    if market == "hk":
        return safe_query(SCREENER_DB,
            "SELECT *, COALESCE(weight_pct,0) as weight_pct FROM sim_hk_position")
    return safe_query(SCREENER_DB,
        "SELECT *, COALESCE(weight_pct,0) as weight_pct FROM sim_position")

@router.get("/v1/portfolio/paper/nav")
def portfolio_paper_nav(market: str = "a", days: int = 60):
    if market == "hk":
        return safe_query(SCREENER_DB,
            "SELECT trade_date, nav, daily_return FROM sim_hk_nav ORDER BY trade_date DESC LIMIT ?", (days,))
    return safe_query(SCREENER_DB,
        "SELECT datetime as trade_date, COALESCE(nav, market_value) as nav, COALESCE(daily_return,0) as daily_return FROM sim_portfolio_snapshot ORDER BY datetime DESC LIMIT ?", (days,))

@router.get("/v1/portfolio/paper/trades")
def portfolio_paper_trades(market: str = "a", limit: int = 50):
    table = "sim_hk_trades" if market == "hk" else "sim_trades"
    return safe_query(SCREENER_DB,
        f"SELECT * FROM {table} ORDER BY trade_time DESC LIMIT ?", (limit,))

@router.get("/v1/portfolio/quant")
def portfolio_quant(market: str = "a"):
    table = "quant_hk_position" if market == "hk" else "quant_position"
    return safe_query(SCREENER_DB,
        f"SELECT *, COALESCE(weight,0) as weight_pct FROM {table} ORDER BY weight DESC")

@router.get("/v1/portfolio/live")
def portfolio_live():
    return safe_query(REPORTS_DB,
        "SELECT symbol, name, weight_pct, pnl_pct, sector FROM live_portfolio ORDER BY weight_pct DESC")

@router.get("/v1/recommendations/active")
def recommendations_active():
    return safe_query(SCREENER_DB,
        "SELECT * FROM recommendation_history WHERE status='active' ORDER BY entry_date DESC")

@router.get("/v1/recommendations/history")
def recommendations_history(days: int = 30):
    return safe_query(SCREENER_DB,
        "SELECT * FROM recommendation_history WHERE status!='active' ORDER BY exit_date DESC LIMIT ?", (days,))
