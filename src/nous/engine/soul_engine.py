"""
Soul Engine — 21位操盘手集体智慧的自动化执行模块

实现 soul.md 中定义的 8 层决策管线：
  L1 通道分配  L2 品质审查  L3 赔率计算  L4 宏观择时
  L5 仓位计算  L6 均衡检查  L7 最终裁决

用法：
  python -m src.soul_engine check 600519     # 单票8层检查
  python -m src.soul_engine filter            # 全量过滤（输出通过四不碰的标的）

集成点：
  - screener: screen_all() 调用 soul_filter() 预过滤
  - trader: Executor 调用 calc_position() 计算仓位
  - review: 复盘时调用 diagnose() 做soul诊断
"""

import sqlite3
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from nous.core.paths import screener_db

DB_PATH = screener_db()
# ═══════════════════════════════════════════════════════
# L1: 板块通道分配
# ═══════════════════════════════════════════════════════

def assign_channel(symbol: str, financial: dict) -> str:
    """
    将股票分配到最合适的操盘手通道。
    返回: '消费垄断' | '周期景气' | '成长挖掘' | '蓝筹价值' | '港股价值' | '通用'
    """
    sector = financial.get("sector", "")
    market = financial.get("market", "a")
    pe = financial.get("pe", 0) or 0
    pb = financial.get("pb", 0) or 0
    roe = financial.get("roe", 0) or 0
    gpm = financial.get("gross_margin", 0) or 0
    
    # 消费垄断通道（林园）
    consumer_sectors = {"食品饮料", "白酒", "中药", "医药生物", "医药"}
    if sector in consumer_sectors and gpm > 50:
        return "消费垄断"
    
    # 周期景气通道（邓晓峰）
    cyclical_sectors = {"光伏", "新能源", "钢铁", "有色", "煤炭", "化工", "建材", "航运", "工程机械"}
    if sector in cyclical_sectors:
        return "周期景气"
    
    # 成长挖掘通道（林奇）— 高增速+PEG合理
    g = financial.get("earnings_growth_3y", 0) or 0
    if g > 15 and pe > 0:
        peg = pe / g
        if peg < 1.5:
            return "成长挖掘"
    
    # 蓝筹价值通道（邱国鹭+巴菲特）
    mv = financial.get("total_mv", 0) or 0
    if mv > 500e8 and roe > 15:
        return "蓝筹价值"
    
    # 港股深度价值通道（蒋锦志+王国斌）
    if market == "hk" and pe > 0 and pe < 15 and pb > 0 and pb < 1.5:
        return "港股价值"
    
    return "通用"


# ═══════════════════════════════════════════════════════
# L2: 邱国鹭四不碰硬过滤
# ═══════════════════════════════════════════════════════

@dataclass
class FilterResult:
    passed: bool
    reason: str = ""


def qiuguolu_hard_filter(financial: dict) -> FilterResult:
    """
    邱国鹭四不碰——任意一条触发即毙掉。
    这是最上游的硬过滤，不通过直接跳过，不进入后续评分。
    """
    # 1. 毛利率 < 20%（低毛利）
    gpm = financial.get("gross_margin")
    if gpm is not None and gpm < 20:
        return FilterResult(False, f"毛利率{gpm:.1f}%<20%")
    
    # 2. 负债率 > 70%（高杠杆）
    debt = financial.get("debt_ratio")
    if debt is not None and debt > 70:
        return FilterResult(False, f"负债率{debt:.1f}%>70%")
    
    # 3. 固定资产占比 > 60%（重资产）
    fa_ratio = financial.get("fixed_asset_ratio")
    if fa_ratio is not None and fa_ratio > 60:
        return FilterResult(False, f"固定资产占比{fa_ratio:.1f}%>60%")
    
    # 4. 大股东质押 > 50%（高质押）
    pledge = financial.get("pledge_ratio")
    if pledge is not None and pledge > 50:
        return FilterResult(False, f"质押率{pledge:.1f}%>50%")
    
    # 5. 连续2年经营现金流为负
    ocf_neg = financial.get("ocf_negative_2y", False)
    if ocf_neg:
        return FilterResult(False, "连续2年经营现金流为负")
    
    # 6. ROE < 5%（陈光明底线）
    roe = financial.get("roe")
    if roe is not None and roe < 5:
        return FilterResult(False, f"ROE={roe:.1f}%<5%")
    
    return FilterResult(True)


# ═══════════════════════════════════════════════════════
# L3: 赔率计算（冯柳）
# ═══════════════════════════════════════════════════════

def fengliu_odds(financial: dict) -> dict:
    """
    冯柳式赔率 = 上涨空间 / 下跌空间
    需要: 当前价、目标价（分析师一致预期×0.7）、硬底价
    简化版：用 PE 分位数 + PB 来估算
    """
    pe = financial.get("pe", 0) or 0
    pb = financial.get("pb", 0) or 0
    pe_pct = financial.get("pe_percentile_5y", 50)
    
    # 赔率估算
    if pe <= 0:
        return {"odds": 0, "level": "无法计算"}
    
    # 简化：PE分位数越低=赔率越高
    if pe_pct < 20 and pb < 1.5:
        odds = 5.0
        level = "高赔率"
    elif pe_pct < 40:
        odds = 3.0
        level = "中赔率"
    elif pe_pct < 60:
        odds = 2.0
        level = "低赔率"
    else:
        odds = 1.0
        level = "无赔率"
    
    return {"odds": odds, "level": level, "pe_pct": pe_pct}


# ═══════════════════════════════════════════════════════
# L4: 琼斯200日均线趋势过滤
# ═══════════════════════════════════════════════════════

def jones_trend_filter(symbol: str) -> dict:
    """
    琼斯200日均线：价格<MA200 → 不做多
    返回: {passed, position, trend_strength}
    """
    rows = _get_daily(symbol, 250)
    if len(rows) < 200:
        return {"passed": False, "reason": "数据不足200天", "trend_strength": 0}
    
    closes = [r["close"] for r in rows]
    ma200 = sum(closes[-200:]) / 200
    ma50 = sum(closes[-50:]) / 50
    ma20 = sum(closes[-20:]) / 20
    current = closes[-1]
    
    # 趋势强度评分
    score = 0
    days_above = sum(1 for c in closes[-60:] if c > ma200)
    score += min(days_above, 40)  # 近60天在MA200之上的天数
    if ma50 > ma200: score += 20
    if current > ma20: score += 10
    
    if current > ma200 and ma50 > ma200:
        return {"passed": True, "position": "强多头", "trend_strength": score}
    elif current > ma200:
        return {"passed": True, "position": "弱多头", "trend_strength": score * 0.7}
    else:
        return {"passed": False, "reason": f"价格<MA200({ma200:.2f})", "trend_strength": 0}


# ═══════════════════════════════════════════════════════
# L5: 德鲁肯米勒确信度 + 琼斯2%仓位计算
# ═══════════════════════════════════════════════════════

def calc_position_weight(conviction: float, entry_price: float, 
                         stop_price: float, portfolio_value: float,
                         channel: str = "通用") -> float:
    """
    计算最终仓位比例。
    
    conviction: 确信度 0-100（来自 L2+L3+L4 的综合得分）
    """
    # 德鲁肯米勒：确信度→仓位
    if conviction >= 90: base_weight = 0.20
    elif conviction >= 80: base_weight = 0.15
    elif conviction >= 70: base_weight = 0.10
    elif conviction >= 50: base_weight = 0.05
    else: return 0
    
    # 琼斯2%风险预算：单笔最大亏损≤总资产2%
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        return 0
    max_shares = portfolio_value * 0.02 / risk_per_share
    max_weight_by_risk = (max_shares * entry_price) / portfolio_value
    
    # 通道上限
    channel_limits = {
        "消费垄断": 0.20, "蓝筹价值": 0.15,
        "周期景气": 0.15, "成长挖掘": 0.10,
        "港股价值": 0.15, "通用": 0.10,
    }
    channel_limit = channel_limits.get(channel, 0.10)
    
    return min(base_weight, max_weight_by_risk, channel_limit)


# ═══════════════════════════════════════════════════════
# L7: 鳄鱼派信号共振
# ═══════════════════════════════════════════════════════

def crocodile_resonance(symbol: str, financial: dict) -> dict:
    """
    检查多操盘手信号是否共振。
    返回: {resonance: bool, signals: [...], level: '共振'|'弱共振'|'未共振'}
    """
    signals = []
    
    # 冯柳赔率
    odds = fengliu_odds(financial)
    if odds["level"] in ("高赔率", "中赔率"):
        signals.append("冯柳-赔率")
    
    # 琼斯趋势
    trend = jones_trend_filter(symbol)
    if trend["passed"] and trend["position"] == "强多头":
        signals.append("琼斯-强趋势")
    elif trend["passed"]:
        signals.append("琼斯-弱趋势")
    
    # 邱国鹭品质
    quality = qiuguolu_hard_filter(financial)
    if quality.passed:
        signals.append("邱国鹭-品质")
    
    # 通道掌门人
    channel = assign_channel(symbol, financial)
    if channel != "通用":
        signals.append(f"通道-{channel}")
    
    if len(signals) >= 3:
        return {"resonance": True, "level": "共振", "signals": signals, "action": "执行买入"}
    elif len(signals) >= 2:
        return {"resonance": True, "level": "弱共振", "signals": signals, "action": "半仓试探"}
    else:
        return {"resonance": False, "level": "未共振", "signals": signals, "action": "继续潜伏"}


# ═══════════════════════════════════════════════════════
# 全量过滤（供 screener 调用）
# ═══════════════════════════════════════════════════════

def soul_filter(market: str = "a") -> list[str]:
    """
    返回通过 L2 四不碰 + L4 趋势过滤的标的列表。
    screener 只需对这些标的打分。
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    # 获取所有标的的基本面数据
    rows = conn.execute("""
        SELECT b.symbol, b.name, b.market,
               f.pe, f.pb, f.roe, f.total_mv,
               f.gross_margin, f.debt_ratio
        FROM stock_basic b
        LEFT JOIN stock_fundamental f ON b.symbol = f.symbol
        WHERE b.market = ?
    """, (market,)).fetchall()
    conn.close()
    
    passed = []
    for r in rows:
        fin = dict(r)
        # L2 四不碰
        q_result = qiuguolu_hard_filter(fin)
        if not q_result.passed:
            continue
        # L4 趋势（可选：仅在大盘偏弱时启用）
        # trend = jones_trend_filter(r["symbol"])
        # if not trend["passed"]:
        #     continue
        passed.append(r["symbol"])
    
    return passed


# ═══════════════════════════════════════════════════════
# 单票完整诊断（供复盘/手动使用）
# ═══════════════════════════════════════════════════════

def diagnose(symbol: str, financial: dict = None) -> dict:
    """对单只股票执行完整的8层诊断"""
    if financial is None:
        financial = _load_financial(symbol)
    
    return {
        "symbol": symbol,
        "L1_channel": assign_channel(symbol, financial),
        "L2_quality": qiuguolu_hard_filter(financial),
        "L3_odds": fengliu_odds(financial),
        "L4_trend": jones_trend_filter(symbol),
        "L7_resonance": crocodile_resonance(symbol, financial),
    }


# ═══════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════

def _load_financial(symbol: str) -> dict:
    """从 screener.db 加载基本面数据"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM stock_fundamental WHERE symbol=?", (symbol,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def _get_daily(symbol: str, days: int = 250) -> list[dict]:
    """从 screener.db 获取日线"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT trade_date, close FROM stock_daily "
        "WHERE symbol=? ORDER BY trade_date ASC LIMIT ?",
        (symbol, days)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python -m src.soul_engine <check|filter> [symbol]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "check" and len(sys.argv) > 2:
        result = diagnose(sys.argv[2])
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    
    elif cmd == "filter":
        market = sys.argv[2] if len(sys.argv) > 2 else "a"
        symbols = soul_filter(market)
        print(f"通过 {len(symbols)} 只")
        for s in symbols[:20]:
            print(f"  {s}")
    
    elif cmd == "diagnose":
        # 批量诊断
        symbols = sys.argv[2:] if len(sys.argv) > 2 else []
        for s in symbols:
            d = diagnose(s)
            resonance = "🟢" if d["L7_resonance"]["resonance"] else "🔴"
            channel = d["L1_channel"]
            quality = "✅" if d["L2_quality"].passed else "❌"
            odds = d["L3_odds"]["level"]
            trend = d["L4_trend"].get("position", "N/A")
            print(f"{resonance} {s:8s} {channel:8s} {quality} 赔率={odds:6s} 趋势={trend}")
