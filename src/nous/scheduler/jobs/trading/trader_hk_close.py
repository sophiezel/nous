#!/usr/bin/env python3
"""模拟盘交易系统 — 港股收盘更新脚本

交易日 16:05 运行，更新港股持仓收盘价并归档。
"""

import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

PROJECT_DIR = Path.home() / "code/stock-advisor"
sys.path.insert(0, str(PROJECT_DIR))

from nous.trader import StateManager


def main():
    state = StateManager(str(PROJECT_DIR / "trader")).load()

    hk_positions = state.portfolio.get_by_market("HK")
    if not hk_positions:
        print("无港股持仓")
        return

    # 拉取港股收盘价
    symbols = [p.symbol for p in hk_positions]
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

        for line in resp.text.strip().split("\n"):
            if not line or "=" not in line:
                continue
            parts = line.split('="')
            if len(parts) < 2:
                continue
            symbol = parts[0].split("_hk")[-1]
            data = parts[1].rstrip('";').split(",")
            if len(data) >= 7 and data[6]:
                price = Decimal(data[6])
                pos = state.portfolio.get(symbol)
                if pos:
                    pos.update_price(price, datetime.now().isoformat())
                    print(f"  {pos.name}({pos.symbol}) 收盘 ¥{price} | 盈亏 {pos.pnl_pct*100:+.2f}%")

    except Exception as e:
        print(f"港股收盘价拉取失败: {e}")

    state.save()
    state.archive()
    print(f"港股收盘更新完成 | 持仓 {len(state.portfolio)}")


if __name__ == "__main__":
    main()
