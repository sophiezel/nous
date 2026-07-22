#!/usr/bin/env python3
"""北向资金多源估算引擎 v1.0

核心原理:
  TOP50个股净买额(有数据) × K系数 → 全市场北向净买额(推算)
  
K系数来源:
  - 历史校准: 监管变更前(2014-2024)，TOP50通常占全市场成交~50%
  - 默认K=2.0 (保守)，可通过更多数据校准
  
多源验证:
  Source 1: TOP50个股聚合 → 个股级流向 + 板块聚合 (主源)
  Source 2: 南向资金联动 → 跨市场验证 (辅源)
  Source 3: 季度持股变化 → 长期趋势校准 (辅源)
"""

import sqlite3
import json
from pathlib import Path
from datetime import date, timedelta
from typing import Optional
from dataclasses import dataclass, field

from nous.core.db import _resolve_path
DB = Path(_resolve_path("screener.db"))

# K系数配置 (待更多数据校准)
K_DEFAULT = 2.0       # TOP50 → 全市场 外推系数
K_P25 = 1.5            # 保守下限
K_P75 = 2.5            # 乐观上限

# 权重配置
W_TOP50 = 0.50         # 主源权重
W_SOUTH = 0.25         # 南向联动
W_QUARTER = 0.15       # 季度校准
W_SECTOR = 0.10        # 板块聚合验证

@dataclass
class NorthboundEstimate:
    trade_date: str
    estimated_net_buy: float          # 推算净买额(亿元)
    confidence: str                   # high / medium / low
    top50_net: float                  # TOP50净买额(元)
    top50_stocks: int                 # TOP50标的数
    south_net: Optional[float] = None # 南向净买额(亿元)
    sector_net: Optional[float] = None # 板块聚合总和
    method: str = "top50_extrapolation_v1"
    details: dict = field(default_factory=dict)


def estimate(as_of_date: str) -> NorthboundEstimate:
    """推算指定日期的北向全市场净买额"""
    conn = sqlite3.connect(str(DB))
    
    # 1. 获取TOP50聚合数据
    top50 = conn.execute("""
        SELECT SUM(estimated_net_buy) as top50_net, COUNT(*) as stocks,
               SUM(CASE WHEN estimated_net_buy > 0 THEN estimated_net_buy ELSE 0 END) as buy_sum,
               SUM(CASE WHEN estimated_net_buy < 0 THEN estimated_net_buy ELSE 0 END) as sell_sum
        FROM hsgt_stock_daily
        WHERE direction='北向' AND trade_date=? AND estimated_net_buy IS NOT NULL
    """, (as_of_date,)).fetchone()
    
    if not top50 or not top50[0]:
        conn.close()
        return NorthboundEstimate(
            trade_date=as_of_date, estimated_net_buy=0, confidence="low",
            top50_net=0, top50_stocks=0, method="no_data"
        )
    
    top50_net = top50[0]  # 单位: 元
    stocks = top50[1]
    buy_sum = top50[2] or 0
    sell_sum = top50[3] or 0
    
    # 2. 外推 - TOP50 → 全市场
    full_market_top50 = top50_net * K_DEFAULT / 1e8  # 转亿元
    full_market_low = top50_net * K_P25 / 1e8
    full_market_high = top50_net * K_P75 / 1e8
    
    # 3. 南向联动验证
    south_net = None
    south_row = conn.execute("""
        SELECT net_buy FROM hsgt_daily 
        WHERE direction='south' AND trade_date=? AND net_buy IS NOT NULL
    """, (as_of_date,)).fetchone()
    if south_row:
        south_net = south_row[0]
    
    # 4. 板块聚合验证
    sector_row = conn.execute("""
        SELECT SUM(estimated_net_buy) FROM hsgt_stock_daily
        WHERE direction='北向' AND trade_date=? AND estimated_net_buy IS NOT NULL AND industry IS NOT NULL
    """, (as_of_date,)).fetchone()
    sector_net_sum = sector_row[0] / 1e8 if sector_row and sector_row[0] else None
    
    # 5. 确定性评估
    if stocks >= 80:
        confidence = "high"
    elif stocks >= 40:
        confidence = "medium"
    else:
        confidence = "low"
    
    # 如果南向方向一致，提升置信度
    if south_net is not None:
        top50_dir = 1 if top50_net > 0 else -1
        south_dir = 1 if south_net > 0 else -1
        if top50_dir == south_dir and confidence == "medium":
            confidence = "high"
    
    # 6. 推算: 纯K×TOP50 (不参与南向/板块融合, 仅侧信道验证)
    estimated = full_market_top50
    
    # 南向方向验证
    if south_net is not None:
        north_dir = 1 if top50_net > 0 else (-1 if top50_net < 0 else 0)
        south_dir = 1 if south_net > 0 else (-1 if south_net < 0 else 0)
        if north_dir != 0 and south_dir != 0 and north_dir != south_dir:
            # 方向不一致→南北向通常对称, 降置信度
            if confidence == "high": confidence = "medium"
            elif confidence == "medium": confidence = "low"
    
    # 板块聚合偏差验证
    if sector_net_sum is not None:
        sector_extrapolated = sector_net_sum * K_DEFAULT
        if estimated != 0:
            deviation = abs(sector_extrapolated - estimated) / abs(estimated)
            if deviation > 0.3:
                if confidence == "high": confidence = "medium"
                elif confidence == "medium": confidence = "low"
    
    conn.close()
    
    return NorthboundEstimate(
        trade_date=as_of_date,
        estimated_net_buy=round(estimated, 2),
        confidence=confidence,
        top50_net=top50_net,
        top50_stocks=stocks,
        south_net=south_net,
        sector_net=sector_net_sum,
        method="top50_extrapolation_v1",
        details={
            "full_market_top50": round(full_market_top50, 2),
            "full_market_range": [round(full_market_low, 2), round(full_market_high, 2)],
            "K": K_DEFAULT,
            "buy_sum_yi": round(buy_sum / 1e8, 2),
            "sell_sum_yi": round(sell_sum / 1e8, 2),
            "weights": {"top50": W_TOP50, "south": W_SOUTH, "quarter": W_QUARTER, "sector": W_SECTOR},
        }
    )


def write_to_db(est: NorthboundEstimate):
    """将推算结果写入hsgt_daily和hsgt_market_daily"""
    conn = sqlite3.connect(str(DB))
    
    # 写入hsgt_daily (north行) - 使用推算值
    conn.execute("""
        INSERT OR REPLACE INTO hsgt_daily 
        (trade_date, direction, net_buy)
        VALUES (?, 'north', ?)
    """, (est.trade_date, est.estimated_net_buy))
    
    # 更新hsgt_market_daily - 记录推算方法
    conn.execute("""
        INSERT OR REPLACE INTO hsgt_market_daily 
        (trade_date, direction, total_turnover, top10_concentration, top10_stocks, fetched_at)
        VALUES (?, '北向', ?, ?, ?, datetime('now','localtime'))
    """, (
        est.trade_date,
        est.estimated_net_buy,
        est.confidence,
        json.dumps({"method": est.method, "top50_net_yi": round(est.top50_net/1e8,2), "top50_stocks": est.top50_stocks}, ensure_ascii=False)
    ))
    
    # 写入板块聚合数据到hsgt_sector_daily
    sector_rows = conn.execute("""
        SELECT industry, SUM(estimated_net_buy) as sector_net, COUNT(*) as stocks,
               SUM(CASE WHEN estimated_net_buy>0 THEN estimated_net_buy ELSE 0 END) as buy_sum,
               SUM(CASE WHEN estimated_net_buy<0 THEN estimated_net_buy ELSE 0 END) as sell_sum,
               SUM(CASE WHEN estimated_net_buy>0 THEN 1 ELSE 0 END) as buys,
               SUM(CASE WHEN estimated_net_buy<0 THEN 1 ELSE 0 END) as sells
        FROM hsgt_stock_daily
        WHERE direction='北向' AND trade_date=? AND estimated_net_buy IS NOT NULL AND industry IS NOT NULL AND industry != ''
        GROUP BY industry
        ORDER BY ABS(SUM(estimated_net_buy)) DESC
        LIMIT 20
    """, (est.trade_date,)).fetchall()
    
    for row in sector_rows:
        conn.execute("""
            INSERT OR REPLACE INTO hsgt_sector_daily 
            (trade_date, direction, sector, total_net_buy, total_net_buy_pos_sum, 
             total_net_buy_neg_sum, stock_count, buy_count, sell_count)
            VALUES (?, '北向', ?, ?, ?, ?, ?, ?, ?)
        """, (est.trade_date, row[0], row[1], row[3], row[4], row[2], row[5], row[6]))
    
    conn.commit()
    conn.close()
    print(f"✅ {est.trade_date}: 推算净买={est.estimated_net_buy:.2f}亿, 置信度={est.confidence}, 板块={len(sector_rows)}个")


# ══════════════════════════════════════════════
# Phase 2: 跨市场代理信号验证 (只读, 不融合)
# ══════════════════════════════════════════════

def cross_validate(est: NorthboundEstimate) -> dict:
    """跨市场信号交叉验证: 检查南向/成交额/ETF/AH溢价等
    
    返回验证报告, 不修改est
    """
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA busy_timeout=5000")
    checks = {}
    
    # 1. 南向方向一致性
    south_row = conn.execute("""
        SELECT MAX(net_buy) FROM hsgt_daily
        WHERE direction='south' AND trade_date=? AND net_buy IS NOT NULL
    """, (est.trade_date,)).fetchone()
    if south_row and south_row[0] is not None:
        south_dir = 1 if south_row[0] > 0 else (-1 if south_row[0] < 0 else 0)
        north_dir = 1 if est.estimated_net_buy > 0 else (-1 if est.estimated_net_buy < 0 else 0)
        checks["南向方向"] = {
            "pass": north_dir == 0 or south_dir == 0 or north_dir != south_dir,  # 通常南北反向
            "detail": f"北向{est.estimated_net_buy:.1f}亿, 南向{south_row[0]:.1f}亿, 方向{'一致⚠️' if north_dir==south_dir and north_dir!=0 else '反向✅'}",
        }
    
    # 2. 成交额比例验证
    idx_row = conn.execute("""
        SELECT SUM(volume)/1e8 FROM index_daily
        WHERE trade_date=? AND symbol IN ('sh000001','sz399001')
    """, (est.trade_date,)).fetchone()
    if idx_row and idx_row[0]:
        total_turnover = idx_row[0]
        # 北向成交/总成交 ≈ 5-12%
        est_turnover = abs(est.estimated_net_buy) / 0.03  # 逆推: 净买/净买率≈成交
        ratio = est_turnover / total_turnover * 100 if total_turnover else 0
        checks["成交额比"] = {
            "pass": 3 < ratio < 15,
            "detail": f"推算北向成交{est_turnover:.0f}亿/{total_turnover:.0f}亿={ratio:.1f}%(合理3-15%)",
        }
    
    # 3. 板块聚合一致性
    sector_total = conn.execute("""
        SELECT SUM(total_net_buy) FROM hsgt_sector_daily
        WHERE direction='北向' AND trade_date=?
    """, (est.trade_date,)).fetchone()
    if sector_total and sector_total[0]:
        deviation = abs(sector_total[0] - est.estimated_net_buy) / abs(est.estimated_net_buy) if est.estimated_net_buy else 0
        checks["板块聚合"] = {
            "pass": deviation < 0.25,
            "detail": f"板块总和{sector_total[0]:.1f}亿 vs 推算{est.estimated_net_buy:.1f}亿, 偏差{deviation:.1%}",
        }
    
    # 4. AH溢价趋势
    ah_row = conn.execute("""
        SELECT premium_pct FROM ah_premium_daily
        WHERE trade_date=? ORDER BY trade_date DESC LIMIT 1
    """, (est.trade_date,)).fetchone()
    if ah_row and ah_row[0] is not None:
        ah_premium = ah_row[0]
        # 溢价>130→A股偏贵→北向可能流出
        if ah_premium > 135 and est.estimated_net_buy > 0:
            checks["AH溢价"] = {
                "pass": False,
                "detail": f"AH溢价{ah_premium:.1f}>135, A股偏贵, 与北向流入不一致⚠️",
            }
        elif ah_premium < 120 and est.estimated_net_buy < 0:
            checks["AH溢价"] = {
                "pass": False, 
                "detail": f"AH溢价{ah_premium:.1f}<120, A股折价, 与北向流出不一致⚠️",
            }
        else:
            checks["AH溢价"] = {
                "pass": True,
                "detail": f"AH溢价{ah_premium:.1f}, 方向一致✅",
            }
    
    conn.close()
    
    passed = sum(1 for c in checks.values() if c["pass"])
    total = len(checks)
    return {
        "checks": checks,
        "passed": f"{passed}/{total}",
        "all_pass": passed == total,
    }


def estimate_and_write(as_of_date: str = None):
    """一站式: 推算 + 写入DB"""
    if as_of_date is None:
        # 默认用hsgt_stock_daily最新日期
        conn = sqlite3.connect(str(DB))
        row = conn.execute("SELECT MAX(trade_date) FROM hsgt_stock_daily WHERE estimated_net_buy IS NOT NULL").fetchone()
        conn.close()
        if not row or not row[0]:
            print("❌ 无可用TOP50数据")
            return
        as_of_date = row[0]
    
    est = estimate(as_of_date)
    print(f"\n{'='*60}")
    print(f"北向资金推算 — {est.trade_date}")
    print(f"{'='*60}")
    print(f"TOP50净买:      {est.top50_net/1e8:.2f}亿 ({est.top50_stocks}只)")
    print(f"推算全市场:     {est.estimated_net_buy:.2f}亿 (K={K_DEFAULT})")
    print(f"区间:           {est.details['full_market_range'][0]:.2f} ~ {est.details['full_market_range'][1]:.2f}亿")
    print(f"置信度:         {est.confidence}")
    if est.south_net is not None:
        print(f"南向参考:       {est.south_net:.2f}亿")
    print(f"买入合计:       {est.details['buy_sum_yi']:.2f}亿")
    print(f"卖出合计:       {est.details['sell_sum_yi']:.2f}亿")
    
    write_to_db(est)
    return est


if __name__ == '__main__':
    import sys
    as_of = sys.argv[1] if len(sys.argv) > 1 else None
    estimate_and_write(as_of)
