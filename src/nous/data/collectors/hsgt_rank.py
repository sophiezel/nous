#!/usr/bin/env python3
"""
北向/南向板块排行+个股前10 全量历史回补脚本.
从 2020-01-01 回补到昨日，写入 hsgt_board_daily 和 hsgt_stock_daily.
支持断点续传、幂等 (INSERT OR IGNORE)、同日并发 (threading).

已验证的 AKShare 接口:
  ✅ stock_hsgt_board_rank_em(symbol='北向资金增持行业板块排行', indicator='今日')
     -> 通过底层 API 直接调用, 支持自定义 TRADE_DATE
     -> 字段: BOARD_NAME, ADD_MARKET_CAP(净流入), INDEX_CHANGE_RATIO(涨跌幅)

  ✅ stock_hsgt_stock_statistics_em(symbol='南向持股', start_date, end_date)
     -> 返回南向个股每日持股明细
     -> 字段: 持股日期, 股票代码, 当日涨跌幅, 持股市值变化-1日

  ❌ stock_hsgt_stock_statistics_em(symbol='北向持股', ...)
     -> RPT_MUTUAL_STOCK_NORTHSTA API 返回 "服务器繁忙" (已失效)
     -> 使用南向报告 (RPT_MUTUAL_STOCK_HOLDRANKS) 替代获取北向个股排行

  ❌ stock_hsgt_board_rank_em 不支持南向板块参数
     -> 南向板块暂无接口可用

使用:
  python3 hsgt_rank.py                    # 前台运行 (验证+回补)
  nohup python3 hsgt_rank.py &            # 后台运行
  python3 hsgt_rank.py --validate-only    # 仅验证
  python3 hsgt_rank.py --no-validate      # 跳过验证直接回补
"""

from __future__ import annotations

import os
import sys
import sqlite3
import time
import logging
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd

# ── 路径 ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "data", "screener.db")

# ── 日志 ────────────────────────────────────────────────
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(BASE_DIR, "logs", "hsgt_rank.log"), mode="a"),
    ],
)
log = logging.getLogger(__name__)

# ── 配置 ────────────────────────────────────────────────
START_DATE = date(2020, 1, 1)
YESTERDAY = date.today() - timedelta(days=1)
API_SLEEP = 0.5  # 天级间隔(秒)
MAX_WORKERS = 4  # 同日并发任务数

# 东方财富 API 端点
API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/hsgtcg/",
}


# ══════════════════════════════════════════════════════════
#  1. 接口验证
# ══════════════════════════════════════════════════════════
def validate_apis():
    """验证各数据源可用性, 打印列名和前3行."""
    log.info("=" * 60)
    log.info("验证 AKShare / 底层 API 接口...")
    log.info("=" * 60)

    # 1) 北向板块
    try:
        latest = _fetch_board_latest_date()
        if latest:
            rows = _fetch_north_board_raw(latest)
            log.info(f"[OK] 北向板块 API (最新数据日: {latest})")
            log.info(f"  {len(rows)} 条板块记录")
            if rows:
                log.info(f"  Columns: {list(rows[0].keys())}")
                for r in rows[:3]:
                    log.info(f"    {r}")
        else:
            log.warning("[WARN] 北向板块 API 无数据返回")
    except Exception as e:
        log.warning(f"[WARN] 北向板块 API 验证失败: {e}")

    # 2) 南向个股 RPT_MUTUAL_STOCK_HOLDRANKS (同时获取北向个股合并数据)
    try:
        from akshare import stock_hsgt_stock_statistics_em
        df = stock_hsgt_stock_statistics_em(
            symbol="南向持股", start_date="20240815", end_date="20240816"
        )
        log.info(f"[OK] stock_hsgt_stock_statistics_em(南向持股)")
        log.info(f"  Columns: {list(df.columns)}")
        log.info(f"  Shape: {df.shape}")
        log.info(f"  Head:\n{df.head(3).to_string()}")
    except Exception as e:
        log.warning(f"[WARN] 南向持股 API 验证失败: {e}")

    # 3) 验证 RPT_MUTUAL_STOCK_HOLDRANKS 是否含北向数据
    try:
        rows_raw = _fetch_stock_holdranks_raw("2024-08-16")
        if rows_raw:
            mutual_types = set(r.get("MUTUAL_TYPE") for r in rows_raw)
            log.info(f"[OK] RPT_MUTUAL_STOCK_HOLDRANKS 返回 MUTUAL_TYPE: {mutual_types}")
            log.info(f"  总记录数: {len(rows_raw)}")
        else:
            log.warning("[WARN] RPT_MUTUAL_STOCK_HOLDRANKS 无数据")
    except Exception as e:
        log.warning(f"[WARN] RPT_MUTUAL_STOCK_HOLDRANKS 验证失败: {e}")

    log.info("接口验证完成.\n")


def _fetch_board_latest_date() -> "str | None":
    """查询北向板块 API 最新可用日期."""
    params = {
        "sortColumns": "TRADE_DATE",
        "sortTypes": "-1",
        "pageSize": "1",
        "pageNumber": "1",
        "reportName": "RPT_MUTUAL_BOARD_HOLDRANK_WEB",
        "columns": "ALL",
        "quoteColumns": "f3~05~SECURITY_CODE~INDEX_CHANGE_RATIO",
        "source": "WEB",
        "client": "WEB",
        "filter": '(BOARD_TYPE="5")(INTERVAL_TYPE="1")',
    }
    r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
    j = r.json()
    if j.get("success") and j.get("result") and j["result"].get("data"):
        return j["result"]["data"][0]["TRADE_DATE"][:10]
    return None


# ══════════════════════════════════════════════════════════
#  2. 数据获取 (底层 API 调用)
# ══════════════════════════════════════════════════════════
def _fetch_north_board_raw(day_str: str) -> list[dict]:
    """
    调用 RPT_MUTUAL_BOARD_HOLDRANK_WEB 获取指定日期北向板块排行.
    返回原始记录列表.
    """
    params = {
        "sortColumns": "ADD_MARKET_CAP",
        "sortTypes": "-1",
        "pageSize": "500",
        "pageNumber": "1",
        "reportName": "RPT_MUTUAL_BOARD_HOLDRANK_WEB",
        "columns": "ALL",
        "quoteColumns": "f3~05~SECURITY_CODE~INDEX_CHANGE_RATIO",
        "source": "WEB",
        "client": "WEB",
        "filter": f'(BOARD_TYPE="5")(TRADE_DATE=\'{day_str}\')(INTERVAL_TYPE="1")',
    }
    r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
    j = r.json()
    if j.get("success") and j.get("result") and j["result"].get("data"):
        return j["result"]["data"]
    return []


def fetch_north_board(day_str: str) -> list[dict]:
    """
    获取北向板块排行数据.
    返回 [{trade_date, board_name, direction, net_inflow, rank, change_pct}, ...]
    """
    raw = _fetch_north_board_raw(day_str)
    records = []
    for i, row in enumerate(raw):
        change_pct = _safe_float(row.get("INDEX_CHANGE_RATIO"))
        net_inflow = _safe_float(row.get("ADD_MARKET_CAP"))
        records.append({
            "trade_date": day_str,
            "board_name": row.get("BOARD_NAME", ""),
            "direction": "北向",
            "net_inflow": net_inflow,
            "rank": i + 1,
            "change_pct": change_pct,
        })
    return records


def _fetch_stock_holdranks_raw(day_str: str) -> list[dict]:
    """
    调用 RPT_MUTUAL_STOCK_HOLDRANKS 获取指定日期个股排行.
    (同时包含南向 MUTUAL_TYPE='002','004')
    """
    params = {
        "sortColumns": "ADD_MARKET_CAP",
        "sortTypes": "-1",
        "pageSize": "500",
        "pageNumber": "1",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": f'(INTERVAL_TYPE="1")(RN=1)(TRADE_DATE=\'{day_str}\')',
        "reportName": "RPT_MUTUAL_STOCK_HOLDRANKS",
    }
    r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
    j = r.json()
    if j.get("success") and j.get("result") and j["result"].get("data"):
        return j["result"]["data"]
    return []


def fetch_stock_top10(day_str: str) -> list[dict]:
    """
    获取南向个股前10 (通过 RPT_MUTUAL_STOCK_HOLDRANKS).
    排序依据: ADD_MARKET_CAP (持股市值变化)
    返回 [{trade_date, symbol, direction, rank, net_inflow, change_pct}, ...]
    """
    raw = _fetch_stock_holdranks_raw(day_str)
    if not raw:
        return []

    # 取前10 (已按 ADD_MARKET_CAP 降序)
    top10 = raw[:10]
    records = []
    for i, row in enumerate(top10):
        records.append({
            "trade_date": day_str,
            "symbol": row.get("SECURITY_CODE", ""),
            "direction": "南向",
            "rank": i + 1,
            "net_inflow": _safe_float(row.get("ADD_MARKET_CAP")),
            "change_pct": _safe_float(row.get("CHANGE_RATE")),
        })
    return records


def _safe_float(value):
    """安全转换为 float, 失败返回 None."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ══════════════════════════════════════════════════════════
#  3. 同日并发聚合
# ══════════════════════════════════════════════════════════
def fetch_day(day_str: str) -> dict:
    """并发获取单日所有数据, 返回 {board: [...], stock: [...]}."""
    results: "dict[str, list]" = {"board": [], "stock": []}
    tasks = {
        "north_board": fetch_north_board,
        "south_stocks": fetch_stock_top10,
    }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        fut_map = {executor.submit(fn, day_str): key for key, fn in tasks.items()}
        for fut in as_completed(fut_map):
            key = fut_map[fut]
            try:
                data = fut.result()
                if "board" in key:
                    results["board"].extend(data)
                else:
                    results["stock"].extend(data)
            except Exception as e:
                log.warning(f"  [{key}] 任务异常 ({day_str}): {e}")

    return results


# ══════════════════════════════════════════════════════════
#  4. 数据库操作
# ══════════════════════════════════════════════════════════
def get_max_date(conn: sqlite3.Connection, table: str) -> "str | None":
    """查询表中已存在的最大 trade_date (用于断点续传)."""
    try:
        cur = conn.execute(f"SELECT MAX(trade_date) FROM {table}")
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def ensure_tables(conn: sqlite3.Connection):
    """确保目标表存在 (含唯一约束实现幂等)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hsgt_board_daily (
            trade_date TEXT NOT NULL,
            board_name TEXT NOT NULL,
            direction TEXT NOT NULL,
            net_inflow REAL,
            rank INTEGER,
            change_pct REAL,
            PRIMARY KEY (trade_date, board_name, direction)
        );
        CREATE TABLE IF NOT EXISTS hsgt_stock_daily (
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            rank INTEGER,
            net_inflow REAL,
            change_pct REAL,
            PRIMARY KEY (trade_date, symbol, direction)
        );
    """)
    conn.commit()


def insert_board_records(conn: sqlite3.Connection, records: list[dict]) -> int:
    """INSERT OR IGNORE 写入板块数据. 返回插入行数."""
    if not records:
        return 0
    sql = """
        INSERT OR IGNORE INTO hsgt_board_daily
            (trade_date, board_name, direction, net_inflow, rank, change_pct)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    rows = [
        (r["trade_date"], r["board_name"], r["direction"],
         r["net_inflow"], r["rank"], r["change_pct"])
        for r in records
    ]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def insert_stock_records(conn: sqlite3.Connection, records: list[dict]) -> int:
    """INSERT OR IGNORE 写入个股数据. 返回插入行数."""
    if not records:
        return 0
    sql = """
        INSERT OR IGNORE INTO hsgt_stock_daily
            (trade_date, symbol, direction, rank, net_inflow, change_pct)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    rows = [
        (r["trade_date"], r["symbol"], r["direction"],
         r["rank"], r["net_inflow"], r["change_pct"])
        for r in records
    ]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


# ══════════════════════════════════════════════════════════
#  5. 主回补循环
# ══════════════════════════════════════════════════════════
def backfill():
    """主回补循环: 逐日获取数据并写入 DB."""
    conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)

    # 断点续传: 取两表中最大的已有日期
    max_board = get_max_date(conn, "hsgt_board_daily")
    max_stock = get_max_date(conn, "hsgt_stock_daily")
    resume_date_str = max(max_board or "", max_stock or "")

    if resume_date_str:
        try:
            resume_date = datetime.strptime(resume_date_str, "%Y-%m-%d").date()
            start = resume_date + timedelta(days=1)
            log.info(f"断点续传: 已有数据至 {resume_date_str}, 从 {start} 开始")
        except ValueError:
            start = START_DATE
            log.info(f"日期解析失败, 回退从 {start} 开始")
    else:
        start = START_DATE
        log.info(f"全量回补: {start} ~ {YESTERDAY}")

    if start > YESTERDAY:
        log.info("数据已是最新, 无需回补.")
        conn.close()
        return

    # 生成日期序列
    all_dates: list[date] = []
    d = start
    while d <= YESTERDAY:
        all_dates.append(d)
        d += timedelta(days=1)

    total = len(all_dates)
    log.info(f"共 {total} 天需要回补 ({start} ~ {YESTERDAY})")

    board_total = 0
    stock_total = 0
    error_days: "list[str]" = []

    for i, d in enumerate(all_dates, start=1):
        day_str = d.strftime("%Y-%m-%d")
        try:
            results = fetch_day(day_str)
            b_cnt = insert_board_records(conn, results["board"])
            s_cnt = insert_stock_records(conn, results["stock"])
            board_total += b_cnt
            stock_total += s_cnt

            if i % 20 == 0 or i == 1 or i == total:
                log.info(
                    f"进度 [{i}/{total}] {day_str} | "
                    f"板块+{b_cnt}条 | 个股+{s_cnt}条 | "
                    f"累计: 板块={board_total} 个股={stock_total}"
                )

            time.sleep(API_SLEEP)
        except Exception as e:
            log.error(f"[{day_str}] 处理失败: {e}")
            error_days.append(day_str)
            time.sleep(API_SLEEP)

    # 最终报告
    log.info("=" * 60)
    log.info("回补完成!")
    log.info(f"  日期范围: {start} ~ {YESTERDAY}")
    log.info(f"  板块写入: {board_total} 条 (hsgt_board_daily)")
    log.info(f"  个股写入: {stock_total} 条 (hsgt_stock_daily)")
    log.info(f"  成功天数: {total - len(error_days)}/{total}")
    if error_days:
        log.warning(f"  失败天数({len(error_days)}): {error_days[:20]}{'...' if len(error_days) > 20 else ''}")
    else:
        log.info("  失败天数: 0 (全部成功)")
    log.info("=" * 60)

    conn.close()


# ══════════════════════════════════════════════════════════
#  6. 入口
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="沪深港通板块/个股排行回补脚本")
    parser.add_argument("--validate-only", action="store_true", help="仅验证接口, 不执行回补")
    parser.add_argument("--no-validate", action="store_true", help="跳过验证, 直接回补")
    args = parser.parse_args()

    if not args.no_validate:
        validate_apis()
        if args.validate_only:
            log.info("仅验证模式, 退出.")
            sys.exit(0)

    backfill()
