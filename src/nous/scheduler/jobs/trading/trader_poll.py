#!/usr/bin/env python3
"""模拟盘交易系统 — 盘中轮询脚本

由 cron 在 09:35 启动，每 5 分钟轮询至 16:00（港股收盘）。
A股收盘15:00后进入港股独享窗口(15:00-16:00)，仅拉港股行情+港股风控。
午休12:00-13:00暂停轮询。
功能：
  1. 拉取候选股+持仓股实时行情（双市场）
  2. 检查止损/止盈/移动止盈/时间止损
  3. 尝试金字塔加仓
  4. 检测大盘熔断
  5. 每轮保存状态

用法：
  PYTHONPATH=~/code/stock-advisor python3 scripts/trader_poll.py
"""

from __future__ import annotations
import sys
import os
import time
import signal
import json
from datetime import date, datetime, time as dtime
from decimal import Decimal
from pathlib import Path

# 添加项目路径
PROJECT_DIR = Path.home() / "code/stock-advisor"
sys.path.insert(0, str(PROJECT_DIR))

from nous.trader import (
    StateManager, RiskEngine, RiskRules, Executor, Candidate,
    parse_recommendations, full_pre_trade_check,
)

# 是否已请求退出
_shutdown = False


def handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    print(f"[{datetime.now():%H:%M:%S}] 收到退出信号，本轮完成后退出...")


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


# ============================================================
# 行情拉取
# ============================================================

def fetch_a_share_prices(symbols: list[str]) -> dict[str, Decimal]:
    """从 akshare 拉取 A 股实时行情"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        prices = {}
        for _, row in df.iterrows():
            code = row["代码"]
            if code in symbols:
                try:
                    prices[code] = Decimal(str(row["最新价"]))
                except (ValueError, KeyError):
                    pass
        return prices
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] A股行情拉取失败: {e}")
        return {}


def fetch_hk_prices(symbols: list[str]) -> dict[str, Decimal]:
    """从 Sina 拉取港股实时行情"""
    if not symbols:
        return {}
    try:
        import requests
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
                    prices[name_raw] = Decimal(data[6])  # 最新价
                except (ValueError, Exception):
                    pass
        return prices
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] 港股行情拉取失败: {e}")
        return {}


# ============================================================
# 主循环
# ============================================================

def should_continue() -> bool:
    """检查是否应继续轮询（双市场：A股15:00收盘，港股16:00收盘）"""
    if _shutdown:
        return False
    now = datetime.now().time()
    # 午休时段暂停（12:00-13:00 港股午休，A股也休）
    if dtime(12, 0) <= now < dtime(13, 0):
        return False
    # 港股收盘 16:00（涵盖A股15:00后的最后一小时）
    return now < dtime(16, 1)


class HKMarketTracker:
    """港股交易时段追踪器"""

    @staticmethod
    def is_hk_trading() -> bool:
        """港股是否在交易时段"""
        now = datetime.now().time()
        morning = dtime(9, 30) <= now < dtime(12, 0)
        afternoon = dtime(13, 0) <= now < dtime(16, 0)
        return morning or afternoon

    @staticmethod
    def is_a_trading() -> bool:
        """A股是否在交易时段"""
        now = datetime.now().time()
        morning = dtime(9, 30) <= now < dtime(11, 30)
        afternoon = dtime(13, 0) <= now < dtime(15, 0)
        return morning or afternoon

    @staticmethod
    def is_lunch_break() -> bool:
        """午休时段"""
        now = datetime.now().time()
        return dtime(12, 0) <= now < dtime(13, 0)

    @staticmethod
    def is_hk_only_window() -> bool:
        """港股独享窗口（A股已收盘，港股最后一小时 15:00-16:00）"""
        now = datetime.now().time()
        return dtime(15, 0) <= now < dtime(16, 0)


def get_all_monitored_symbols(
    state: StateManager,
    candidates: list[Candidate],
) -> tuple[list[str], list[str]]:
    """获取所有需监控的股票代码"""
    a_symbols = set()
    hk_symbols = set()

    for pos in state.portfolio.positions.values():
        if pos.market == "A":
            a_symbols.add(pos.symbol)
        else:
            hk_symbols.add(pos.symbol)

    for c in candidates:
        if c.market == "A":
            a_symbols.add(c.symbol)
        else:
            hk_symbols.add(c.symbol)

    return list(a_symbols), list(hk_symbols)


# ============================================================
# 主入口
# ============================================================

def main():
    print(f"[{datetime.now():%H:%M:%S}] 模拟盘轮询启动")
    sys.stdout.flush()

    # 加载状态
    state_dir = str(PROJECT_DIR / "trader")
    state = StateManager(state_dir).load()

    # 读取今日荐股报告
    today = date.today().isoformat()
    report_path = Path.home() / "wiki/finance/reports" / f"{today}.md"

    candidates = []
    if report_path.exists():
        candidates = parse_recommendations(str(report_path))
        print(f"[{datetime.now():%H:%M:%S}] 加载 {len(candidates)} 只候选股")
    else:
        print(f"[{datetime.now():%H:%M:%S}] 未找到今日荐股报告 {report_path}")

    # 初始化风控和执行器
    risk = RiskEngine(state.risk_rules)
    executor = Executor(state, risk)

    # 配置 AT（简化版：用固定 ATR）
    for c in candidates:
        if c.atr == 0:
            c.atr = Decimal("1.5")  # 默认 ATR

    poll_count = 0
    stale_count = 0
    max_stale = 3  # 连续 stale 上限

    while should_continue():
        poll_count += 1
        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now_str}] 轮询 #{poll_count}")
        sys.stdout.flush()

        # 1. 拉取行情
        a_syms, hk_syms = get_all_monitored_symbols(state, candidates)
        
        # 港股独享窗口：不拉A股行情（A股已收盘）
        if HKMarketTracker.is_hk_only_window():
            a_prices = {}
        else:
            a_prices = fetch_a_share_prices(a_syms) if a_syms else {}
        hk_prices = fetch_hk_prices(hk_syms) if hk_syms else {}
        all_prices = {**a_prices, **hk_prices}

        if not all_prices:
            stale_count += 1
            print(f"  ⚠️ 行情数据为空 (连续 {stale_count}/{max_stale})")
            if stale_count >= max_stale:
                print(f"  🛑 连续 {max_stale} 轮无数据，暂停交易")
                for symbol, pos in state.portfolio.positions.items():
                    print(f"    持仓: {pos.name} 最后价 ¥{pos.current_price}")
                state.save()
                time.sleep(300)
                continue
        else:
            stale_count = 0
            print(f"  📊 行情: A股 {len(a_prices)}/{len(a_syms)} 港股 {len(hk_prices)}/{len(hk_syms)}")
            sys.stdout.flush()

        # 2. 更新持仓价格
        state.portfolio.update_all_prices(all_prices, now_str)

        # 3. 检查退出信号（止损/止盈/移动止盈/时间止损）
        exit_results = executor.execute_exits(all_prices)
        for er in exit_results:
            if er.action == "sell":
                print(f"  🔴 {er.name}({er.symbol}) {er.reason} pnl={er.pnl:.0f} ({er.pnl_pct:.2%})")

        # 4. 检查账户熔断
        index_changes = {"sh000001": Decimal("0")}  # 简化：大盘检查由外部提供
        breaker = risk.check_account_circuit_breaker(state.account, state.portfolio)
        if not breaker.passed:
            print(f"  ⚠️ 账户熔断: {breaker.reason}")
            state.save()
            time.sleep(300)
            continue

        # 5. 尝试买入（候选股 + 金字塔加仓）
        buy_candidates = [c for c in candidates
                         if not state.portfolio.has(c.symbol)
                         or state.portfolio.get(c.symbol).pyramid_stage < (2 if c.strategy == "short_term" else 3)]

        if buy_candidates:
            buy_results = executor.execute_open_buys(buy_candidates, all_prices, index_changes)
            for br in buy_results:
                if br.action == "buy":
                    print(f"  🟢 {br.name}({br.symbol}) {br.reason} {br.shares}股 @ ¥{br.price}")
                elif br.action == "skip":
                    pass  # 太多 skip 输出

        # 6. 金字塔加仓
        add_results = executor.execute_pyramid_add(all_prices, index_changes)
        for ar in add_results:
            if ar.action == "buy":
                print(f"  🟡 加仓 {ar.name}({ar.symbol}) {ar.reason} {ar.shares}股")

        # 7. 保存状态
        state.save()
        print(f"  💾 已保存 | 持仓 {len(state.portfolio)} | 现金 ¥{state.account.cash:,.0f}")
        sys.stdout.flush()

        # 8. 等待 5 分钟
        if should_continue():
            time.sleep(300)

    # 收盘
    print(f"\n[{datetime.now():%H:%M:%S}] 收盘，停止轮询（A股15:00 / 港股16:00）")
    state.archive()
    print(f"  归档完成 | 共 {poll_count} 轮")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
