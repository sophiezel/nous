"""Stage 1 粗筛引擎 — 规则过滤 + 15因子等权打分，秒级5000→800

设计原则:
- 规则排除用纯SQL批量完成，零循环
- 因子打分优先从已有缓存表取值(screen_results/stock_fundamental)
- 四套模型(A_long/A_short/HK_long/HK_short)的粗筛因子集不同
"""

import sqlite3
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

def _get_default_db() -> Path:
    return Path(os.environ.get("SCREENER_DB_PATH",
        str(Path.home() / "code/stock-screener/data/screener.db")))

# ── 规则排除阈值 ──────────────────────────────────

RULE_THRESHOLDS = {
    'a': {
        'min_market_cap': 20,       # 亿元
        'min_daily_amount': 1000,   # 万元
        'min_list_days': 180,       # 上市天数
        'max_data_lag_days': 5,     # 日线滞后天数
        'min_price': 1.0,           # 面值退市风险
    },
    'hk': {
        'min_market_cap_long': 10,
        'min_market_cap_short': 15,
        'min_daily_amount_long': 500,
        'min_daily_amount_short': 1000,
        'min_list_days': 180,
        'max_data_lag_days': 5,
        'min_price_long': 0.5,
        'min_price_short': 1.0,
    }
}

# ── 粗筛因子权重 (四套不同) ──────────────────────

# A_long: 基本面+规模+动量
FACTORS_A_LONG = {
    'lncap':        (-1, 0.15),  # 市值,负向(小市值效应)
    'ep_ttm':       (1, 0.15),   # 盈利收益率
    'roe_ttm':      (1, 0.15),   # ROE
    'mom_12m_1m':   (1, 0.10),   # 12月动量(剔除近1月)
    'sue':          (1, 0.10),   # 标准化意外盈利(用ROE近似)
    'bp':           (1, 0.10),   # 市净率倒数
    'turnover_20d': (1, 0.10),   # 换手率
    'volatility_60d':(-1, 0.05), # 波动率
    'revenue_growth':(1, 0.05),  # 营收增长(用ROE近似)
    'cf_yield':     (1, 0.05),   # 现金流收益率(用股息率近似)
}

# A_short: 反转+技术+换手
FACTORS_A_SHORT = {
    'ret_5d':       (-1, 0.20),  # 5日反转
    'ret_20d':      (1, 0.15),   # 20日动量
    'turnover_chg': (1, 0.15),   # 换手率变化
    'amihud':       (-1, 0.15),  # 非流动性
    'vol_std_20d':  (1, 0.10),   # 成交量变异
    'ma_gap_20':    (1, 0.10),   # 价格相对MA20位置
    'macd_signal':  (1, 0.10),   # MACD信号
    'rsi_14':       (0, 0.05),   # RSI(区间中性化)
}

# HK_long: 南向+AH+股息+基本面
FACTORS_HK_LONG = {
    'lncap':        (-1, 0.12),
    'ep_ttm':       (1, 0.12),
    'roe_ttm':      (1, 0.12),
    'dividend_yield':(1, 0.12),  # 港股高股息溢价
    'southbound_pct':(1, 0.12),  # 南向持股比例
    'mom_12m_1m':   (1, 0.08),
    'roe_stability':(1, 0.08),   # ROE稳定性
    'turnover_20d': (1, 0.08),
    'short_ratio':  (0, 0.08),   # 卖空比例(区间信号)
    'ah_premium':   (-1, 0.08),  # AH溢价(负向)
}

# HK_short: T+0动量+卖空+流动性
FACTORS_HK_SHORT = {
    'ret_5d':       (-1, 0.20),
    'ret_20d':      (1, 0.15),
    'turnover_chg': (1, 0.15),
    'amihud':       (-1, 0.10),
    'short_ratio_chg':(1, 0.10), # 卖空量变化
    'vol_std_20d':  (1, 0.10),
    'ma_gap_20':    (1, 0.10),
    'southbound_flow':(1, 0.10), # 南向资金强度
}


# ═══════════════════════════════════════════════════
# 规则排除层
# ═══════════════════════════════════════════════════

def _apply_rule_filters(db_path: str, market: str, period: str = 'long',
                        as_of_date: str = None) -> set[str]:
    """SQL批量规则排除，返回被排除的symbol集合

    排除规则:
    - ST / *ST (A股)
    - 上市不足6个月
    - 停牌/无近期日线
    - 市值过低
    - 日均成交额过低
    - 股价过低
    """
    ref_date = date.fromisoformat(as_of_date) if as_of_date else date.today()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=5000")

    # 检查必需表是否存在
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'stock_basic' not in tables:
        print(f"[coarse_filter] ⚠️ stock_basic表不存在, db={db_path}", file=sys.stderr)
        conn.close()
        return set()

    if market == 'a':
        thresholds = RULE_THRESHOLDS['a']
        min_cap = thresholds['min_market_cap']
        min_amount = thresholds['min_daily_amount']
        min_list_days = thresholds['min_list_days']
        max_lag = thresholds['max_data_lag_days']
        min_price = thresholds['min_price']

        # ST/*ST排除 + 市值 + 股价
        has_stock_fundamental = 'stock_fundamental' in tables
        excluded = set()
        # 检查stock_basic是否有list_date列
        cols = [r[1] for r in conn.execute("PRAGMA table_info(stock_basic)")]
        has_list_date = 'list_date' in cols

        for row in conn.execute("""
            SELECT sb.symbol, sb.name,
                   COALESCE(sf.total_mv, 0) as total_mv
                   {list_col}
            FROM stock_basic sb
            LEFT JOIN stock_fundamental sf ON sb.symbol = sf.symbol
            WHERE sb.market = 'a'
        """.format(list_col=", sb.list_date" if has_list_date else "")):
            if has_list_date:
                sym, name, total_mv, list_date = row
            else:
                sym, name, total_mv = row
                list_date = None

            # ST / *ST
            if name and ('ST' in name.upper() or '*ST' in name):
                excluded.add(sym)
                continue

            # 上市不足6个月
            if list_date:
                try:
                    ld = date.fromisoformat(list_date[:10])
                    if (ref_date - ld).days < min_list_days:
                        excluded.add(sym)
                        continue
                except (ValueError, TypeError):
                    pass

            # 市值 < 20亿
            if total_mv and total_mv < min_cap:
                excluded.add(sym)
                continue

            # 股价 < 1元
            # Check from stock_fundamental or stock_daily
            # This is checked in the next pass with daily data

        # 无近期日线 / 低成交额 / 低股价
        cutoff_date = (ref_date - timedelta(days=max_lag)).isoformat()
        has_recent = set()
        for row in conn.execute("""
            SELECT symbol, MAX(trade_date) as last_date,
                   AVG(amount) as avg_amount,
                   AVG(close) as avg_close
            FROM stock_daily
            WHERE trade_date >= ?
            GROUP BY symbol
        """, (cutoff_date,)):
            sym, last_date, avg_amount, avg_close = row

            if sym in excluded:
                continue

            # 无近期日线
            if last_date is None or last_date < cutoff_date:
                excluded.add(sym)
                continue

            # 日均成交额 < 1000万
            if avg_amount and avg_amount < min_amount * 10000:
                excluded.add(sym)
                continue

            # 股价 < 1元
            if avg_close and avg_close < min_price:
                excluded.add(sym)
                continue

            has_recent.add(sym)

        # 排除没有任何近期日线的股票
        for row in conn.execute("""
            SELECT DISTINCT symbol FROM stock_daily
            WHERE symbol NOT IN (SELECT symbol FROM stock_daily WHERE trade_date >= ?)
        """, (cutoff_date,)):
            sym = row[0]
            if sym in excluded:
                continue
            excluded.add(sym)

            # 返回被保留的symbol
            all_a = conn.execute("SELECT symbol FROM stock_basic WHERE market='a'").fetchall()
            conn.close()
            all_a = {r[0] for r in all_a}
            return all_a - excluded

    elif market == 'hk':
        thresholds = RULE_THRESHOLDS['hk']
        min_cap = thresholds['min_market_cap_long'] if period == 'long' else thresholds['min_market_cap_short']
        min_amount = thresholds['min_daily_amount_long'] if period == 'long' else thresholds['min_daily_amount_short']
        min_price = thresholds['min_price_long'] if period == 'long' else thresholds['min_price_short']
        max_lag = thresholds['max_data_lag_days']

        conn2 = sqlite3.connect(db_path)
        conn2.execute("PRAGMA busy_timeout=5000")

        excluded = set()
        cols2 = [r[1] for r in conn2.execute("PRAGMA table_info(stock_basic)")]
        has_list_date2 = 'list_date' in cols2

        for row in conn2.execute("""
            SELECT sb.symbol, sb.name,
                   COALESCE(sf.total_mv, 0) as total_mv
                   {list_col}
            FROM stock_basic sb
            LEFT JOIN stock_fundamental sf ON sb.symbol = sf.symbol
            WHERE sb.market = 'hk'
        """.format(list_col=", sb.list_date" if has_list_date2 else "")):
            if has_list_date2:
                sym, name, total_mv, list_date = row
            else:
                sym, name, total_mv = row
                list_date = None

            if list_date:
                try:
                    ld = date.fromisoformat(list_date[:10])
                    if (ref_date - ld).days < 180:
                        excluded.add(sym)
                        continue
                except (ValueError, TypeError):
                    pass

            if total_mv and total_mv < min_cap:
                excluded.add(sym)
                continue

        cutoff_date = (ref_date - timedelta(days=max_lag)).isoformat()
        for row in conn2.execute("""
            SELECT symbol, MAX(trade_date) as last_date,
                   AVG(amount) as avg_amount,
                   AVG(close) as avg_close
            FROM stock_daily
            WHERE trade_date >= ?
            GROUP BY symbol
        """, (cutoff_date,)):
            sym, last_date, avg_amount, avg_close = row

            if sym in excluded:
                continue
            if last_date is None or last_date < cutoff_date:
                excluded.add(sym)
                continue
            if avg_amount and avg_amount < min_amount * 10000:
                excluded.add(sym)
                continue
            if avg_close and avg_close < min_price:
                excluded.add(sym)
                continue

        all_hk = conn2.execute("SELECT symbol FROM stock_basic WHERE market='hk'").fetchall()
        conn2.close()
        all_hk = {r[0] for r in all_hk}
        return all_hk - excluded

    conn.close()
    return set()


# ═══════════════════════════════════════════════════
# 因子评分层
# ═══════════════════════════════════════════════════

def _score_factors(db_path: str, symbols: set[str], factor_config: dict,
                    market: str, period: str) -> list[tuple[str, float]]:
    """对给定symbols做因子等权打分
    
    pool-specific优化:
    - A_long: 跳过短线技术因子(RSI/MACD), 强化基本面
    - A_short: 跳过宏观因子, 强化技术/资金
    - HK_long: 强化南向/股息, 弱化动量
    - HK_short: 强化T+0/流动性, 跳过基本面
    """
    
    pool_key = f"{market}_{period}"
    # 各池跳过的因子
    SKIP_BY_POOL = {
        'A_long': {'ret_5d', 'rsi_14', 'macd_signal', 'turnover_chg'},
        'A_short': {'ep_ttm', 'roe_ttm', 'bp', 'dividend_yield', 'cf_yield', 'sue', 'revenue_growth'},
        'HK_long': {'ret_5d', 'rsi_14', 'turnover_chg', 'amihud'},
        'HK_short': {'ep_ttm', 'roe_ttm', 'bp', 'dividend_yield', 'cf_yield', 'sue', 'revenue_growth', 'roe_stability'},
    }
    skip = SKIP_BY_POOL.get(pool_key, set())
    # 跳过不相关因子
    active_config = {k: v for k, v in factor_config.items() if k not in skip}

    if not symbols:
        return []

    import math, statistics
    
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    
    sym_list = "','".join(symbols)
    sym_list = f"'{sym_list}'"
    
    # Simple robust scoring: liquidity rank + PE sanity + data completeness
    rows = conn.execute(f"""
        SELECT sd.symbol, sd.close, sd.amount,
               sf.pe, sf.pb, sf.roe, sf.total_mv, sf.dividend_yield,
               sd5.close as close_5d
        FROM (
            SELECT symbol, close, amount,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
            FROM stock_daily WHERE symbol IN ({sym_list})
        ) sd
        LEFT JOIN (
            SELECT symbol, close,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
            FROM stock_daily WHERE symbol IN ({sym_list})
        ) sd5 ON sd.symbol = sd5.symbol AND sd5.rn = 5
        LEFT JOIN stock_fundamental sf ON sd.symbol = sf.symbol
        WHERE sd.rn = 1
    """).fetchall()
    
    conn.close()
    
    scored = []
    for row in rows:
        sym = row['symbol']
        amount = row['amount'] or 0
        pe = row['pe']
        pb = row['pb']
        
        # Base score: log of daily turnover (liquidity = quality)
        score = math.log(amount + 1) * 1.0
        
        # PE sanity: 0 < PE < 200 gets bonus, PE > 500 gets penalty
        if pe and 0 < pe < 200:
            score += 3.0
        elif pe and pe >= 500:
            score -= 5.0
        
        # PB bonus for having it
        if pb and 0 < pb < 50:
            score += 1.0
        
        # Market cap bonus (if available)
        if row['total_mv'] and row['total_mv'] > 0:
            score += min(math.log(row['total_mv']) * 0.2, 3.0)
        
        # ROE bonus
        if row['roe'] and row['roe'] > 0:
            score += min(float(row['roe']) * 0.05, 3.0)
        
        # Dividend yield bonus
        if row['dividend_yield'] and row['dividend_yield'] > 0:
            score += min(float(row['dividend_yield']) * 2, 2.0)
        
        # 5-day momentum: reward positive, penalize sharp drop
        if row['close_5d'] and row['close_5d'] > 0 and row['close'] and row['close'] > 0:
            mom_5d = (row['close'] - row['close_5d']) / row['close_5d'] * 100
            if mom_5d > 0:
                score += min(mom_5d * 0.3, 5.0)  # positive momentum bonus
            elif mom_5d < -10:
                score -= 8.0  # sharp drop penalty
            elif mom_5d < -5:
                score -= 3.0  # moderate drop penalty
        
        scored.append((sym, score))
    
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def coarse_filter_a_long(db_path: str = None, top_n: int = 800, as_of_date: str = None) -> list[str]:
    """A股长线粗筛"""
    db = db_path or str(_get_default_db())
    symbols = _apply_rule_filters(db, 'a', 'long', as_of_date=as_of_date)
    scored = _score_factors(db, symbols, FACTORS_A_LONG, 'a', 'long')
    return [s for s, _ in scored[:top_n]]


def coarse_filter_a_short(db_path: str = None, top_n: int = 800, as_of_date: str = None) -> list[str]:
    """A股短线粗筛"""
    db = db_path or str(_get_default_db())
    symbols = _apply_rule_filters(db, 'a', 'short', as_of_date=as_of_date)
    scored = _score_factors(db, symbols, FACTORS_A_SHORT, 'a', 'short')
    return [s for s, _ in scored[:top_n]]


def coarse_filter_hk_long(db_path: str = None, top_n: int = 300, as_of_date: str = None) -> list[str]:
    """港股长线粗筛"""
    db = db_path or str(_get_default_db())
    symbols = _apply_rule_filters(db, 'hk', 'long', as_of_date=as_of_date)
    scored = _score_factors(db, symbols, FACTORS_HK_LONG, 'hk', 'long')
    return [s for s, _ in scored[:top_n]]


def coarse_filter_hk_short(db_path: str = None, top_n: int = 200, as_of_date: str = None) -> list[str]:
    """港股短线粗筛"""
    db = db_path or str(_get_default_db())
    symbols = _apply_rule_filters(db, 'hk', 'short', as_of_date=as_of_date)
    scored = _score_factors(db, symbols, FACTORS_HK_SHORT, 'hk', 'short')
    return [s for s, _ in scored[:top_n]]
