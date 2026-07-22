#!/usr/bin/env python3
"""交易信号评估引擎 v1.0

三重信号: 入场(MA金叉+量比+RSI) → 持仓管理 → 出场(移动止盈/MA死叉/硬止损/时间止损)

纯SQLite实现, 批量查询, 不做逐只API调用。
"""

import sqlite3
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from nous.core.db import _resolve_path
DB = Path(_resolve_path("screener.db"))


@dataclass
class Signal:
    approved: bool
    reason: str
    score: float = 0.0
    details: dict = field(default_factory=dict)


# ══════════════════════════════════════════════
# 入场信号
# ══════════════════════════════════════════════

def evaluate_buy_signal(symbol: str, as_of_date: str, db_conn,
                        pool_type: str = "A_long",
                        market_regime: str = "SIDEWAYS") -> Signal:
    """评估入场信号
    
    pool_type: A_long/A_short/HK_long/HK_short
    market_regime: BULL/BEAR/SIDEWAYS/VOLATILE
    
    入场条件 (ALL must pass):
    1. MA5 > MA20 (短期趋势向上)
    2. 量比 > 1.2 (有资金跟进)
    3. RSI14 < 75 (不在超买区,短线池放宽到80)
    4. BEAR市场: 长线池减半通过, 短线池拒绝
    """
    # 获取技术指标
    ma = _compute_ma(symbol, as_of_date, db_conn)
    rsi = _compute_rsi14(symbol, as_of_date, db_conn)
    vol_ratio = _compute_volume_ratio(symbol, as_of_date, db_conn)
    close = _get_close(symbol, as_of_date, db_conn)
    
    if not close:
        return Signal(False, "无当日收盘价", 0)
    
    reasons = []
    score = 5.0
    details = {"ma5": ma.get("ma5"), "ma20": ma.get("ma20"),
               "rsi14": rsi, "vol_ratio": vol_ratio, "close": close}
    
    is_short = "short" in pool_type
    
    # 1. MA金叉
    if ma.get("ma5") and ma.get("ma20"):
        if ma["ma5"] > ma["ma20"]:
            score += 1.5
        else:
            reasons.append(f"MA5({ma['ma5']:.2f})<MA20({ma['ma20']:.2f})")
            score -= 2
    else:
        reasons.append("MA数据不足")
        score -= 1
    
    # 2. 量比
    if vol_ratio:
        if vol_ratio > 1.2:
            score += 1
        else:
            reasons.append(f"量比{vol_ratio:.1f}<1.2")
            score -= 0.5
    else:
        score -= 0.5  # 无数据不阻止
    
    # 3. RSI
    rsi_limit = 80 if is_short else 75
    if rsi is not None:
        if rsi < rsi_limit:
            score += 1
        else:
            reasons.append(f"RSI{rsi:.0f}>={rsi_limit}(超买)")
            score -= 1
    else:
        score -= 0.5
    
    # 4. 体制过滤
    if market_regime == "BEAR":
        if is_short:
            return Signal(False, f"BEAR市场跳过短线", 0, details)
        else:
            reasons.append("BEAR市场(长线减半)")
            score -= 2
    
    if reasons:
        return Signal(False, "; ".join(reasons), score, details)
    return Signal(True, "信号确认", score, details)


# ══════════════════════════════════════════════
# 出场信号
# ══════════════════════════════════════════════

def evaluate_sell_signal(symbol: str, entry_price: float, entry_date: str,
                         as_of_date: str, db_conn, pool_type: str = "A_long",
                         highest_since_entry: float = None) -> Signal:
    """评估出场信号 (逐日)
    
    出场条件 (ANY triggers):
    1. ATR移动止盈: 从最高点回撤 > 2×ATR(短线1.5×)
    2. MA死叉: MA5 < MA20
    3. 硬止损: 价格 < 入场价 × 0.93(长线)/0.95(短线)
    4. 时间止损: 长线>30天且亏损>5%
    """
    close = _get_close(symbol, as_of_date, db_conn)
    if not close:
        return Signal(False, "无收盘价", 0)
    
    is_long = "long" in pool_type
    is_short = "short" in pool_type
    
    # 计算持仓天数
    from datetime import date
    days_held = (date.fromisoformat(as_of_date) - date.fromisoformat(entry_date)).days
    pnl_pct = (close - entry_price) / entry_price * 100
    
    # 1. ATR移动止盈
    atr = _compute_atr14(symbol, as_of_date, db_conn)
    if atr and highest_since_entry:
        atr_mult = 2.0 if is_long else 1.5
        drawdown = (highest_since_entry - close) / highest_since_entry * 100
        atr_stop = atr / close * 100 * atr_mult
        if drawdown > atr_stop and pnl_pct > 0:  # 只在盈利时移动止盈
            return Signal(True, f"ATR移动止盈(回撤{drawdown:.1f}%>{atr_stop:.1f}%)",
                         10, {"pnl": pnl_pct, "drawdown": drawdown, "atr": atr})
    
    # 2. MA死叉
    ma = _compute_ma(symbol, as_of_date, db_conn)
    if ma.get("ma5") and ma.get("ma20"):
        if ma["ma5"] < ma["ma20"] and pnl_pct < 0:  # 只在亏损时触发
            return Signal(True, f"MA死叉(MA5={ma['ma5']:.2f}<MA20={ma['ma20']:.2f})",
                         5, {"pnl": pnl_pct})
    
    # 3. 硬止损
    stop_mult = 0.93 if is_long else 0.95
    if close < entry_price * stop_mult:
        return Signal(True, f"硬止损(跌{abs(pnl_pct):.1f}%>{abs((1-stop_mult)*100):.0f}%)",
                     8, {"pnl": pnl_pct})
    
    # 4. 时间止损 (仅长线)
    if is_long and days_held > 30 and pnl_pct < -5:
        return Signal(True, f"时间止损(持仓{days_held}天, 仍亏{pnl_pct:.1f}%)",
                     3, {"pnl": pnl_pct, "days_held": days_held})
    
    return Signal(False, "继续持有", 0, {"pnl": pnl_pct, "days_held": days_held})


# ══════════════════════════════════════════════
# 仓位计算
# ══════════════════════════════════════════════

def compute_position_size(symbol: str, as_of_date: str, db_conn,
                          pool_type: str = "A_long") -> float:
    """ATR动态仓位: 仓位% = 1% / ATR%, 上限15%, 下限3%"""
    atr = _compute_atr14(symbol, as_of_date, db_conn)
    close = _get_close(symbol, as_of_date, db_conn)
    if not atr or not close or close <= 0:
        return 0.10  # 默认10%
    
    atr_pct = atr / close * 100
    if atr_pct <= 0:
        return 0.10
    
    pos = min(0.15, max(0.03, 1.0 / atr_pct / 100))
    return round(pos, 3)


# ══════════════════════════════════════════════
# 技术指标计算 (纯SQL)
# ══════════════════════════════════════════════

def _compute_ma(symbol: str, as_of_date: str, db_conn) -> dict:
    """计算MA5/MA20"""
    rows = db_conn.execute("""
        SELECT close FROM stock_daily_all 
        WHERE symbol=? AND trade_date <= ?
        ORDER BY trade_date DESC LIMIT 20
    """, (symbol, as_of_date)).fetchall()
    
    closes = [r[0] for r in rows if r[0]]
    if not closes:
        return {}
    
    result = {}
    if len(closes) >= 5:
        result["ma5"] = round(sum(closes[:5]) / 5, 2)
    if len(closes) >= 20:
        result["ma20"] = round(sum(closes[:20]) / 20, 2)
    return result


def _compute_rsi14(symbol: str, as_of_date: str, db_conn) -> Optional[float]:
    """计算14日RSI"""
    rows = db_conn.execute("""
        SELECT close FROM stock_daily_all 
        WHERE symbol=? AND trade_date <= ?
        ORDER BY trade_date DESC LIMIT 15
    """, (symbol, as_of_date)).fetchall()
    
    closes = [r[0] for r in rows if r[0]]
    if len(closes) < 15:
        return None
    
    gains = losses = 0
    for i in range(14):
        diff = closes[i] - closes[i+1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)
    
    if losses == 0:
        return 100.0
    avg_gain = gains / 14
    avg_loss = losses / 14
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    return round(100 - 100 / (1 + rs), 1)


def _compute_volume_ratio(symbol: str, as_of_date: str, db_conn) -> Optional[float]:
    """量比 = 今日成交量 / 过去5日均量"""
    rows = db_conn.execute("""
        SELECT volume FROM stock_daily_all 
        WHERE symbol=? AND trade_date <= ?
        ORDER BY trade_date DESC LIMIT 6
    """, (symbol, as_of_date)).fetchall()
    
    volumes = [r[0] for r in rows if r[0]]
    if len(volumes) < 6:
        return None
    
    today_vol = volumes[0]
    avg_5 = sum(volumes[1:6]) / 5
    return round(today_vol / avg_5, 2) if avg_5 > 0 else None


def _compute_atr14(symbol: str, as_of_date: str, db_conn) -> Optional[float]:
    """计算14日ATR"""
    rows = db_conn.execute("""
        SELECT high, low, close FROM stock_daily_all 
        WHERE symbol=? AND trade_date <= ?
        ORDER BY trade_date DESC LIMIT 15
    """, (symbol, as_of_date)).fetchall()
    
    prices = [(r[0], r[1], r[2]) for r in rows if r[0] and r[1] and r[2]]
    if len(prices) < 15:
        return None
    
    trs = []
    for i in range(len(prices) - 1):
        h, l, c = prices[i]
        prev_c = prices[i+1][2]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    
    return round(sum(trs) / len(trs), 2) if trs else None


def _get_close(symbol: str, as_of_date: str, db_conn) -> Optional[float]:
    """获取指定日期的收盘价(最近的≤as_of_date)"""
    row = db_conn.execute("""
        SELECT close FROM stock_daily_all 
        WHERE symbol=? AND trade_date <= ?
        ORDER BY trade_date DESC LIMIT 1
    """, (symbol, as_of_date)).fetchone()
    return row[0] if row else None
