"""
行业集中度 + 流动性风险
========================
- 行业集中度: 检测单行业持仓占比是否超过 30%
- 流动性风险: 检测持仓占日均成交量比例是否超过 5%

数值单位: 人民币
"""

import sqlite3
from pathlib import Path
from typing import Dict, List, Any

DB = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"


# ===================================================================
# 行业集中度
# ===================================================================

# 基于 stock_daily.amount 的行业近似映射 (简化版)
# key = symbol 前几位代码 / 规则, value = 行业名
# 在实际生产环境中, 应从 stock_basic 或外部行业表获取
_SECTOR_MAP = {}  # 占位 — 可由外部数据源填充


def _fetch_symbol_sector(symbol: str) -> str:
    """
    获取单只股票的行业归属
    当前为简化实现: 通过 stock_basic 的 name 关键词粗略判断

    TODO: 接入专业行业分类 (申万 / GICS)
    """
    conn = sqlite3.connect(str(DB))
    row = conn.execute(
        "SELECT name FROM stock_basic WHERE symbol=?", (symbol,)
    ).fetchone()
    conn.close()

    if row is None:
        return "未知"

    name = row[0] or ""

    # 简单的关键词映射 (仅供演示, 非专业分类)
    keyword_sector = {
        # 金融
        "银行": "金融",
        "证券": "金融",
        "保险": "金融",
        "平安": "金融",
        "信托": "金融",
        "投资": "金融",
        # 食品饮料
        "白酒": "食品饮料",
        "酒": "食品饮料",
        "茅台": "食品饮料",
        "五粮液": "食品饮料",
        "食品": "食品饮料",
        "饮料": "食品饮料",
        "乳业": "食品饮料",
        "调味": "食品饮料",
        # 医药生物
        "医药": "医药生物",
        "生物": "医药生物",
        "药": "医药生物",
        "医疗": "医药生物",
        "健康": "医药生物",
        # 科技 / 电子
        "科技": "科技",
        "信息": "科技",
        "通讯": "科技",
        "通信": "科技",
        "软件": "科技",
        "电子": "电子",
        "芯片": "电子",
        "半导体": "电子",
        # 新能源 / 电力设备
        "新能源": "电力设备",
        "光伏": "电力设备",
        "锂电": "电力设备",
        "宁德时代": "电力设备",
        "电池": "电力设备",
        "汽车": "汽车",
        # 家电 / 消费
        "家电": "家用电器",
        "美的": "家用电器",
        "格力": "家用电器",
        "海尔": "家用电器",
        "消费": "大消费",
        # 工业 / 制造
        "机械": "机械设备",
        "装备": "机械设备",
        "军工": "国防军工",
        "航空": "交通运输",
        "航天": "国防军工",
        # 周期
        "有色": "有色金属",
        "煤炭": "煤炭",
        "钢铁": "钢铁",
        "化工": "化工",
        "石化": "化工",
        # 建筑 / 地产
        "建筑": "建筑建材",
        "建材": "建筑建材",
        "地产": "房地产",
        # 公用事业
        "电力": "公用事业",
        "环保": "公用事业",
        "水务": "公用事业",
        # 传媒 / 互联网
        "传媒": "传媒",
        "互联网": "传媒",
        # 农业
        "农业": "农林牧渔",
        "牧": "农林牧渔",
        "渔": "农林牧渔",
    }

    for kw, sector in keyword_sector.items():
        if kw in name:
            return sector

    return "其他"


def sector_concentration(
    holdings: Dict[str, float],
    threshold: float = 0.30,
) -> Dict[str, Any]:
    """
    计算行业集中度

    参数
    ----
    holdings : dict
        {symbol: weight}, 权重之和应为 1.0
    threshold : float, default=0.30
        单行业告警阈值 (30%)

    返回
    ----
    dict
        {
            'sector_weights': {行业: 占比},
            'max_sector':     str,       # 占比最大的行业
            'max_sector_pct': float,     # 最大行业占比
            'alert':          bool,      # 是否触发告警
            'alerts':         list[str], # 告警消息
        }
    """
    # 按行业聚合权重
    sector_weights: Dict[str, float] = {}
    for sym, w in holdings.items():
        sector = _fetch_symbol_sector(sym)
        sector_weights[sector] = sector_weights.get(sector, 0.0) + w

    # 计算最大行业占比
    if not sector_weights:
        max_sector = "N/A"
        max_pct = 0.0
    else:
        max_sector = max(sector_weights, key=sector_weights.get)
        max_pct = sector_weights[max_sector]

    # 告警检测
    alerts = []
    for sector, pct in sorted(sector_weights.items(), key=lambda x: -x[1]):
        if pct > threshold:
            alerts.append(f"[行业集中] {sector}: {pct:.2%} (阈值: {threshold:.0%})")

    return {
        "sector_weights": sector_weights,
        "max_sector": max_sector,
        "max_sector_pct": round(max_pct, 4),
        "alert": len(alerts) > 0,
        "alerts": alerts,
    }


# ===================================================================
# 流动性风险
# ===================================================================

def liquidity_risk(
    holdings: Dict[str, float],
    trade_date: str,
    threshold: float = 0.05,
) -> List[str]:
    """
    检测持仓占日均成交额比例 (流动性风险)

    参数
    ----
    holdings : dict
        {symbol: 持仓金额 (人民币)}
    trade_date : str
        YYYY-MM-DD 格式的交易日期
    threshold : float, default=0.05
        持仓占日均成交额告警阈值 (5%)

    返回
    ----
    list[str]
        告警消息列表, 空列表表示无告警
    """
    conn = sqlite3.connect(str(DB))
    alerts = []

    for sym, amount in holdings.items():
        # 取最近 20 个交易日的日均成交额
        avg_vol_row = conn.execute(
            "SELECT AVG(amount) FROM ("
            "  SELECT amount FROM stock_daily "
            "  WHERE symbol=? AND trade_date <= ? "
            "  ORDER BY trade_date DESC LIMIT 20"
            ")", (sym, trade_date)
        ).fetchone()

        avg_vol = avg_vol_row[0] if avg_vol_row else None

        if avg_vol and avg_vol > 0:
            ratio = amount / avg_vol
            if ratio > threshold:
                alerts.append(
                    f"[流动性] {sym}: 持仓 ¥{amount:,.0f} 占日均成交 "
                    f"{ratio:.1%} (阈值: {threshold:.0%})"
                )

    conn.close()
    return alerts
