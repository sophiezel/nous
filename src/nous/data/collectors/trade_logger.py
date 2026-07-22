#!/usr/bin/env python3
"""
交易日志记录器 — 每笔买入/卖出写入trade_log表
提供人类可读的reason_text生成
"""
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from nous.data.storage import get_db

REASON_TEMPLATES = {
    # 买入
    'SLOT1_BUY': 'Slot1买入(09:31): {reason_detail}',
    'SLOT2_BUY': 'Slot2补仓(10:01): {reason_detail}',
    'SLOT3_BUY': 'Slot3补仓(14:01): {reason_detail}',
    'QUANT_SIGNAL': '量化信号: {reason_detail}',
    
    # 卖出
    'STOP_LOSS': '止损触发: 持仓均价{entry_price:.2f}→当前{current_price:.2f}, 跌幅{pnl:.1f}%>{stop_pct:.0f}%',
    'TAKE_PROFIT': '止盈50%: 盈利{pnl:.1f}%>{target_pct:.0f}%, RSI={rsi:.1f}>70',
    'TIME_EXIT': '时间退出: 持仓{hold_days}天>{max_days}天, 盈亏{pnl:.1f}%<3%',
    'POOL_REMOVE': '池移除: 连续2日不在推荐池',
    'TRAILING_STOP': '移动止盈: 从最高盈利回撤{drawdown:.1f}%>3%',
    'QUANT_SELL': '量化卖出: {reason_detail}',
}


def log_buy(symbol: str, name: str, price: float, shares: int, 
            portfolio: str, reason_code: str, reason_detail: str = "",
            atr: float = None, rsi: float = None, entry_score: float = None,
            model_name: str = None, trade_date: str = None, trade_time: str = None) -> int:
    """记录一笔买入。
    
    Args:
        reason_detail: 具体说明(如 "荐股评分9.0, ATR=2.3, RSI=66.6")
    
    Returns:
        trade_log row id
    """
    if trade_date is None:
        trade_date = date.today().isoformat()
    if trade_time is None:
        trade_time = datetime.now().strftime("%H:%M")
    
    template = REASON_TEMPLATES.get(reason_code, '{reason_detail}')
    reason_text = template.format(
        reason_detail=reason_detail,
        atr=atr or 0, rsi=rsi or 0, score=entry_score or 0
    )
    
    conn = get_db(write=True)
    try:
        cur = conn.execute("""
            INSERT INTO trade_log (trade_date, trade_time, symbol, name, action,
                price, shares, amount, reason_code, reason_text, portfolio,
                atr_at_entry, rsi_at_trade, entry_score, model_name)
            VALUES (?,?,?,?,'BUY',?,?,?,?,?,?,?,?,?,?)
        """, (
            trade_date, trade_time, symbol, name,
            price, shares, price * shares,
            reason_code, reason_text, portfolio,
            atr, rsi, entry_score, model_name
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def log_sell(symbol: str, name: str, price: float, shares: int,
             portfolio: str, reason_code: str,
             pnl_pct: float = None, pnl_amount: float = None, hold_days: int = None,
             entry_price: float = None, current_price: float = None,
             stop_pct: float = None, target_pct: float = None,
             rsi: float = None, max_days: int = None, drawdown: float = None,
             reason_detail: str = "",
             atr: float = None, trade_date: str = None, trade_time: str = None) -> int:
    """记录一笔卖出。
    
    Returns:
        trade_log row id
    """
    if trade_date is None:
        trade_date = date.today().isoformat()
    if trade_time is None:
        trade_time = datetime.now().strftime("%H:%M")
    
    pnl = pnl_pct or 0
    
    # Format reason text
    format_args = {
        'reason_detail': reason_detail,
        'entry_price': entry_price or 0,
        'current_price': current_price or price,
        'pnl': abs(pnl),
        'stop_pct': stop_pct or 0,
        'target_pct': target_pct or 0,
        'rsi': rsi or 0,
        'hold_days': hold_days or 0,
        'max_days': max_days or 0,
        'drawdown': drawdown or 0,
    }
    template = REASON_TEMPLATES.get(reason_code, '{reason_detail}')
    reason_text = template.format(**format_args)
    
    conn = get_db(write=True)
    try:
        cur = conn.execute("""
            INSERT INTO trade_log (trade_date, trade_time, symbol, name, action,
                price, shares, amount, reason_code, reason_text, portfolio,
                pnl_pct, pnl_amount, hold_days, atr_at_entry, rsi_at_trade)
            VALUES (?,?,?,?,'SELL',?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade_date, trade_time, symbol, name,
            price, shares, price * shares,
            reason_code, reason_text, portfolio,
            pnl_pct, pnl_amount, hold_days,
            atr, rsi
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_today_trades(portfolio: str = None) -> list[dict]:
    """查询今日所有交易记录."""
    conn = get_db(write=False)
    try:
        today = date.today().isoformat()
        sql = "SELECT * FROM trade_log WHERE trade_date = ?"
        params = [today]
        if portfolio:
            sql += " AND portfolio = ?"
            params.append(portfolio)
        sql += " ORDER BY trade_time"
        
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── CLI ────────────────────────────────────────────────

if __name__ == "__main__":
    trades = get_today_trades()
    print(f"Today's trades: {len(trades)}")
    for t in trades:
        emoji = "🔵" if t['action'] == 'BUY' else "🔴"
        print(f"  {t['trade_time']} {emoji} {t['symbol']} {t['name']} "
              f"{t['action']} {t['shares']}股@{t['price']:.2f} "
              f"| {t['portfolio']} | {t['reason_code']}")
        if t['pnl_pct']:
            print(f"    P&L: {t['pnl_pct']:.1f}% ({t['pnl_amount']:.0f}元) {t['hold_days']}天")
