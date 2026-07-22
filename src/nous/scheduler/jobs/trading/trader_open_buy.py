#!/usr/bin/env python3
"""模拟盘交易系统 — 开盘买入脚本

交易日 09:32 运行：
  1. 纪律检查
  2. 读取荐股报告
  3. 执行首次买入
  4. 启动盘中轮询（后台）
"""

import sys
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

PROJECT_DIR = Path.home() / "code/stock-advisor"
sys.path.insert(0, str(PROJECT_DIR))

from nous.trader import (
    StateManager, RiskEngine, RiskRules, Executor, Candidate,
    parse_recommendations, DisciplineChecker,
)


def main():
    today = date.today().isoformat()
    state_dir = str(PROJECT_DIR / "trader")

    # 加载状态
    state = StateManager(state_dir).load()

    # 1. 纪律检查
    dc = DisciplineChecker(state_dir)
    disc_result = dc.check(state.orders, RiskEngine(state.risk_rules))
    if disc_result.violations:
        print("⚠️ 纪律违规:")
        for v in disc_result.violations:
            print(f"  - {v}")
    if disc_result.reduce_to < Decimal("1.0"):
        print(f"⚠️ 仓位限制: {float(disc_result.reduce_to*100):.0f}%")
    print(f"纪律状态: {dc.get_status_summary()[:120]}")
    sys.stdout.flush()

    # 2. 读取荐股报告
    report_path = Path.home() / "wiki/finance/reports" / f"{today}.md"
    if not report_path.exists():
        print(f"❌ 未找到荐股报告 {report_path}，跳过买入")
        return

    candidates = parse_recommendations(str(report_path))
    print(f"加载 {len(candidates)} 只候选股")

    # 3. 配置 ATR（暂时用默认值，后续从日线计算）
    for c in candidates:
        c.buy_low = Decimal("0")  # 报告里无买入区间，使用 ±2% 开盘价区间
        c.buy_high = Decimal("999999")  # 不设上限（实际由风控约束）
        if c.atr == 0:
            c.atr = Decimal("1.5")

    # 4. 拉取开盘价
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        a_prices = {}
        for _, row in df.iterrows():
            code = row["代码"]
            price = row["最新价"]
            if price and str(price) != "nan":
                a_prices[code] = Decimal(str(price))
    except Exception as e:
        print(f"⚠️ A股行情拉取失败: {e}")
        a_prices = {}

    # 港股
    hk_prices = {}
    hk_candidates = [c for c in candidates if c.market == "HK"]
    if hk_candidates:
        try:
            import requests
            codes = ",".join(f"rt_hk{c.symbol}" for c in hk_candidates)
            url = f"https://hq.sinajs.cn/list={codes}"
            resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                if not line or "=" not in line:
                    continue
                parts = line.split('="')
                if len(parts) < 2:
                    continue
                symbol = parts[0].split("_hk")[-1]
                data = parts[1].rstrip('";').split(",")
                if len(data) >= 7 and data[6]:
                    hk_prices[symbol] = Decimal(data[6])
        except Exception as e:
            print(f"⚠️ 港股行情拉取失败: {e}")

    all_prices = {**a_prices, **hk_prices}
    print(f"行情: A股 {len(a_prices)} 港股 {len(hk_prices)}")

    # 5. 执行买入
    risk = RiskEngine(state.risk_rules)
    executor = Executor(state, risk)
    index_changes = {"sh000001": Decimal("0")}

    results = executor.execute_open_buys(candidates, all_prices, index_changes)
    for r in results:
        if r.action == "buy":
            print(f"🟢 {r.name}({r.symbol}) {r.shares}股 @ ¥{r.price} — {r.reason}")
        elif r.action == "skip":
            print(f"⏭️ {r.name}({r.symbol}) skip: {r.reason[:60]}")
        else:
            print(f"❌ {r.name}({r.symbol}) {r.action}: {r.reason[:60]}")

    state.save()
    print(f"\n开盘买入完成 | 持仓 {len(state.portfolio)} | 现金 ¥{state.account.cash:,.0f}")

    # 6. 启动盘中轮询（后台）
    print("\n启动盘中轮询...")
    poll_script = PROJECT_DIR / "scripts" / "trader_poll.py"
    os.system(
        f"PYTHONUNBUFFERED=1 {sys.executable} {poll_script} &"
    )
    print("盘中轮询已后台启动（PID 见上）")


if __name__ == "__main__":
    main()
