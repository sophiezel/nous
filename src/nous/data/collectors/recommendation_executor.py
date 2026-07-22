#!/usr/bin/env python3
"""
荐股模拟盘 四池交易引擎 — 基于每日荐股报告驱动模拟交易

四池: A股短线 / A股长线 / 港股短线 / 港股长线
买入: 新入池标的 slot1(09:31)/2(10:01)/3(14:01) 分时建仓
卖出: 止损/止盈/时间退出/池移除/移动止盈 五维触发
每笔交易写入 trade_log + sim_trades + sim_position

用法:
    python -m src.collectors.recommendation_executor [--dry-run]
    # cron: 09:31 / 10:01 / 14:01 自动判断当前slot
"""
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from nous.data.storage import get_db
from nous.data.collectors.trade_logger import log_buy, log_sell
from nous.data.collectors.atr_calculator import get_atr, get_latest_price, get_latest_rsi

# ── 四池参数(ATR倍数体系) ──────────────────────────
PORTFOLIO_PARAMS = {
    ('A', 'short'):  {'key': 'A_short',  'atr_stop': 1.5, 'atr_target': 2.0, 'atr_trail_start': 3.0, 'max_days': 5,   'max_positions': 4, 'slot_capital': 50000},
    ('A', 'mid'):    {'key': 'A_mid',    'atr_stop': 2.0, 'atr_target': 3.0, 'atr_trail_start': 3.0, 'max_days': 20,  'max_positions': 3, 'slot_capital': 80000},
    ('A', 'long'):   {'key': 'A_long',   'atr_stop': 3.0, 'atr_target': 5.0, 'atr_trail_start': 3.0, 'max_days': 120, 'max_positions': 3, 'slot_capital': 120000},
    ('HK','short'):  {'key': 'HK_short', 'atr_stop': 2.0, 'atr_target': 2.5, 'atr_trail_start': 0,   'max_days': 5,   'max_positions': 3, 'slot_capital': 50000},
    ('HK','long'):   {'key': 'HK_long',  'atr_stop': 3.5, 'atr_target': 6.0, 'atr_trail_start': 0,   'max_days': 120, 'max_positions': 2, 'slot_capital': 100000},
}

# 各slot执行时间
SLOT_TIMES = {1: "09:31", 2: "10:01", 3: "14:01"}

# ── 市场体制过滤 ─────────────────────────────────────

def should_trade_today() -> tuple[bool, str]:
    """检查市场体制是否允许交易。
    
    Returns:
        (can_trade, reason)
    """
    conn = get_db(write=False)
    try:
        # 检查大盘日内跌幅
        row = conn.execute("""
            SELECT close FROM index_daily 
            WHERE symbol='IDX_000001' 
            ORDER BY trade_date DESC LIMIT 2
        """).fetchall()
        
        if len(row) >= 2:
            today_close = row[0][0]
            yesterday_close = row[1][0]
            change = (today_close - yesterday_close) / yesterday_close
            if change < -0.03:
                return False, f"上证日内跌幅{change*100:.1f}%>3%"
    finally:
        conn.close()
    
    return True, "OK"


def get_current_slot() -> int:
    """根据当前时间判断是哪个slot(1=09:31, 2=10:01, 3=14:01)。"""
    now = datetime.now().strftime("%H:%M")
    if now < "10:00":
        return 1
    elif now < "11:30":
        return 2
    else:
        return 3


# ── 池变更检测 ───────────────────────────────────────

def get_trl_picks(today: str) -> dict:
    """读取龙脉TRL引擎今日推荐 (从leader_history表)。
    
    Returns:
        {(market, cycle): [('TRL', symbol, name, score)]}
    """
    conn = get_db(write=False)
    try:
        rows = conn.execute("""
            SELECT symbol, theme_name as name, pool, tier, score 
            FROM leader_history 
            WHERE recommend_date = ? AND engine LIKE 'TRL%'
            ORDER BY score DESC
        """, (today,)).fetchall()
    finally:
        conn.close()
    
    picks = {}
    for r in rows:
        pool = r['pool']
        # pool字段格式: a_long/a_short/hk_long/hk_short
        if not pool or '_' not in pool:
            continue
        market, cycle = pool.split('_', 1)
        market = 'HK' if market == 'hk' else market.upper()
        key = (market, cycle)
        if key not in picks:
            picks[key] = []
        picks[key].append(('TRL', r['symbol'], r['name'], r['score']))
    return picks

def get_pool_changes(today: str) -> dict:
    """对比昨日vs今日推荐池，找出入池/出池标的。
    
    Returns:
        {market: {cycle: {'new': [(symbol,name,score)], 'removed': [symbol]}}}
    """
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    
    conn = get_db(write=False)
    try:
        today_pool = conn.execute("""
            SELECT market, cycle, symbol, name, score FROM recommendation_pool
            WHERE rec_date = ?
        """, (today,)).fetchall()
        
        yesterday_pool = conn.execute("""
            SELECT market, cycle, symbol FROM recommendation_pool
            WHERE rec_date = ?
        """, (yesterday,)).fetchall()
    finally:
        conn.close()
    
    today_set = {(r['market'], r['cycle'], r['symbol']) for r in today_pool}
    yesterday_set = {(r['market'], r['cycle'], r['symbol']) for r in yesterday_pool}
    
    changes = {}
    
    # 入池标的
    new_entries = today_set - yesterday_set
    for market, cycle, symbol in new_entries:
        key = (market, cycle)
        if key not in changes:
            changes[key] = {'new': [], 'removed': []}
        # Find full info
        for r in today_pool:
            if r['symbol'] == symbol and r['market'] == market:
                changes[key]['new'].append((symbol, r['name'], r['score']))
                break
    
    # 出池标的
    removed_entries = yesterday_set - today_set
    for market, cycle, symbol in removed_entries:
        key = (market, cycle)
        if key not in changes:
            changes[key] = {'new': [], 'removed': []}
        # Check if it's been out for 2+ days
        changes[key]['removed'].append(symbol)
    
    return changes


# ── 买入执行 ──────────────────────────────────────────

def execute_buy(symbol: str, name: str, portfolio_key: str, slot: int, score: float, dry_run: bool = False):
    """为标的执行slot买入。"""
    params = PORTFOLIO_PARAMS.get(tuple(portfolio_key.split('_')[:2]))
    if not params:
        return
    
    # 获取价格和ATR
    price = get_latest_price(symbol)
    if not price:
        print(f"  [{portfolio_key}] {symbol} {name}: 无价格数据，跳过")
        return
    
    atr = get_atr(symbol)
    if not atr:
        atr = price * 0.02  # 回退默认2%波动
    
    rsi = get_latest_rsi(symbol)
    
    # ATR动态仓位计算(三重约束)
    # 1. ATR风险预算: shares = slot_capital / (atr_stop * atr)
    risk_per_share = params['atr_stop'] * atr
    shares_by_risk = max(int(params['slot_capital'] / (risk_per_share * 100)) * 100, 100)
    
    # 2. 股价硬上限: 单笔成本 ≤ slot_capital (防止高价股买超)
    shares_by_price = max(int(params['slot_capital'] / price / 100) * 100, 0)
    
    # 3. 单票上限: 不超过总资金池的15% (200万×15%=30万)
    max_cost_per_symbol = 300000  # 200万×15%
    shares_by_concentration = max(int(max_cost_per_symbol / price / 100) * 100, 0)
    
    # 取三者最保守值，最少100股
    shares = max(min(shares_by_risk, shares_by_price, shares_by_concentration), 100) if shares_by_concentration > 0 else max(min(shares_by_risk, shares_by_price), 100)
    
    cost = shares * price
    # ── 共识加仓检测 (D9修复) ──
    # 若该标的同时被F3(海鹰)和TRL(龙脉)推荐 → 仓位×1.5, 上限15%
    from datetime import date as dt_date
    rec_date = dt_date.today().isoformat()
    is_consensus = False
    try:
        conn_cons = get_db(write=False)
        trl_count = conn_cons.execute("""
            SELECT COUNT(*) FROM leader_history 
            WHERE symbol=? AND recommend_date=? AND engine='TRL'
        """, (symbol, rec_date)).fetchone()[0]
        conn_cons.close()
        if trl_count > 0:
            is_consensus = True
            consensus_mult = 1.5
            shares = int(shares * consensus_mult)
            # 共识加仓后不得超过15%上限(200万×15%=30万)
            consensus_cost = shares * price
            if consensus_cost > max_cost_per_symbol:
                shares = max(int(max_cost_per_symbol / price / 100) * 100, 100)
                consensus_cost = shares * price
            print(f"  [{portfolio_key}] {symbol} {name}: ⚡共识(F3+TRL)→仓位×1.5={shares}股")
    except Exception:
        pass  # consensus check non-fatal
    
    if cost > params['slot_capital'] * 1.5:
        print(f"  [{portfolio_key}] {symbol} {name}: 成本{cost:.0f}>{params['slot_capital']*1.5:.0f}元, 缩减至slot上限")
        shares = max(int(params['slot_capital'] / price / 100) * 100, 100)
        cost = shares * price
    
    # 全局资金检查: 所有池总市值≤100万
    if not dry_run:
        conn_check = get_db(write=False)
        try:
            total_market_value = conn_check.execute(
                "SELECT COALESCE(SUM(shares * current_price), 0) FROM sim_position WHERE shares > 0"
            ).fetchone()[0]
        finally:
            conn_check.close()
        
        if total_market_value + cost > 2_000_000:
            remaining = max(2_000_000 - total_market_value, 0)
            if remaining < price * 100:
                print(f"  [{portfolio_key}] {symbol} {name}: 全局资金不足(已用{total_market_value:.0f}/200万), 跳过")
                return
            shares = max(int(remaining / price / 100) * 100, 100)
            cost = shares * price
            print(f"  [{portfolio_key}] {symbol} {name}: 资金受限→{shares}股{cost:.0f}元(剩余{remaining:.0f})")
    
    reason_code = f'SLOT{slot}_BUY'
    reason_detail = f'荐股评分{score:.1f}, ATR={atr:.2f}, RSI={rsi:.1f if rsi else 0:.1f}, Slot{slot}建仓'
    
    if dry_run:
        print(f"  [DRY-RUN] {portfolio_key} {symbol} {name}: BUY {shares}股@{price:.2f} "
              f"金额{price*shares:.0f} | {reason_code} | {reason_detail}")
        return
    
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    
    conn = get_db(write=True)
    try:
        # 写入 sim_trades
        conn.execute("""
            INSERT INTO sim_trades (trade_date, trade_time, symbol, action, slot, price, shares,
                reason_code, atr_at_entry, entry_score, rsi_at_trade, portfolio_key)
            VALUES (?,?,?,'buy',?,?,?,?,?,?,?,?)
        """, (today, now, symbol, slot, price, shares,
              reason_code, atr, score, rsi, portfolio_key))
        
        # 更新 sim_position
        existing = conn.execute("""
            SELECT shares, entry_price FROM sim_position WHERE symbol=? AND slot=?
        """, (symbol, slot)).fetchone()
        
        if existing:
            new_shares = existing['shares'] + shares
            new_avg = ((existing['entry_price'] * existing['shares']) + (price * shares)) / new_shares
            conn.execute("""
                UPDATE sim_position SET shares=?, entry_price=?, current_price=?, market_value=?,
                    updated_at=?
                WHERE symbol=? AND slot=?
            """, (new_shares, new_avg, price, price * new_shares, now, symbol, slot))
        else:
            conn.execute("""
                INSERT INTO sim_position (symbol, slot, shares, entry_price, entry_date,
                    name, current_price, market_value, weight_pct)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (symbol, slot, shares, price, today, name, price, price * shares, 0))
        
        conn.commit()
    finally:
        conn.close()
    
    # 写入 trade_log
    log_buy(symbol, name, price, shares, portfolio_key, reason_code,
            reason_detail=reason_detail, atr=atr, rsi=rsi, entry_score=score,
            trade_date=today, trade_time=now)
    
    print(f"  [{portfolio_key}] 🔵 {symbol} {name}: BUY {shares}股@{price:.2f} "
          f"金额{price*shares:.0f} | Slot{slot} | ATR={atr:.2f}")


# ── 卖出检查 ─────────────────────────────────────────

def check_and_execute_sells(portfolio_key: str, market_cycle: tuple, dry_run: bool = False):
    """检查持仓是否需要卖出(止损/止盈/时间退出/池移除)。"""
    params = PORTFOLIO_PARAMS.get(market_cycle)
    if not params:
        return
    
    conn = get_db(write=False)
    try:
        positions = conn.execute("""
            SELECT symbol, name, slot, shares, entry_price, entry_date, current_price
            FROM sim_position WHERE shares > 0
        """).fetchall()
    finally:
        conn.close()
    
    for pos in positions:
        symbol = pos['symbol']
        entry_price = pos['entry_price']
        shares = pos['shares']
        entry_date = pos['entry_date']
        
        # 检查是否属于当前portfolio
        # (简化: 所有持仓都检查，实际应区分portfolio)
        
        current_price = get_latest_price(symbol)
        if not current_price:
            continue
        
        pnl_pct = (current_price - entry_price) / entry_price
        hold_days = (date.today() - date.fromisoformat(entry_date)).days
        
        reason = None
        reason_code = None
        
        # 1. 止损
        atr = get_atr(symbol) or (entry_price * 0.02)
        stop_price = entry_price - params['atr_stop'] * atr
        if current_price <= stop_price:
            reason_code = 'STOP_LOSS'
            reason = f'止损触发: 均价{entry_price:.2f}→现价{current_price:.2f}(止损线{stop_price:.2f}, ATR={atr:.2f})'
        
        # 2. 止盈 (50%)
        elif pnl_pct >= (params['atr_target'] * atr / entry_price):
            rsi = get_latest_rsi(symbol)
            if rsi and rsi > 70:
                reason_code = 'TAKE_PROFIT'
                reason = f'止盈50%: 盈利{pnl_pct*100:.1f}%, RSI={rsi:.1f}>70'
                shares = shares // 2  # 只卖一半
        
        # 3. 时间退出
        elif hold_days > params['max_days'] and pnl_pct < 0.03:
            reason_code = 'TIME_EXIT'
            reason = f'时间退出: 持仓{hold_days}天>{params["max_days"]}天, 盈亏{pnl_pct*100:.1f}%<3%'
        
        if reason_code and not dry_run:
            execute_sell(symbol, pos['name'] or '', current_price, shares, portfolio_key,
                        reason_code, reason, pnl_pct, entry_price, current_price,
                        hold_days, params, atr, dry_run)
        elif reason_code:
            print(f"  [DRY-RUN] {portfolio_key} {symbol}: SELL原因={reason_code} | {reason}")


def execute_sell(symbol, name, price, shares, portfolio_key, reason_code, reason,
                 pnl_pct, entry_price, current_price, hold_days, params, atr, dry_run=False):
    """执行卖出交易。"""
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    
    conn = get_db(write=True)
    try:
        conn.execute("""
            INSERT INTO sim_trades (trade_date, trade_time, symbol, action, slot, price, shares,
                reason_code, atr_at_entry, rsi_at_trade, portfolio_key)
            VALUES (?,?,?,'sell',0,?,?,?,?,?,?)
        """, (today, now, symbol, price, shares,
              reason_code, atr, get_latest_rsi(symbol), portfolio_key))
        
        conn.execute("""
            UPDATE sim_position SET shares = shares - ?, current_price = ?,
                market_value = (shares - ?) * ?, updated_at = ?
            WHERE symbol = ?
        """, (shares, price, shares, price, now, symbol))
        
        conn.execute("DELETE FROM sim_position WHERE shares <= 0")
        conn.commit()
    finally:
        conn.close()
    
    pnl_amount = shares * (price - entry_price)
    
    log_sell(symbol, name, price, shares, portfolio_key, reason_code,
             pnl_pct=pnl_pct*100, pnl_amount=pnl_amount, hold_days=hold_days,
             entry_price=entry_price, current_price=current_price,
             stop_pct=params['atr_stop']*100, rsi=get_latest_rsi(symbol),
             max_days=params['max_days'], atr=atr,
             trade_date=today, trade_time=now)
    
    print(f"  [{portfolio_key}] 🔴 {symbol} {name}: SELL {shares}股@{price:.2f} "
          f"盈亏{pnl_pct*100:+.1f}% | {reason_code} | {hold_days}天")


# ── 主流程 ────────────────────────────────────────────

def main(dry_run: bool = False, slot: int = None):
    """四池交易引擎主入口。
    
    Args:
        dry_run: 仅打印不执行
        slot: 指定slot(1-3)，None=自动判断当前slot
    """
    today = date.today().isoformat()
    
    if slot is None:
        slot = get_current_slot()
    
    print(f"\n{'='*60}")
    print(f"推荐池交易引擎 | {today} | Slot {slot} ({SLOT_TIMES[slot]})")
    print(f"{'='*60}")
    
    # 市场体制过滤
    ok, reason = should_trade_today()
    if not ok:
        print(f"⛔ 不开仓: {reason}")
        return
    print(f"✅ 市场正常: {reason}")
    
    # 获取池变更
    changes = get_pool_changes(today)
    
    if not changes:
        print("📭 今日无池变更(F3)")
    else:
        total_new = sum(len(v['new']) for v in changes.values())
        total_removed = sum(len(v['removed']) for v in changes.values())
        print(f"📊 F3池变更: +{total_new}入池 / -{total_removed}出池")
    
    # ── TRL龙脉推荐 ──
    trl_picks = get_trl_picks(today)
    trl_total = sum(len(v) for v in trl_picks.values())
    if trl_total > 0:
        print(f"🐉 TRL龙脉推荐: {trl_total}只")
        for (m, c), picks in trl_picks.items():
            print(f"  {m}_{c}: {len(picks)}只")
    else:
        print(f"🐉 TRL龙脉: 今日无推荐 (无主线确认)")
    
    # 1. 先处理卖出(池移除标的)
    for (market, cycle), info in changes.items():
        portfolio_key = f"{market}_{cycle}"
        if info['removed']:
            print(f"\n🔴 {portfolio_key} 出池标的:")
            for symbol in info['removed']:
                print(f"  {symbol}: 连续2日不在池 → 全仓卖出")
                if not dry_run:
                    # Query position
                    conn = get_db(write=False)
                    try:
                        pos = conn.execute("""
                            SELECT name, shares, entry_price, entry_date, current_price
                            FROM sim_position WHERE symbol=? AND shares>0
                        """, (symbol,)).fetchone()
                    finally:
                        conn.close()
                    
                    if pos:
                        price = get_latest_price(symbol) or pos['current_price']
                        pnl = (price - pos['entry_price']) / pos['entry_price']
                        days = (date.today() - date.fromisoformat(pos['entry_date'])).days
                        params = PORTFOLIO_PARAMS.get((market, cycle), PORTFOLIO_PARAMS[('A','short')])
                        atr = get_atr(symbol)
                        execute_sell(symbol, pos['name'] or '', price, pos['shares'],
                                    portfolio_key, 'POOL_REMOVE',
                                    f'池移除: 连续2日不在推荐池', pnl,
                                    pos['entry_price'], price, days, params, atr, dry_run)
    
    # 2. 买入(入池标的)
    for (market, cycle), info in changes.items():
        portfolio_key = f"{market}_{cycle}"
        if info['new'] and slot == 1:
            # Slot 1: 所有新入池标的建立初始仓位
            print(f"\n🔵 {portfolio_key} 入池标的(Slot 1):")
            for symbol, name, score in info['new']:
                # 检查是否已持仓
                conn = get_db(write=False)
                try:
                    existing = conn.execute("""
                        SELECT COUNT(*) FROM sim_position WHERE symbol=? AND shares>0
                    """, (symbol,)).fetchone()[0]
                finally:
                    conn.close()
                
                if existing:
                    print(f"  {symbol} {name}: 已持仓,跳过")
                    continue
                
                # 检查池容量
                conn = get_db(write=False)
                try:
                    pos_count = conn.execute("""
                        SELECT COUNT(DISTINCT symbol) FROM sim_position WHERE shares>0
                    """).fetchone()[0]
                finally:
                    conn.close()
                
                params = PORTFOLIO_PARAMS.get((market, cycle), {})
                if pos_count >= params.get('max_positions', 99):
                    print(f"  {symbol} {name}: 池已满({pos_count}/{params['max_positions']}),跳过")
                    continue
                
                execute_buy(symbol, name, portfolio_key, slot, score, dry_run)
        
        elif info['new'] and slot > 1:
            # Slot 2/3: 继续补仓未满配的标的
            print(f"\n🔵 {portfolio_key} 补仓(Slot {slot}):")
            for symbol, name, score in info['new']:
                conn = get_db(write=False)
                try:
                    pos = conn.execute("""
                        SELECT shares, entry_price FROM sim_position WHERE symbol=? AND shares>0
                    """, (symbol,)).fetchone()
                finally:
                    conn.close()
                
                if not pos:
                    continue
                
                params = PORTFOLIO_PARAMS.get((market, cycle), {})
                target_shares = int(params.get('slot_capital', 50000) * 3 / 100) * 100
                
                if pos['shares'] < target_shares * 0.8:
                    execute_buy(symbol, name, portfolio_key, slot, score, dry_run)
    
    # ── TRL龙脉买入 ──
    if trl_picks and slot == 1:
        print(f"\n🐉 TRL龙脉买入(Slot 1):")
        for (market, cycle), picks in trl_picks.items():
            portfolio_key = f"{market}_{cycle}"
            for source, symbol, name, score in picks:
                # Check existing position
                conn = get_db(write=False)
                try:
                    existing = conn.execute(
                        "SELECT COUNT(*) FROM sim_position WHERE symbol=? AND shares>0",
                        (symbol,)).fetchone()[0]
                finally:
                    conn.close()
                if existing:
                    print(f"  {symbol} {name}: 已持仓,跳过")
                    continue
                execute_buy(symbol, name, portfolio_key, slot, score, dry_run)
    
    # 3. 检查存量持仓的卖出条件
    print(f"\n📋 存量持仓卖出检查:")
    for (market, cycle), _ in changes.items():
        portfolio_key = f"{market}_{cycle}"
        check_and_execute_sells(portfolio_key, (market, cycle), dry_run)
    
    # 4. 清理零持仓
    if not dry_run:
        conn = get_db(write=True)
        try:
            conn.execute("DELETE FROM sim_position WHERE shares <= 0")
            conn.commit()
        finally:
            conn.close()
    
    print(f"\n✅ {today} Slot{slot} 执行完成")
    print(f"{'='*60}\n")


# ── CLI ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="四池交易引擎")
    parser.add_argument('--dry-run', action='store_true', help="仅模拟不执行")
    parser.add_argument('--slot', type=int, choices=[1,2,3], help="指定slot(1-3)")
    args = parser.parse_args()
    
    main(dry_run=args.dry_run, slot=args.slot)
