#!/usr/bin/env python3
"""
ATR计算器 — 从stock_daily计算Average True Range

用法:
    from nous.data.collectors.atr_calculator import get_atr, get_latest_price
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from nous.data.storage import get_db


def get_atr(symbol: str, period: int = 14) -> float | None:
    """计算ATR(14) — Average True Range.
    
    True Range = max(H-L, |H-C_prev|, |L-C_prev|)
    ATR = SMA(TR, period)
    
    Args:
        symbol: 股票代码(6位A股或5位港股)
        period: ATR周期, 默认14
    
    Returns:
        ATR值，数据不足返回None
    """
    conn = get_db(write=False)
    try:
        rows = conn.execute("""
            SELECT high, low, close FROM stock_daily
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT ?
        """, (symbol, period + 1)).fetchall()
        
        if len(rows) < period + 1:
            return None
        
        tr_values = []
        for i in range(len(rows) - 1):
            h, l, c = rows[i][0], rows[i][1], rows[i][2]
            c_prev = rows[i + 1][2]
            
            tr = max(
                h - l,
                abs(h - c_prev),
                abs(l - c_prev)
            )
            tr_values.append(tr)
        
        if not tr_values:
            return None
        
        # ATR = SMA of TR
        return sum(tr_values) / len(tr_values)
    finally:
        conn.close()


def get_latest_price(symbol: str) -> float | None:
    """获取最新收盘价."""
    conn = get_db(write=False)
    try:
        row = conn.execute("""
            SELECT close FROM stock_daily
            WHERE symbol = ?
            ORDER BY trade_date DESC LIMIT 1
        """, (symbol,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_latest_rsi(symbol: str, period: int = 14) -> float | None:
    """快速RSI估算 — 从stock_daily close序列计算Wilder's RSI."""
    conn = get_db(write=False)
    try:
        rows = conn.execute("""
            SELECT close FROM stock_daily
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT ?
        """, (symbol, period * 2)).fetchall()
        
        if len(rows) < period + 2:
            return None
        
        closes = [r[0] for r in reversed(rows)]
        
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        # Use last 'period' values for simple RSI
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
    finally:
        conn.close()


# ── CLI ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('symbol')
    parser.add_argument('--period', type=int, default=14)
    args = parser.parse_args()
    
    atr = get_atr(args.symbol, args.period)
    price = get_latest_price(args.symbol)
    rsi = get_latest_rsi(args.symbol)
    
    print(f"Symbol: {args.symbol}")
    print(f"ATR({args.period}): {atr:.2f}" if atr else "ATR: N/A")
    print(f"Latest Close: {price:.2f}" if price else "Price: N/A")
    print(f"RSI({args.period}): {rsi:.1f}" if rsi else "RSI: N/A")
    
    if atr and price:
        print(f"\nStop-loss (1.5×ATR): {price - 1.5*atr:.2f}")
        print(f"Take-profit (2×ATR): {price + 2.0*atr:.2f}")
