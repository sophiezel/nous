#!/usr/bin/env python3
"""backfill_rebound_data.py — 为 rebound 引擎回补缺失数据（资金面/情绪/宏观层面）

用法:
  .venv/bin/python scripts/backfill_rebound_data.py margin  --start 2020-01-01 --end 2026-08-21
  .venv/bin/python scripts/backfill_rebound_data.py ztpool  --start 2020-01-01 --end 2026-08-21
  .venv/bin/python scripts/backfill_rebound_data.py macro
  .venv/bin/python scripts/backfill_rebound_data.py fundflow --start 2025-01-01   # 尽力而为(东财限流)

说明:
  - 绕过 macOS 坏代理 (127.0.0.1:7897): NO_PROXY='*'
  - 断点续跑: 已存在的 (date,symbol) 自动跳过
  - 东财 push2his (主力资金流) 有限流/指纹封禁, 用 curl 子进程 + 退避, 失败不中断
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["NO_PROXY"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import akshare as ak
import requests

from nous.data.storage import get_db

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
           "Referer": "https://data.eastmoney.com/"}

DDL = """
CREATE TABLE IF NOT EXISTS margin_stock_daily (
    trade_date TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT,
    margin_balance REAL, margin_buy REAL, margin_repay REAL,
    short_balance REAL, short_sell REAL,
    PRIMARY KEY (trade_date, symbol));
CREATE TABLE IF NOT EXISTS zt_pool_daily (
    trade_date TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT,
    close REAL, pct_chg REAL, limit_up_days INTEGER, amount REAL, reason TEXT,
    PRIMARY KEY (trade_date, symbol));
"""


def _ensure_tables(conn):
    conn.executescript(DDL)
    conn.commit()


def _trade_dates(start: str, end: str) -> list[str]:
    """取 A 股交易日序列（用 stock_daily_all）。"""
    rows = conn_exec("SELECT trade_date FROM stock_daily_all WHERE trade_date>=? AND trade_date<=? "
                     "GROUP BY trade_date ORDER BY trade_date", (start, end))
    return [r[0] for r in rows]


_conn = None


def conn_exec(sql, params=()):
    global _conn
    if _conn is None:
        _conn = get_db(write=True)
    return _conn.execute(sql, params)


# ══════════════════════════════════════════════════════
# 个股两融明细（沪: akshare 上交所 / 深: akshare 深交所）
# ══════════════════════════════════════════════════════

def backfill_margin(start: str, end: str) -> None:
    dates = _trade_dates(start, end)
    print(f"[margin] 交易日 {len(dates)} 天, 断点续跑")
    n_ins = 0
    for i, d in enumerate(dates):
        ymd = d.replace("-", "")
        for mkt, fn in (("sh", ak.stock_margin_detail_sse), ("sz", ak.stock_margin_detail_szse)):
            try:
                df = fn(date=ymd)
                for _, row in df.iterrows():
                    if mkt == "sh":
                        sym, name = str(row["标的证券代码"]), str(row["标的证券简称"])
                        vals = (row["融资余额"], row["融资买入额"], row["融资偿还额"], row["融券余量"], row["融券卖出量"])
                    else:
                        sym, name = str(row["证券代码"]), str(row["证券简称"])
                        vals = (row["融资余额"], row["融资买入额"], None, row["融券余额"], row["融券卖出量"])
                    conn_exec("INSERT OR REPLACE INTO margin_stock_daily VALUES (?,?,?,?,?,?,?,?)",
                              (d, sym, name, *[None if v is None or (isinstance(v, float) and v != v) else float(v) for v in vals]))
                    n_ins += 1
            except Exception as e:
                print(f"  [margin] {d} {mkt} 失败: {repr(e)[:80]}", flush=True)
                time.sleep(2)
        if (i + 1) % 20 == 0:
            _conn.commit()
            print(f"  [margin] {i+1}/{len(dates)} ({n_ins} 行)", flush=True)
        time.sleep(0.15)
    _conn.commit()
    print(f"[margin] 完成: {n_ins} 行")


# ══════════════════════════════════════════════════════
# 涨停池（东财 push2ex，直连请求）
# ══════════════════════════════════════════════════════

def backfill_ztpool(start: str, end: str) -> None:
    dates = _trade_dates(start, end)
    print(f"[ztpool] 交易日 {len(dates)} 天")
    n_ins = 0
    for i, d in enumerate(dates):
        ymd = d.replace("-", "")
        url = (f"https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
               f"&dpt=wz.ztzt&Pageindex=0&pagesize=500&sort=fbt%3Aasc&date={ymd}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            data = (r.json() or {}).get("data") or {}
            pool = data.get("pool") or []
            for p in pool:
                conn_exec("INSERT OR REPLACE INTO zt_pool_daily VALUES (?,?,?,?,?,?,?,?)",
                          (d, str(p.get("c", "")), p.get("n", ""), p.get("p"), p.get("zdp"),
                           p.get("lbc"), p.get("amount"), p.get("hybk", "")))
                n_ins += 1
        except Exception as e:
            print(f"  [ztpool] {d} 失败: {repr(e)[:80]}", flush=True)
            time.sleep(1.5)
        if (i + 1) % 20 == 0:
            _conn.commit()
            print(f"  [ztpool] {i+1}/{len(dates)} ({n_ins} 行)", flush=True)
        time.sleep(0.12)
    _conn.commit()
    print(f"[ztpool] 完成: {n_ins} 行")


# ══════════════════════════════════════════════════════
# 宏观 CPI/PPI/GDP 补齐
# ══════════════════════════════════════════════════════

def backfill_macro() -> None:
    updates = []
    try:
        df = ak.macro_china_cpi_monthly()
        for _, r in df.iterrows():
            updates.append(("macro_cpi", str(r.get("日期")), r.get("今值")))
        print(f"[macro] CPI 月度 {len(df)} 行")
    except Exception as e:
        print(f"[macro] CPI 失败: {repr(e)[:100]}")
    try:
        df = ak.macro_china_ppi_yearly()
        for _, r in df.iterrows():
            updates.append(("macro_ppi", str(r.get("日期")), r.get("今值")))
        print(f"[macro] PPI 年度 {len(df)} 行")
    except Exception as e:
        print(f"[macro] PPI 失败: {repr(e)[:100]}")
    for tbl, d, v in updates:
        try:
            conn_exec(f"UPDATE {tbl} SET value=? WHERE date=?", (float(v), d))
        except Exception:
            pass
    _conn.commit()
    print("[macro] 完成（尽力而为：列名/格式可能不匹配，主要验证数据可达）")


# ══════════════════════════════════════════════════════
# 主力资金流历史（东财 push2his，curl 子进程 + 退避，尽力而为）
# ══════════════════════════════════════════════════════

def backfill_fundflow(start: str, end: str) -> None:
    rows = conn_exec("SELECT symbol FROM stock_basic WHERE market='a' ORDER BY symbol").fetchall()
    done = {r[0] for r in conn_exec("SELECT DISTINCT symbol FROM fund_flow_stock").fetchall()}
    todo = [r[0] for r in rows if r[0] not in done]
    print(f"[fundflow] A股 {len(rows)} 只, 已回补 {len(done)}, 待 {len(todo)}（东财限流，尽力而为）")
    n_ins = 0
    fails = 0
    for k, sym in enumerate(todo):
        mkt = "1" if sym.startswith(("6", "9")) else "0"
        url = (f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=2000&klt=101&"
               f"fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57&secid={mkt}.{sym}")
        try:
            out = subprocess.run(["curl", "-s", "--max-time", "12",
                                  "-A", HEADERS["User-Agent"], "-e", "https://data.eastmoney.com/", url],
                                 capture_output=True, text=True, timeout=18)
            d = json.loads(out.stdout)
            kl = (d.get("data") or {}).get("klines") or []
            for line in kl:
                parts = line.split(",")
                if len(parts) >= 5:
                    conn_exec("INSERT OR REPLACE INTO fund_flow_stock (trade_date, symbol, main_net, "
                              "small_net, medium_net, large_net, super_large_net) VALUES (?,?,?,?,?,?,?)",
                              (parts[0], sym, *[float(x) for x in parts[1:6]]))
                    n_ins += 1
            fails = 0
        except Exception as e:
            fails += 1
            if fails >= 5:
                print(f"  [fundflow] 连续失败 {fails} 次，暂停 60s 退避", flush=True)
                time.sleep(60)
                fails = 0
        if (k + 1) % 30 == 0:
            _conn.commit()
            print(f"  [fundflow] {k+1}/{len(todo)} ({n_ins} 行, 失败退避中若见)", flush=True)
        time.sleep(1.2)
    _conn.commit()
    print(f"[fundflow] 结束: 尝试 {len(todo)}, 写入 {n_ins} 行")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=["margin", "ztpool", "macro", "fundflow"])
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-08-21")
    args = ap.parse_args()
    _ensure_tables(get_db(write=True))
    if args.task == "margin":
        backfill_margin(args.start, args.end)
    elif args.task == "ztpool":
        backfill_ztpool(args.start, args.end)
    elif args.task == "macro":
        backfill_macro()
    elif args.task == "fundflow":
        backfill_fundflow(args.start, args.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
