#!/usr/bin/env python3
"""sync_daemon — 数据同步守护进程

增量扫描 screener.db → HTTP POST 到 Dashboard API

表分三类同步频率:
  盘中高频 (60s): intraday_minute, market_breadth_snapshot, sim_pnl_snapshot,
                  sim_portfolio_snapshot, northbound_intraday
  盘后批量 (一次性采集后推送): hsgt_stock_daily, hsgt_sector_daily, hsgt_market_daily,
                            lhb_daily, limit_up_sentiment, futures_basis,
                            etf_flow_daily, block_trades, fund_flow_stock,
                            institution_research
  日频 (每日增量): stock_daily, index_global_daily, futures_daily,
                    hsgt_daily, margin_daily, realtime_pool

自愈: resilient_fetch + CircuitBreaker + 指数退避重试
看门狗: heartbeat('sync_daemon')

用法:
    python -m src.collectors.sync_daemon          # 持续同步
    python -m src.collectors.sync_daemon --once   # 单次运行
"""

import sys
import os
import json
import time
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 确保可以从 src 导入 ─────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import requests

from nous.data.collectors import resilient_fetch, heartbeat, collector_main_loop
from nous.data.storage import get_db

# ── 配置 ────────────────────────────────────────────

# 本地测试 / 生产
LOCAL_API = "http://localhost:3456/api/data/sync"
PROD_API = "http://127.0.0.1/api/data/sync"

# 环境变量覆盖
SYNC_API_URL = os.environ.get("SYNC_API_URL", LOCAL_API)
SYNC_API_KEY = os.environ.get("SYNC_API_KEY", "")

# 状态持久化
STATE_DIR = Path.home() / ".hermes" / "cache"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "sync_state.json"

# 单批最大行数
BATCH_SIZE = 500

# ── 增量检测策略 ────────────────────────────────────
# key: 时间列名
# window: 初始回溯窗口(天)
# interval: 同步频率(秒)

INCREMENTAL_CONFIG = {
    # ═══ 盘中高频 (每60s) ═══
    "intraday_minute": {
        "key": "datetime",
        "window_days": 0.01,  # 最近15分钟
        "interval": 60,
    },
    "market_breadth_snapshot": {
        "key": "datetime",
        "window_days": 0.01,
        "interval": 60,
    },
    "sim_pnl_snapshot": {
        "key": "datetime",
        "window_days": 0.01,
        "interval": 60,
    },
    "sim_portfolio_snapshot": {
        "key": "datetime",
        "window_days": 0.01,
        "interval": 60,
    },
    "northbound_intraday": {
        "key": "datetime",
        "window_days": 0.01,
        "interval": 60,
    },
    # ═══ 盘后批量 (采集完成后一次性推送) ═══
    "hsgt_stock_daily": {
        "key": "trade_date",
        "window_days": 2,
        "interval": 300,
    },
    "hsgt_sector_daily": {
        "key": "trade_date",
        "window_days": 2,
        "interval": 300,
    },
    "hsgt_market_daily": {
        "key": "trade_date",
        "window_days": 2,
        "interval": 300,
    },
    "lhb_daily": {
        "key": "trade_date",
        "window_days": 2,
        "interval": 300,
    },
    "limit_up_sentiment": {
        "key": "trade_date",
        "window_days": 2,
        "interval": 300,
    },
    "futures_basis": {
        "key": "trade_date",
        "window_days": 2,
        "interval": 300,
    },
    "etf_flow_daily": {
        "key": "trade_date",
        "window_days": 2,
        "interval": 300,
    },
    "block_trades": {
        "key": "trade_date",
        "window_days": 2,
        "interval": 300,
    },
    "fund_flow_stock": {
        "key": "trade_date",
        "window_days": 2,
        "interval": 300,
    },
    "institution_research": {
        "key": "announce_date",
        "window_days": 2,
        "interval": 300,
    },
    # ═══ 日频 (每日增量) ═══
    "stock_daily": {
        "key": "trade_date",
        "window_days": 1,
        "interval": 900,
    },
    "index_global_daily": {
        "key": "trade_date",
        "window_days": 1,
        "interval": 900,
    },
    "futures_daily": {
        "key": "trade_date",
        "window_days": 1,
        "interval": 900,
    },
    "hsgt_daily": {
        "key": "trade_date",
        "window_days": 1,
        "interval": 900,
    },
    "margin_daily": {
        "key": "trade_date",
        "window_days": 1,
        "interval": 900,
    },
    "realtime_pool": {
        "key": "added_at",
        "window_days": 1,
        "interval": 900,
    },
}


# ── 同步状态管理 ─────────────────────────────────────

def load_state() -> dict:
    """加载同步状态 (每张表的 last_synced 值)"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict):
    """持久化同步状态"""
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str))


# ── 扫描增量数据 ────────────────────────────────────

def scan_incremental(conn: sqlite3.Connection, table: str, config: dict,
                     last_synced: Optional[str]) -> list[dict]:
    """扫描表增量数据: SELECT * FROM {table} WHERE {key} > ? ORDER BY {key}

    Args:
        conn: 数据库连接
        table: 表名
        config: {key, window_days, interval}
        last_synced: 上次同步的时间点 (None 则回溯 window_days)

    Returns:
        rows: list[dict] — 增量数据行
    """
    key_col = config["key"]

    if last_synced:
        where_clause = f"WHERE {key_col} > ?"
        params = (last_synced,)
    else:
        # 首次同步: 回溯 window_days
        # 根据列名推断格式: datetime列用空格分隔, trade_date列用纯日期
        if "date" in key_col.lower() and "time" not in key_col.lower():
            cutoff = (datetime.now() - timedelta(days=config["window_days"])).strftime("%Y-%m-%d")
        else:
            cutoff = (datetime.now() - timedelta(days=config["window_days"])).strftime("%Y-%m-%d %H:%M:%S")
        where_clause = f"WHERE {key_col} >= ?"
        params = (cutoff,)

    sql = f"SELECT * FROM {table} {where_clause} ORDER BY {key_col}"
    cur = conn.execute(sql, params)
    rows = [dict(row) for row in cur.fetchall()]
    return rows


# ── HTTP POST 推送 ──────────────────────────────────

def push_rows(table: str, rows: list[dict]) -> bool:
    """将行数据 POST 到 Dashboard API, 支持分批发送

    Args:
        table: 表名
        rows: 数据行列表

    Returns:
        bool: 全部推送成功?
    """
    if not rows:
        return True

    total_ok = True
    api_url = SYNC_API_URL
    headers = {"Content-Type": "application/json"}
    if SYNC_API_KEY:
        headers["Authorization"] = f"Bearer {SYNC_API_KEY}"

    # 分批发送
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        payload = {
            "table": table,
            "rows": batch,
            "batch": i // BATCH_SIZE + 1,
            "total_batches": (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE,
        }

        def _post():
            resp = requests.post(api_url, json=payload, headers=headers, timeout=(5, 30))
            resp.raise_for_status()
            return resp.json()

        result, status = resilient_fetch(
            "sync_daemon", _post,
            max_retries=3, base_delay=2.0,
        )

        if not status.get("success"):
            print(f"  [sync] FAILED table={table} batch={payload['batch']}/{payload['total_batches']} "
                  f"error={status.get('error', 'unknown')}", file=sys.stderr)
            total_ok = False
        else:
            print(f"  [sync] OK table={table} batch={payload['batch']}/{payload['total_batches']} "
                  f"rows={len(batch)}", file=sys.stderr)

    return total_ok


# ── 单表同步一轮 ────────────────────────────────────

def sync_table(conn: sqlite3.Connection, table: str, config: dict,
               state: dict) -> Optional[str]:
    """同步一张表: 扫描增量 → POST → 更新 last_synced

    Args:
        conn: 数据库连接
        table: 表名
        config: 增量配置
        state: 全局状态字典 (会被更新)

    Returns:
        新的 last_synced 值, 如果失败返回 None
    """
    key_col = config["key"]
    last_synced = state.get(table)

    # 扫描增量
    rows = scan_incremental(conn, table, config, last_synced)
    if not rows:
        # 无新数据, 但记录 last_synced 以防万一
        return last_synced

    # 推送
    if not push_rows(table, rows):
        return None  # 推送失败, 不更新 last_synced

    # 更新 last_synced = 最后一行的时间列值
    new_last_synced = str(rows[-1][key_col])

    # 去重: 如果新值与旧值相同, 跳过持久化
    if new_last_synced == last_synced:
        return last_synced

    state[table] = new_last_synced
    return new_last_synced


# ── 主同步循环 (单轮) ───────────────────────────────

def sync_all_tables() -> bool:
    """同步所有满足间隔条件的表

    Returns:
        bool: 是否所有表都成功
    """
    state = load_state()
    overall_ok = True

    # 获取当前时间作为"最后检查时间"标记
    now = time.time()

    try:
        conn = get_db(write=False)  # 只读连接
    except Exception as e:
        print(f"[sync_daemon] Cannot open DB: {e}", file=sys.stderr)
        return False

    # 记录哪些表本轮需要同步 (基于 interval)
    tables_to_sync = []
    for table, config in INCREMENTAL_CONFIG.items():
        last_check = state.get(f"_last_check_{table}", 0)
        if now - last_check >= config["interval"]:
            tables_to_sync.append(table)

    if not tables_to_sync:
        conn.close()
        return True  # 没到任何表的同步间隔

    # 验证表是否存在
    existing = set()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    for row in cur.fetchall():
        existing.add(row[0])

    for table in tables_to_sync:
        if table not in existing:
            print(f"  [sync] SKIP table={table} (not in DB)", file=sys.stderr)
            state[f"_last_check_{table}"] = now
            continue

        config = INCREMENTAL_CONFIG[table]
        try:
            new_last = sync_table(conn, table, config, state)
            if new_last is None:
                overall_ok = False
            state[f"_last_check_{table}"] = now
        except Exception as e:
            print(f"  [sync] ERROR table={table}: {type(e).__name__}: {e}", file=sys.stderr)
            overall_ok = False
            state[f"_last_check_{table}"] = now

    conn.close()

    # 持久化状态
    save_state(state)
    return overall_ok


# ── 单次同步函数 (供 collector_main_loop 调用) ────

def collect_once() -> bool:
    """包装函数: 同步全部 → 返回成功/失败"""
    return sync_all_tables()


# ═══════════════════════════════════════════════════
# 独立入口
# ═══════════════════════════════════════════════════

def main():
    """独立运行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="数据同步守护进程 — 增量推送 screener.db → Dashboard API")
    parser.add_argument("--once", action="store_true", help="仅运行一次, 不进入循环")
    parser.add_argument("--api-url", default=None, help="覆盖 API URL (默认: localhost:3456)")
    parser.add_argument("--api-key", default=None, help="覆盖 API Key")
    args = parser.parse_args()

    global SYNC_API_URL, SYNC_API_KEY
    if args.api_url:
        SYNC_API_URL = args.api_url
    if args.api_key:
        SYNC_API_KEY = args.api_key

    print(f"[sync_daemon] Starting sync daemon", file=sys.stderr)
    print(f"[sync_daemon] API: {SYNC_API_URL}", file=sys.stderr)
    print(f"[sync_daemon] State: {STATE_FILE}", file=sys.stderr)
    print(f"[sync_daemon] Tables: {len(INCREMENTAL_CONFIG)} configured", file=sys.stderr)

    if args.once:
        ok = sync_all_tables()
        sys.exit(0 if ok else 1)
    else:
        # 使用 collector_main_loop 进入持续模式 (含心跳 + 自愈 + 看门狗)
        # 最小间隔取所有表中最小值, 实际 sync_all_tables 内部按 interval 调度
        min_interval = min(c["interval"] for c in INCREMENTAL_CONFIG.values())
        collector_main_loop(
            name="sync_daemon",
            collect_fn=collect_once,
            interval_seconds=min_interval,
            max_consecutive_failures=10,
        )


if __name__ == "__main__":
    main()
