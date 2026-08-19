"""鳄鱼派信号引擎 — 567篇蒸馏量化实现

6个信号:
  1. 两只脚: 科技+金融联合判断
  2. 火车头: 龙头股开盘检测
  3. 拥挤度: 大额成交集中度（科技占比≥90%=极点）
  4. 主线阶段: 五阶段生命周期
  5. 资金情绪: 融资+北向（融资日减≥100亿=日K飞行员降落）
  6. 基差信号: IF/IC/IM/IH（IF点位>100或IM>300=红灯）

来源: 鳄鱼派532篇(2024.2-2026.5) + batch6 article_533-567(2026.5.27-7.15)
"""

import sys
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from nous.core.paths import screener_db

# 确保项目根目录在Python路径中
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB = screener_db()


def _pct_change(db_conn, symbol: str, trade_date: str, days: int = 5) -> Optional[float]:
    """计算N日涨跌幅"""
    rows = db_conn.execute(
        "SELECT close FROM index_daily WHERE symbol=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        (symbol, trade_date, days + 1)
    ).fetchall()
    if len(rows) < 2:
        return None
    return (rows[0][0] - rows[-1][0]) / rows[-1][0] * 100


def _get_index_close(db_conn, symbol: str, trade_date: str) -> Optional[float]:
    """获取指数收盘价"""
    row = db_conn.execute(
        "SELECT close FROM index_daily WHERE symbol=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
        (symbol, trade_date)
    ).fetchone()
    return row[0] if row else None


# ══════════════════════════════════════════════
# 信号1: 两只脚 (市场广度共振)
# ══════════════════════════════════════════════

def _calc_correlation(db_conn, sym1: str, sym2: str, trade_date: str, days: int = 10) -> float:
    """计算两个指数最近N日涨跌相关性"""
    from datetime import datetime, timedelta
    import math
    
    dt = datetime.strptime(trade_date, '%Y-%m-%d')
    start_date = (dt - timedelta(days=days * 2)).strftime('%Y-%m-%d')  # 多取一些天数
    
    rows1 = db_conn.execute(
        "SELECT trade_date, close FROM index_daily WHERE symbol=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (sym1, start_date, trade_date)
    ).fetchall()
    
    rows2 = db_conn.execute(
        "SELECT trade_date, close FROM index_daily WHERE symbol=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (sym2, start_date, trade_date)
    ).fetchall()
    
    if len(rows1) < 3 or len(rows2) < 3:
        return None
    
    # 按日期对齐
    dict1 = {r[0]: r[1] for r in rows1}
    dict2 = {r[0]: r[1] for r in rows2}
    common_dates = sorted(set(dict1.keys()) & set(dict2.keys()))
    
    if len(common_dates) < 3:
        return None
    
    # 计算日涨跌幅
    pct1 = []
    pct2 = []
    for i in range(1, len(common_dates)):
        d0, d1 = common_dates[i-1], common_dates[i]
        if dict1[d0] > 0 and dict2[d0] > 0:
            pct1.append((dict1[d1] - dict1[d0]) / dict1[d0])
            pct2.append((dict2[d1] - dict2[d0]) / dict2[d0])
    
    if len(pct1) < 3:
        return None
    
    # 计算相关系数
    n = len(pct1)
    mean1 = sum(pct1) / n
    mean2 = sum(pct2) / n
    
    cov = sum((p1 - mean1) * (p2 - mean2) for p1, p2 in zip(pct1, pct2)) / n
    std1 = math.sqrt(sum((p - mean1) ** 2 for p in pct1) / n)
    std2 = math.sqrt(sum((p - mean2) ** 2 for p in pct2) / n)
    
    if std1 == 0 or std2 == 0:
        return None
    
    return cov / (std1 * std2)


def signal_two_feet(db_conn, trade_date: str) -> dict:
    """
    两只脚: 市场广度共振
    
    鳄鱼派原文:
    - "上证是主力军(传统行业),创业板是先锋军(新兴行业)"
    - "两者分化=市场分歧,不可持续; 两者共振=单边行情持续性强"
    - "上证代表内资,恒生科技代表外资"
    
    核心逻辑: 不同维度的市场参与者是否在共振
    - 行业广度: 上证(传统) vs 创业板(新兴)
    - 资金广度: 上证(内资) vs 恒生科技(外资)
    
    返回:
      status: '强共振'/'弱共振'/'分化'/'强分化'
      score: 0-100
      sector_corr: 行业广度相关性
      capital_corr: 资金广度相关性
    """
    # 行业广度: 上证 vs 创业板
    sector_corr = _calc_correlation(db_conn, 'IDX_000001', 'IDX_399006', trade_date, 10)
    
    # 资金广度: 上证 vs 恒生科技(如果有的话)
    capital_corr = _calc_correlation(db_conn, 'IDX_000001', 'IDX_HSI', trade_date, 10)
    
    # 综合判断
    corrs = [c for c in [sector_corr, capital_corr] if c is not None]
    
    if not corrs:
        return {'status': '数据不足', 'score': 50, 
                'sector_corr': sector_corr, 'capital_corr': capital_corr}
    
    avg_corr = sum(corrs) / len(corrs)
    
    if avg_corr > 0.7:
        status = '强共振'
        score = 100
    elif avg_corr > 0.3:
        status = '弱共振'
        score = 70
    elif avg_corr > -0.3:
        status = '分化'
        score = 40
    else:
        status = '强分化'
        score = 10
    
    return {
        'status': status, 'score': score,
        'sector_corr': round(sector_corr, 3) if sector_corr else None,
        'capital_corr': round(capital_corr, 3) if capital_corr else None,
    }


# ══════════════════════════════════════════════
# 信号2: 火车头检测
# ══════════════════════════════════════════════

def signal_locomotive(db_conn, trade_date: str) -> dict:
    """
    火车头检测 v2 — 接入概念板块
    
    逻辑:
    1. 获取当前主线概念(涨幅最高的概念板块)
    2. 取该概念的龙头股(最近5天涨幅TOP3)
    3. 检查龙头股的开盘表现
    
    返回:
      status: '正常带'/'低开预警'/'高开加速'/'无主线'
      stocks: [{'symbol': ..., 'name': ..., 'open_pct': ...}]
      theme: 主线概念名
    """
    # 获取主线概念
    try:
        from nous.engine.signals.concept_signals import identify_mainline
        mainline = identify_mainline(db_conn, trade_date)
        
        if not mainline.get('mainline'):
            return {'status': '无主线', 'stocks': [], 'theme': None, 'score': 50}
        
        concept_name = mainline['mainline']

        # 窄窗分区（避免扫 stock_daily_all 全历史）
        from nous.data.storage.daily_bars import (
            approx_start_for_lookback,
            daily_relation_sql,
        )
        _rel = daily_relation_sql(
            approx_start_for_lookback(trade_date, 20),
            trade_date,
            conn=db_conn,
        )
        
        # 获取龙头股(最近5个交易日涨幅TOP3)
        # 找到最近5个交易日(而不是精确5天前)
        prev_dates = db_conn.execute(
            f"SELECT DISTINCT trade_date FROM {_rel} WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 5",
            (trade_date,)
        ).fetchall()
        
        if not prev_dates:
            return {'status': '无主线', 'stocks': [], 'theme': concept_name, 'score': 50}
        
        prev_date = prev_dates[-1][0]  # 最远的那个交易日
        
        leaders = db_conn.execute(f"""
            SELECT t.symbol, sb.name,
                   (t.close - p.close) / p.close * 100 as pct_5d
            FROM stock_concept_map scm
            JOIN {_rel} t ON t.symbol = scm.symbol
            JOIN {_rel} p ON p.symbol = scm.symbol
            JOIN stock_basic sb ON sb.symbol = scm.symbol
            WHERE scm.concept_name = ?
            AND t.trade_date = ? AND p.trade_date = ?
            AND t.close > 0 AND p.close > 0
            ORDER BY pct_5d DESC
            LIMIT 3
        """, (concept_name, trade_date, prev_date)).fetchall()
        
        if not leaders:
            return {'status': '无主线', 'stocks': [], 'theme': concept_name, 'score': 50}
        
        # 检查开盘表现
        opening = []
        for sym, name, _ in leaders:
            row = db_conn.execute(f"""
                SELECT t.open, p.close
                FROM {_rel} t
                JOIN {_rel} p ON p.symbol = t.symbol
                WHERE t.symbol = ? AND t.trade_date = ?
                AND p.trade_date < t.trade_date
                ORDER BY p.trade_date DESC
                LIMIT 1
            """, (sym, trade_date)).fetchone()
            
            if row and row[0] and row[1] and row[1] > 0:
                open_pct = (row[0] - row[1]) / row[1] * 100
                opening.append({
                    'symbol': sym,
                    'name': name,
                    'open_pct': round(open_pct, 2)
                })
        
        if not opening:
            return {'status': '数据不足', 'stocks': [], 'theme': concept_name, 'score': 50}
        
        # 计算平均开盘涨幅
        avg_open_pct = sum(s['open_pct'] for s in opening) / len(opening)
        
        # 判断状态
        if avg_open_pct < -1.5:
            status = '低开预警'
            score = 20
        elif avg_open_pct > 2.0:
            status = '高开加速'
            score = 70
        else:
            status = '正常带'
            score = 80
        
        return {
            'status': status,
            'stocks': opening,
            'theme': concept_name,
            'avg_open_pct': round(avg_open_pct, 2),
            'score': score,
        }
        
    except Exception as e:
        return {'status': '无主线', 'stocks': [], 'theme': None, 'score': 50}


# ══════════════════════════════════════════════
# 信号3: 拥挤度
# ══════════════════════════════════════════════

def signal_crowding(db_conn, trade_date: str) -> dict:
    """
    成交额>10亿的个股中,科技板块占比
    科技=电子/计算机/通信/传媒/电力设备(申万一级)
    
    返回:
      pct: 科技占比百分比
      level: '健康'/'预警'/'极度拥挤'
      score: 0-100 (拥挤度越高分越低)
      total: 成交额>10亿的总数
      tech_count: 其中科技股数
    """
    # 成交额>10亿的个股（单日走年分表路由）
    from nous.data.storage.daily_bars import daily_table_for
    _tbl = daily_table_for(trade_date)
    try:
        big = db_conn.execute(f"""
            SELECT sd.symbol, si.industry_name
            FROM {_tbl} sd
            LEFT JOIN stock_industry si ON si.symbol = sd.symbol OR si.symbol = 'sh' || sd.symbol OR si.symbol = 'sz' || sd.symbol
            WHERE sd.trade_date = ? AND sd.amount > 1000000
        """, (trade_date,)).fetchall()
    except Exception:
        big = db_conn.execute("""
            SELECT sd.symbol, si.industry_name
            FROM stock_daily_all sd
            LEFT JOIN stock_industry si ON si.symbol = sd.symbol OR si.symbol = 'sh' || sd.symbol OR si.symbol = 'sz' || sd.symbol
            WHERE sd.trade_date = ? AND sd.amount > 1000000
        """, (trade_date,)).fetchall()
    
    if not big:
        return {'pct': 0, 'level': '数据不足', 'score': 50, 'total': 0, 'tech_count': 0}
    
    tech_industries = {'电子', '计算机', '通信', '传媒', '电力设备', '半导体', '软件'}
    tech_count = sum(1 for _, ind in big if ind and any(t in ind for t in tech_industries))
    total = len(big)
    pct = tech_count / total * 100 if total > 0 else 0
    
    if pct > 90:
        level = '拥挤极点'
        score = 5
    elif pct > 85:
        level = '极度拥挤'
        score = 10
    elif pct > 60:
        level = '预警'
        score = 40
    else:
        level = '健康'
        score = 80
    
    return {'pct': round(pct, 1), 'level': level, 'score': score, 'total': total, 'tech_count': tech_count}


# ══════════════════════════════════════════════
# 信号4: 主线阶段判断(概念板块版)
# ══════════════════════════════════════════════

def signal_mainline_stage(db_conn, trade_date: str) -> dict:
    """
    基于概念板块判断主线在哪个阶段
    
    优先用概念板块(如AI光模块、电力),降级用L2行业
    
    五阶段: 苗头期→确立期→加速期→分歧期→退潮期
    """
    # 优先用概念板块
    try:
        from nous.engine.signals.concept_signals import identify_mainline
        concept_result = identify_mainline(db_conn, trade_date)
        
        if concept_result.get('mainline'):
            pct = concept_result['pct']
            momentum = concept_result['momentum']
            score = concept_result['score']
            
            # 判断阶段
            if pct < 2:
                stage = '苗头期'
                stage_score = 60
            elif pct < 5:
                stage = '确立期'
                stage_score = 90
            elif pct < 10:
                stage = '加速期'
                stage_score = 80
            elif momentum == '减速':
                stage = '分歧期'
                stage_score = 50
            else:
                stage = '加速期'
                stage_score = 70
            
            return {
                'stage': stage,
                'theme': concept_result['mainline'],
                'theme_score': score,
                'score': stage_score,
                'source': 'concept',
                'pct': pct,
                'momentum': momentum,
            }
    except Exception:
        pass
    
    # 降级: 用L2行业主题
    rows = db_conn.execute(
        "SELECT theme_name, total_score, category, level FROM theme_scores WHERE theme_date<=? AND level='L2' AND theme_name NOT LIKE '%chaos%' AND total_score < 10000 ORDER BY theme_date DESC, total_score DESC LIMIT 5",
        (trade_date,)
    ).fetchall()
    
    if not rows:
        rows = db_conn.execute(
            "SELECT theme_name, total_score, category, level FROM theme_scores WHERE theme_date<=? AND level='L1' AND theme_name NOT LIKE '%chaos%' AND total_score < 10000 ORDER BY theme_date DESC, total_score DESC LIMIT 5",
            (trade_date,)
        ).fetchall()
    
    if not rows:
        return {'stage': '无数据', 'theme': None, 'theme_score': 0, 'score': 50}
    
    current_theme = rows[0][0]
    current_score = rows[0][1] or 0
    
    # 获取前一日同主题评分
    prev_score = None
    for r in rows[1:]:
        if r[0] == current_theme:
            prev_score = r[1]
            break
    
    if current_score < 40:
        stage = '苗头期'
        score = 60
    elif current_score < 60:
        stage = '确立期'
        score = 90
    elif current_score < 80:
        stage = '加速期'
        score = 80
    elif prev_score and current_score < prev_score:
        stage = '分歧期'
        score = 50
    elif current_score >= 80:
        stage = '加速期'
        score = 70
    else:
        stage = '确立期'
        score = 75
    
    return {'stage': stage, 'theme': current_theme, 'theme_score': round(current_score, 1), 'score': score}


# ══════════════════════════════════════════════
# 信号5: 资金情绪
# ══════════════════════════════════════════════

def signal_capital_flow(db_conn, trade_date: str) -> dict:
    """
    融资余额变化 + 北向资金净买入
    
    返回:
      margin_5d_change: 融资余额5日变化(亿)
      northbound_net: 北向当日净买入(亿)
      signal: '杠杆看多'/'杠杆撤退'/'外资看多'/'外资撤退'/'中性'
      score: 0-100
    """
    # 融资余额
    margin = db_conn.execute(
        "SELECT margin_balance FROM margin_daily WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 6",
        (trade_date,)
    ).fetchall()
    
    margin_5d = None
    if len(margin) >= 2:
        margin_5d = (margin[0][0] - margin[-1][0]) / 1e8 if margin[0][0] and margin[-1][0] else None
    
    # 北向资金
    north = db_conn.execute(
        "SELECT SUM(estimated_net_buy) FROM hsgt_stock_daily WHERE trade_date=? AND direction='北向'",
        (trade_date,)
    ).fetchone()
    northbound_net = north[0] / 1e8 if north and north[0] else None
    
    # 综合判断
    signals = []
    score = 50
    
    if margin_5d is not None:
        if margin_5d > 50:
            signals.append('杠杆看多')
            score += 15
        elif margin_5d < -50:
            signals.append('杠杆撤退')
            score -= 15
    
    if northbound_net is not None:
        if northbound_net > 50:
            signals.append('外资看多')
            score += 15
        elif northbound_net < -50:
            signals.append('外资撤退')
            score -= 15
    
    if not signals:
        signal = '中性'
    else:
        signal = '+'.join(signals)
    
    return {
        'margin_5d_change': round(margin_5d, 2) if margin_5d else None,
        'northbound_net': round(northbound_net, 2) if northbound_net else None,
        'signal': signal,
        'score': max(0, min(100, score))
    }


# ══════════════════════════════════════════════
# 信号6: 基差信号
# ══════════════════════════════════════════════

def signal_basis(db_conn, trade_date: str) -> dict:
    """
    IF/IC/IM/IH基差矩阵
    基差收敛=情绪好转, 基差扩大=情绪恶化
    
    返回:
      if_basis: IF基差率
      signal: '收敛做多'/'扩大做空'/'数据缺失'
      score: 0-100
    """
    rows = db_conn.execute(
        "SELECT symbol, basis_rate FROM futures_basis WHERE trade_date=?",
        (trade_date,)
    ).fetchall()
    
    if not rows:
        return {'if_basis': None, 'signal': '数据缺失', 'score': 50}
    
    basis_map = {r[0]: r[1] for r in rows}
    if_basis = basis_map.get('IF0') or basis_map.get('IF')
    
    if if_basis is None:
        return {'if_basis': None, 'signal': '数据缺失', 'score': 50}
    
    if if_basis > 0.5:
        signal = '升水做多'
        score = 80
    elif if_basis < -0.5:
        signal = '贴水做空'
        score = 20
    else:
        signal = '中性'
        score = 50
    
    return {'if_basis': round(if_basis, 3), 'signal': signal, 'score': score}


# ══════════════════════════════════════════════
# 综合信号
# ══════════════════════════════════════════════

def evaluate_crocodile_signals(db_conn, trade_date: str) -> dict:
    """
    汇总6个信号, 输出综合评分
    
    权重:
      两只脚: 25% (最重要的盘面信号)
      火车头: 20% (主线健康度)
      拥挤度: 15% (安全信号)
      主线阶段: 20% (方向判断)
      资金情绪: 10% (辅助验证)
      基差信号: 10% (辅助验证)
    
    返回:
      total_score: 0-100 综合评分
      signals: 各信号详情
      verdict: 鳄鱼派一句话判断
    """
    signals = {
        'two_feet': signal_two_feet(db_conn, trade_date),
        'locomotive': signal_locomotive(db_conn, trade_date),
        'crowding': signal_crowding(db_conn, trade_date),
        'mainline': signal_mainline_stage(db_conn, trade_date),
        'capital': signal_capital_flow(db_conn, trade_date),
        'basis': signal_basis(db_conn, trade_date),
    }
    
    weights = {
        'two_feet': 0.25,
        'locomotive': 0.20,
        'crowding': 0.15,
        'mainline': 0.20,
        'capital': 0.10,
        'basis': 0.10,
    }
    
    total = sum(signals[k]['score'] * weights[k] for k in weights)
    total = round(total, 1)
    
    # 鳄鱼派一句话判断
    if total >= 80:
        verdict = '信号没问题，可以干活'
    elif total >= 60:
        verdict = '信号一般，谨慎参与'
    elif total >= 40:
        verdict = '信号较差，观望为主'
    else:
        verdict = '信号很差，休息'
    
    # 安全信号优先
    if signals['crowding']['level'] == '拥挤极点':
        verdict = '拥挤极点，边打边撤，忌追科技'
    elif signals['crowding']['level'] == '极度拥挤':
        verdict = '拥挤度极高，边打边撤'
    if signals['mainline']['stage'] == '退潮期':
        verdict = '主线退潮，清仓离场'
    if signals['locomotive']['status'] == '低开预警':
        verdict = '火车头低开，立即减仓'
    # batch6: 基差红灯（若库内存点位绝对值）
    if_b = signals['basis'].get('if_basis')
    if if_b is not None and abs(if_b) > 100:
        verdict = '基差红灯，飞行员警戒，减仓观望'
    
    return {
        'total_score': total,
        'signals': signals,
        'verdict': verdict,
        'trade_date': trade_date,
    }


if __name__ == '__main__':
    import sys
    td = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    conn = sqlite3.connect(str(DB))
    result = evaluate_crocodile_signals(conn, td)
    conn.close()
    
    print(f"=== 鳄鱼派信号 {td} ===")
    print(f"综合评分: {result['total_score']}/100")
    print(f"判断: {result['verdict']}")
    print()
    for name, sig in result['signals'].items():
        status = sig.get('status', sig.get('signal', sig.get('stage', sig.get('level', '?'))))
        print(f"  {name}: {status} ({sig['score']}分)")
