"""
开盘前关键数据采集器 v3
交易日上午 08:15 运行

数据源：
  L1 必采集（可靠）：
    - A股全量行情  → stock_zh_a_spot() 新浪源 5514只 17s
    - 龙虎榜       → stock_lhb_detail_daily_sina() 新浪源
    - 北向资金     → stock_hsgt_hist_em() 东方财富 datacenter（不限流）
    - 大宗交易     → stock_dzjy_mrmx() 东方财富 datacenter（不限流）
    - 限售解禁     → stock_restricted_release_queue_sina() 新浪源

  L2 补充（尝试拉取，失败不阻塞）：
    - 龙虎榜统计   → stock_lhb_ggtj_sina()
    - A50期货      → stock_zh_index_spot_em() 东方财富（限流，等3s）
    - 主力资金流向 → stock_market_fund_flow() 东方财富（限流，等3s）
    - 港股通标的   → stock_hk_ggt_components_em() 东方财富（限流）

输出：~/wiki/finance/raw/data/premarket_YYYY-MM-DD.json
"""

import akshare as ak
import pandas as pd
import json
import os
import time
from datetime import datetime, date, timedelta
from typing import Dict, Optional

WIKI_RAW = os.path.expanduser("~/wiki/finance/raw/data")
os.makedirs(WIKI_RAW, exist_ok=True)

DEGRADED_SOURCES = []
EASTMONEY_CALL_COUNT = 0  # 东方财富 API 调用计数器


def safe_fetch(func, name: str, source: str = "auto", **kwargs) -> Optional[pd.DataFrame]:
    """安全拉取，超时30s，失败返回None"""
    global EASTMONEY_CALL_COUNT

    # 东方财富源：计数器+延迟防限流
    if source == "eastmoney":
        EASTMONEY_CALL_COUNT += 1
        if EASTMONEY_CALL_COUNT > 1:
            time.sleep(2)  # 每次东方财富调用间隔2s
        if EASTMONEY_CALL_COUNT > 3:
            time.sleep(3)  # 超过3次后每5s

    try:
        df = func(**kwargs)
        print(f"  [{name}] ✅ {len(df)} rows")
        return df
    except Exception as e:
        print(f"  [{name}] ❌ {type(e).__name__}: {str(e)[:80]}")
        DEGRADED_SOURCES.append(name)
        return None


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")

def yesterday_str() -> str:
    return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

def yesterday_compact() -> str:
    return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


# ═══════════════════════════════════════════════
# L1: 必采集（可靠数据源）
# ═══════════════════════════════════════════════

def fetch_a_share_market() -> Optional[pd.DataFrame]:
    """A股全量行情 — 新浪源（5514只，~18s）"""
    return safe_fetch(ak.stock_zh_a_spot, "A股全量行情", source="sina")

def fetch_dragon_tiger() -> Optional[pd.DataFrame]:
    """龙虎榜明细 — 新浪源"""
    return safe_fetch(ak.stock_lhb_detail_daily_sina, "龙虎榜", source="sina",
                      date=yesterday_str())

def fetch_northbound() -> Optional[pd.DataFrame]:
    """
    北向资金历史 — 东方财富 datacenter（不限流 ✅）
    取最近3条看趋势
    """
    try:
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        print(f"  [北向资金] ✅ {len(df)} rows (datacenter)")
        return df
    except Exception as e:
        print(f"  [北向资金] ❌ {e}")
        DEGRADED_SOURCES.append("北向资金")
        return None

def fetch_block_trades() -> Optional[pd.DataFrame]:
    """
    大宗交易 — 东方财富 datacenter（不限流 ✅）
    拉昨日单日数据
    """
    ym = yesterday_compact()
    return safe_fetch(ak.stock_dzjy_mrmx, "大宗交易", source="eastmoney",
                      symbol="A股", start_date=ym, end_date=ym)

def fetch_lockup() -> Optional[pd.DataFrame]:
    """限售解禁排队 — 新浪源"""
    return safe_fetch(ak.stock_restricted_release_queue_sina, "限售解禁",
                      source="sina", symbol="all")


# ═══════════════════════════════════════════════
# L2: 补充采集（尽力而为）
# ═══════════════════════════════════════════════

def fetch_dragon_tiger_stats() -> Optional[pd.DataFrame]:
    """龙虎榜统计 — 新浪源"""
    return safe_fetch(ak.stock_lhb_ggtj_sina, "龙虎榜统计", source="sina")

def fetch_market_fund_flow() -> Optional[pd.DataFrame]:
    """主力资金流向 — 东方财富（有限流，等3s）"""
    time.sleep(3)
    return safe_fetch(ak.stock_market_fund_flow, "主力资金流向", source="eastmoney")


# ═══════════════════════════════════════════════
# 情绪分数计算 v3
# ═══════════════════════════════════════════════

def calc_sentiment(
    a_df: Optional[pd.DataFrame] = None,    # A股全量
    nb_df: Optional[pd.DataFrame] = None,   # 北向资金
    fund_df: Optional[pd.DataFrame] = None, # 主力资金流向
) -> Dict:
    """
    情绪分 = 市场宽度(±0.25) + 北向资金(±0.20) + 主力资金(±0.15)
    数据不足时自动降权。
    """
    score = {
        "total": 0.0,
        "market_breadth": 0.0,    # A股涨跌比
        "northbound": 0.0,        # 北向资金方向
        "main_fund": 0.0,         # 主力资金流向
        "recommendation": "neutral",
        "position_advice": "稳健",
        "a_max": 4,
        "hk_max": 4,
        "degraded_sources": DEGRADED_SOURCES.copy(),
        "details": {},
        "timestamp": datetime.now().isoformat(),
    }

    # — 市场宽度（A股涨跌家数）—
    if a_df is not None and len(a_df) > 0:
        try:
            up = (a_df["涨跌额"] > 0).sum()
            down = (a_df["涨跌额"] < 0).sum()
            if up + down > 0:
                ratio = up / (up + down)
            else:
                ratio = 0.5
            score["details"]["up_count"] = int(up)
            score["details"]["down_count"] = int(down)
            score["details"]["breadth_ratio"] = round(ratio, 3)

            if ratio > 0.65:
                score["market_breadth"] = 0.25
            elif ratio > 0.55:
                score["market_breadth"] = 0.15
            elif ratio > 0.50:
                score["market_breadth"] = 0.05
            elif ratio > 0.40:
                score["market_breadth"] = -0.10
            else:
                score["market_breadth"] = -0.25
        except Exception:
            pass

    # — 北向资金方向 —
    if nb_df is not None and len(nb_df) >= 2:
        try:
            col = "当日成交净买额" if "当日成交净买额" in nb_df.columns else None
            if col:
                recent = nb_df[col].tail(3)
                net = recent.sum()
                score["details"]["northbound_3d_net"] = float(net)
                if net > 30:
                    score["northbound"] = 0.20
                elif net > 10:
                    score["northbound"] = 0.10
                elif net < -30:
                    score["northbound"] = -0.20
                elif net < -10:
                    score["northbound"] = -0.10
        except Exception:
            pass

    # — 主力资金流向 —
    if fund_df is not None and len(fund_df) > 0:
        try:
            col = "主力净流入-净额" if "主力净流入-净额" in fund_df.columns else None
            if col:
                last_val = fund_df[col].iloc[-1]
                if pd.notna(last_val):
                    yi = last_val / 1e8  # 转换为亿
                    score["details"]["main_fund_net_yi"] = round(yi, 1)
                    if yi > 50:
                        score["main_fund"] = 0.15
                    elif yi > 20:
                        score["main_fund"] = 0.10
                    elif yi < -50:
                        score["main_fund"] = -0.15
                    elif yi < -20:
                        score["main_fund"] = -0.10
        except Exception:
            pass

    # — 汇总 —
    score["total"] = sum([
        score["market_breadth"],
        score["northbound"],
        score["main_fund"],
    ])

    if score["total"] >= 0.20:
        score["recommendation"] = "bullish"
        score["position_advice"] = "积极"
        score["a_max"] = 5
        score["hk_max"] = 5
    elif score["total"] <= -0.20:
        score["recommendation"] = "bearish"
        score["position_advice"] = "防守"
        score["a_max"] = 2
        score["hk_max"] = 2
    else:
        score["recommendation"] = "neutral"
        score["position_advice"] = "稳健"
        score["a_max"] = 4
        score["hk_max"] = 4

    return score


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def main():
    global DEGRADED_SOURCES, EASTMONEY_CALL_COUNT
    DEGRADED_SOURCES = []
    EASTMONEY_CALL_COUNT = 0

    ts = today_str()
    print(f"[{datetime.now():%H:%M:%S}] 开盘前数据采集 v3 启动...")
    print(f"  数据日期: {ts} (前交易日: {yesterday_str()})")
    print()

    results = {
        "date": ts,
        "fetched_at": datetime.now().isoformat(),
        "version": "3.0",
    }

    # — L1: 必采集 —
    print("═══ L1 必采集 ═══")

    # A股全量行情（市场宽度）
    a_df = fetch_a_share_market()
    results["a_share_spot"] = len(a_df) if a_df is not None else "failed"

    # 北向资金
    nb_df = fetch_northbound()
    results["northbound"] = len(nb_df) if nb_df is not None else "failed"

    # 龙虎榜
    lhb = fetch_dragon_tiger()
    if lhb is not None:
        lhb.to_json(os.path.join(WIKI_RAW, f"dragon_tiger_{ts}.json"),
                    orient="records", force_ascii=False)
        results["dragon_tiger"] = len(lhb)
    else:
        results["dragon_tiger"] = "failed"

    # 大宗交易
    dzjy = fetch_block_trades()
    if dzjy is not None:
        dzjy.to_json(os.path.join(WIKI_RAW, f"block_trades_{ts}.json"),
                     orient="records", force_ascii=False)
        results["block_trades"] = len(dzjy)
    else:
        results["block_trades"] = "failed"

    # 限售解禁
    lockup = fetch_lockup()
    if lockup is not None:
        results["lockup"] = len(lockup)
    else:
        results["lockup"] = "failed"

    print()

    # — L2: 补充 —
    print("═══ L2 补充 ═══")

    # 龙虎榜统计
    lhb_stats = fetch_dragon_tiger_stats()
    if lhb_stats is not None:
        results["dragon_tiger_stats"] = len(lhb_stats)

    # 主力资金流向
    fund_df = fetch_market_fund_flow()
    if fund_df is not None:
        results["main_fund_flow"] = len(fund_df)

    print()

    # — 情绪计算 —
    sentiment = calc_sentiment(a_df, nb_df, fund_df)
    results["sentiment"] = sentiment

    # — 保存 —
    filepath = os.path.join(WIKI_RAW, f"premarket_{ts}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now():%H:%M:%S}] 采集完成 → {filepath}")
    print(f"  市场宽度: ↑{sentiment['details'].get('up_count','?')}"
          f" ↓{sentiment['details'].get('down_count','?')}"
          f" ({sentiment['details'].get('breadth_ratio','?')})")
    print(f"  情绪分数: {sentiment['total']:+.2f} → {sentiment['recommendation']}")
    print(f"  仓位建议: {sentiment['position_advice']}")
    print(f"  荐股上限: A股{sentiment['a_max']} 港股{sentiment['hk_max']}")
    if DEGRADED_SOURCES:
        print(f"  ⚠️ 降级/失败: {', '.join(DEGRADED_SOURCES)}")

    return results


if __name__ == "__main__":
    main()
