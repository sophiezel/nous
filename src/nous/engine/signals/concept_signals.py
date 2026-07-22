"""概念板块信号引擎 — 识别跨行业市场主题

基于stock_concept_map表,计算各概念板块的涨幅和动量
用于识别当前市场主线(如AI光模块、电力、新能源车等)
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"


def get_concept_performance(db_conn, trade_date: str, days: int = 5) -> list:
    """计算各概念板块最近N天的涨幅"""
    dt = datetime.strptime(trade_date, '%Y-%m-%d')
    
    # 找到最近N个交易日(而不是精确N天前)
    prev_dates = db_conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date < ? ORDER BY trade_date DESC LIMIT ?",
        (trade_date, days)
    ).fetchall()
    
    if not prev_dates:
        return []
    
    prev_date = prev_dates[-1][0]  # 最远的那个交易日
    
    rows = db_conn.execute("""
        SELECT scm.concept_name,
               AVG((t.close - p.close) / p.close * 100) as avg_pct,
               COUNT(DISTINCT t.symbol) as stock_count
        FROM stock_concept_map scm
        JOIN stock_daily t ON t.symbol = scm.symbol
        JOIN stock_daily p ON p.symbol = scm.symbol
        WHERE t.trade_date = ? AND p.trade_date = ?
        AND t.close > 0 AND p.close > 0
        GROUP BY scm.concept_name
        HAVING stock_count >= 5
        ORDER BY avg_pct DESC
    """, (trade_date, prev_date)).fetchall()
    
    return [(r[0], round(r[1], 2), r[2]) for r in rows]


def get_concept_momentum(db_conn, concept_name: str, trade_date: str) -> dict:
    """计算概念板块的动量(最近5天vs最近10天)"""
    dt = datetime.strptime(trade_date, '%Y-%m-%d')
    
    # 最近5天涨幅
    d5 = (dt - timedelta(days=5)).strftime('%Y-%m-%d')
    r5 = db_conn.execute("""
        SELECT AVG((t.close - p.close) / p.close * 100)
        FROM stock_concept_map scm
        JOIN stock_daily t ON t.symbol = scm.symbol
        JOIN stock_daily p ON p.symbol = scm.symbol
        WHERE scm.concept_name = ?
        AND t.trade_date = ? AND p.trade_date = ?
        AND t.close > 0 AND p.close > 0
    """, (concept_name, trade_date, d5)).fetchone()
    
    # 最近10天涨幅
    d10 = (dt - timedelta(days=10)).strftime('%Y-%m-%d')
    r10 = db_conn.execute("""
        SELECT AVG((t.close - p.close) / p.close * 100)
        FROM stock_concept_map scm
        JOIN stock_daily t ON t.symbol = scm.symbol
        JOIN stock_daily p ON p.symbol = scm.symbol
        WHERE scm.concept_name = ?
        AND t.trade_date = ? AND p.trade_date = ?
        AND t.close > 0 AND p.close > 0
    """, (concept_name, trade_date, d10)).fetchone()
    
    pct_5d = r5[0] if r5 and r5[0] else 0
    pct_10d = r10[0] if r10 and r10[0] else 0
    
    # 动量判断: 5天涨幅>10天涨幅 = 加速
    if pct_5d > pct_10d * 1.2:
        momentum = '加速'
    elif pct_5d < pct_10d * 0.8:
        momentum = '减速'
    else:
        momentum = '平稳'
    
    return {
        'pct_5d': round(pct_5d, 2),
        'pct_10d': round(pct_10d, 2),
        'momentum': momentum,
    }


def identify_mainline(db_conn, trade_date: str) -> dict:
    """识别当前市场主线
    
    返回:
      mainline: 主线概念名
      pct: 涨幅
      momentum: 动量(加速/减速/平稳)
      stock_count: 成分股数量
      score: 主线强度评分(0-100)
    """
    concepts = get_concept_performance(db_conn, trade_date, 5)
    
    if not concepts:
        return {'mainline': None, 'pct': 0, 'momentum': '无数据', 'stock_count': 0, 'score': 0}
    
    # 取涨幅最高的概念
    top_concept = concepts[0]
    concept_name, pct, stock_count = top_concept
    
    # 计算动量
    momentum_data = get_concept_momentum(db_conn, concept_name, trade_date)
    
    # 计算主线强度评分
    # 涨幅权重60%, 动量权重40%
    pct_score = min(100, max(0, pct * 5))  # 涨幅20%=100分
    momentum_score = 100 if momentum_data['momentum'] == '加速' else (50 if momentum_data['momentum'] == '平稳' else 20)
    
    score = pct_score * 0.6 + momentum_score * 0.4
    
    return {
        'mainline': concept_name,
        'pct': pct,
        'momentum': momentum_data['momentum'],
        'stock_count': stock_count,
        'score': round(score, 1),
        'all_concepts': concepts[:5],  # TOP5概念
    }


if __name__ == "__main__":
    import sys
    td = sys.argv[1] if len(sys.argv) > 1 else '2026-05-27'
    
    conn = sqlite3.connect(str(DB))
    result = identify_mainline(conn, td)
    conn.close()
    
    print(f"=== 市场主线 {td} ===")
    print(f"主线: {result['mainline']}")
    print(f"涨幅: {result['pct']:+.2f}%")
    print(f"动量: {result['momentum']}")
    print(f"成分股: {result['stock_count']} 只")
    print(f"强度: {result['score']}/100")
    
    if result.get('all_concepts'):
        print(f"\nTOP5概念:")
        for name, pct, count in result['all_concepts']:
            print(f"  {name}: {pct:+.2f}% ({count}只)")
