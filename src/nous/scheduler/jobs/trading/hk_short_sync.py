#!/usr/bin/env python3
"""港股做空数据同步脚本

从 hk_short_selling fetcher 获取数据 → 写入 screener.db hk_short_signal 表

用法:
  cd ~/code/stock-advisor
  python3 scripts/hk_short_sync.py
"""

from __future__ import annotations
import sys
import os
import sqlite3
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from nous.data.collectors.fetchers.hk_short_selling import fetch_hk_short_selling

DB_PATH = Path.home() / "code/stock-screener/data/screener.db"


def main():
    today = date.today().isoformat()

    # 获取做空数据
    print(f"[hk_short_sync] 获取 {today} 港股做空数据...")
    data = fetch_hk_short_selling()
    if not data:
        print("[hk_short_sync] 无做空数据(已标记盲区), 跳过")
        return

    print(f"[hk_short_sync] 获取 {len(data)} 条做空记录")

    # 写入数据库
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA busy_timeout = 30000")

    inserted = 0
    for item in data:
        symbol = item.get("symbol", "")
        if not symbol:
            continue
        try:
            db.execute(
                """INSERT OR REPLACE INTO hk_short_signal 
                   (trade_date, symbol, name, short_volume, short_amount, short_ratio, prev_short_ratio)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    today,
                    symbol,
                    item.get("name", ""),
                    item.get("short_volume", 0),
                    item.get("short_amount", 0),
                    item.get("short_ratio", 0),
                    0,  # prev_short_ratio 后续由计算逻辑填充
                )
            )
            inserted += 1
        except Exception as e:
            print(f"  ⚠️ {symbol}: {e}")

    db.commit()

    # 验证
    cur = db.execute(
        "SELECT COUNT(*) FROM hk_short_signal WHERE trade_date=?",
        (today,)
    )
    count = cur.fetchone()[0]
    db.close()

    print(f"[hk_short_sync] 写入 {inserted} 条, 验证 {count} 条")


if __name__ == "__main__":
    main()
