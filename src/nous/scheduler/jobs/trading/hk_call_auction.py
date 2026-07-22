#!/usr/bin/env python3
"""港股集合竞价预判脚本

运行时间: 09:20 (早市竞价) / 12:45 (午市竞价)
功能:
  1. 拉取港股候选股的虚拟开盘价 (Sina API IEP)
  2. 调用 executor.call_auction_hk() 执行竞价买入

港股竞价时段:
  早市: 09:00-09:30 (09:20后不可撤单，09:28-09:30随机对盘)
  午市: 12:00-13:00 (12:45后不可撤单)

用法:
  PYTHONPATH=~/code/stock-advisor python3 scripts/hk_call_auction.py
"""

from __future__ import annotations
import sys
import os
import requests
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

PROJECT_DIR = Path.home() / "code/stock-advisor"
sys.path.insert(0, str(PROJECT_DIR))

from nous.trader import StateManager, RiskEngine, Executor, Candidate, parse_recommendations


def fetch_hk_auction_prices(symbols: list[str]) -> dict[str, Decimal]:
    """从 Sina 拉取港股虚拟开盘价（IEP）"""
    if not symbols:
        return {}
    try:
        codes = ",".join(f"rt_hk{s}" for s in symbols)
        url = f"https://hq.sinajs.cn/list={codes}"
        resp = requests.get(
            url,
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=10,
        )
        resp.encoding = "gbk"
        prices = {}
        for line in resp.text.strip().split("\n"):
            if not line or "=" not in line:
                continue
            parts = line.split('="')
            if len(parts) < 2:
                continue
            name_raw = parts[0].split("_hk")[-1]
            data = parts[1].rstrip('";').split(",")
            if len(data) >= 7 and data[6]:
                try:
                    prices[name_raw] = Decimal(data[6])
                except (ValueError, Exception):
                    pass
        return prices
    except Exception as e:
        print(f"[hk_auction] 行情拉取失败: {e}")
        return {}


def main():
    now = datetime.now()
    print(f"[{now:%H:%M:%S}] 港股集合竞价预判启动")
    sys.stdout.flush()

    # 加载状态
    state_dir = str(PROJECT_DIR / "trader")
    state = StateManager(state_dir).load()
    
    # 只处理港股候选(从荐股报告)
    today = date.today().isoformat()
    report_path = Path.home() / "wiki/finance/reports" / f"{today}.md"
    
    hk_candidates = []
    if report_path.exists():
        all_candidates = parse_recommendations(str(report_path))
        hk_candidates = [c for c in all_candidates if c.market == "HK"]
    
    if not hk_candidates:
        print(f"[{now:%H:%M:%S}] 无港股候选，跳过")
        return
    
    print(f"[{now:%H:%M:%S}] 港股候选 {len(hk_candidates)} 只: "
          f"{', '.join(c.symbol for c in hk_candidates)}")
    
    # 拉取虚拟开盘价
    symbols = [c.symbol for c in hk_candidates]
    virtual_prices = fetch_hk_auction_prices(symbols)
    
    if not virtual_prices:
        print(f"[{now:%H:%M:%S}] 无港股虚拟开盘价数据，跳过")
        return
    
    print(f"[{now:%H:%M:%S}] 获取 {len(virtual_prices)} 个虚拟开盘价")
    for s, p in list(virtual_prices.items())[:5]:
        print(f"  {s}: {p}")
    
    # 执行竞价预判
    risk = RiskEngine(state.risk_rules)
    executor = Executor(state, risk)
    
    results = executor.call_auction_hk(hk_candidates, virtual_prices)
    
    buys = [r for r in results if r.action == "buy"]
    waits = [r for r in results if r.action == "wait"]
    skips = [r for r in results if r.action == "skip"]
    
    print(f"[{now:%H:%M:%S}] 竞价结果: 买入 {len(buys)}, 等待 {len(waits)}, 跳过 {len(skips)}")
    for r in buys:
        print(f"  🟢 {r.name}({r.symbol}) {r.reason}")
    for r in waits:
        print(f"  🟡 {r.name}({r.symbol}) {r.reason}")
    
    state.save()
    print(f"[{now:%H:%M:%S}] 状态已保存")


if __name__ == "__main__":
    main()
