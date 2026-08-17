#!/usr/bin/env python3
"""
北向/南向个股TOP50 采集器 v2.

使用 resilient_fetch + CircuitBreaker 采集东方财富沪深港通个股排行数据，
写入 hsgt_stock_daily 表，新增推算字段。

数据源:
  RPT_MUTUAL_STOCK_HOLDRANKS 底层 API
  北向: MUTUAL_TYPE='002'(深股通), '004'(沪股通)
  南向: MUTUAL_TYPE='005'(港股通沪), '006'(港股通深)

表结构: hsgt_stock_daily
  trade_date, symbol, direction, rank, net_inflow, change_pct,
  estimated_net_buy, estimated_net_buy_direction,
  holding_market_cap, holding_pct, confidence, industry

采集频率：交易日每小时，由看门狗调度。
"""

import sys
import os
import time
from datetime import datetime, date, timedelta
from typing import Optional

# ── 路径 ────────────────────────────────────────────────
from nous.core.paths import repo_root

PROJECT_DIR = str(repo_root())
sys.path.insert(0, PROJECT_DIR)

import requests

from nous.data.collectors import resilient_fetch, CircuitBreaker, heartbeat
from nous.data.storage import get_db, with_retry

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

# 熔断器
EM_CB = CircuitBreaker("em", failure_threshold=3, cooldown_seconds=180)

# 进程名
PROCESS_NAME = "hsgt_stock_collector"

# MUTUAL_TYPE → direction 映射
# 002=深股通(北向), 004=沪股通(北向)
# 005=港股通沪(南向), 006=港股通深(南向)
MUTUAL_TYPE_DIRECTION = {
    "002": "北向",
    "004": "北向",
    "005": "南向",
    "006": "南向",
}


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def yesterday_str() -> str:
    return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ══════════════════════════════════════════════════════════
#  API 采集 — 统一使用 RPT_MUTUAL_STOCK_HOLDRANKS
# ══════════════════════════════════════════════════════════

def fetch_stock_holdranks_raw(day_str: str) -> list[dict]:
    """调用东方财富 datacenter 获取全部通道的个股排行。
    
    优先 RPT_MUTUAL_STOCK_HOLDRANKS, 失败后轮询备选报表。
    返回原始记录列表（含 002/004/005/006 四种 MUTUAL_TYPE）。
    """
    # 报表名优先级: 主表→备选1→备选2→备选3
    REPORT_NAMES = [
        "RPT_MUTUAL_STOCK_HOLDRANKS",      # 主: TOP50个股排行 ✅
        "RPT_MUTUAL_STOCK_HSGTSTAT",       # 备1: 沪深港通统计
        "RPT_MUTUAL_STOCK_HOLDETAIL",      # 备2: 持股明细
        "RPT_MUTUAL_STOCK_HSGTSUM",        # 备3: 沪深港通汇总
    ]

    base_params = {
        "sortColumns": "ADD_MARKET_CAP",
        "sortTypes": "-1",
        "pageSize": "100",
        "pageNumber": "1",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": "(INTERVAL_TYPE=\"1\")(RN=1)(TRADE_DATE='%s')" % day_str,
    }

    for report_name in REPORT_NAMES:
        params = base_params.copy()
        params["reportName"] = report_name

        def _fetch(p=params):
            r = requests.get(API_URL, params=p, headers=HEADERS, timeout=30)
            j = r.json()
            if j.get("success") and j.get("result") and j["result"].get("data"):
                return j["result"]["data"]
            return []

        result, status = resilient_fetch(
            "em", _fetch, max_retries=2, base_delay=1.0,
        )

        if status["success"] and result:
            if report_name != REPORT_NAMES[0]:
                print(f"  [API] {report_name} 命中(fallback)", file=sys.stderr)
            return result
        else:
            err = status.get("error", "empty")
            print(f"  [API] {report_name} 失败: {err}", file=sys.stderr)

    print("  [API] 所有报表名均失败", file=sys.stderr)
    return []


def parse_holdranks(raw: list[dict], day_str: str) -> list[dict]:
    """解析原始记录为统一格式，提取推算字段。
    
    从 API 返回的原始记录中提取:
      - HOLD_MARKET_CAP → holding_market_cap (持股市值)
      - HOLD_SHARES_RATIO → holding_pct (持股占比)
      - ADD_MARKET_CAP → estimated_net_buy (当日增持市值)
      - 根据 ADD_MARKET_CAP 正负号判断 estimated_net_buy_direction
      - INDUSTRY → industry (行业分类，用于后续板块聚合)
    """
    records = []
    for i, row in enumerate(raw):
        mutual_type = row.get("MUTUAL_TYPE", "")
        direction = MUTUAL_TYPE_DIRECTION.get(mutual_type)
        if not direction:
            continue

        add_market_cap = _safe_float(row.get("ADD_MARKET_CAP"))
        close_price = _safe_float(row.get("CLOSE_PRICE"))

        # 确定 estimated_net_buy_direction
        if add_market_cap is not None:
            if add_market_cap > 0:
                net_buy_dir = "buy"
            elif add_market_cap < 0:
                net_buy_dir = "sell"
            else:
                net_buy_dir = "neutral"
        else:
            net_buy_dir = "neutral"

        # confidence: ADD_MARKET_CAP 绝对值 > 0 → 'high', 否则 'medium'
        if add_market_cap is not None and abs(add_market_cap) > 0:
            confidence = "high"
        else:
            confidence = "medium"

        symbol = str(row.get("SECURITY_CODE", "")).strip()
        if not symbol:
            continue

        # 南向(southbound, 005/006) 保持港股格式(5位数字)
        # 北向(northbound, 002/004) 保持原格式(6位A股代码)
        # 不需要转换

        records.append({
            "trade_date": day_str,
            "symbol": symbol,
            "direction": direction,
            "rank": i + 1,
            "net_inflow": add_market_cap,
            "change_pct": _safe_float(row.get("CHANGE_RATE")),
            "estimated_net_buy": add_market_cap,
            "estimated_net_buy_direction": net_buy_dir,
            "holding_market_cap": _safe_float(row.get("HOLD_MARKET_CAP")),
            "holding_pct": _safe_float(row.get("HOLD_SHARES_RATIO")),
            "confidence": confidence,
            "industry": str(row.get("INDUSTRY", "")) if row.get("INDUSTRY") else "",
        })

    return records[:200]


def collect_all_stock() -> list[dict]:
    """采集所有通道的个股排行数据。"""
    day = yesterday_str()
    # 如果是周末/节假日，回溯到最近交易日
    from datetime import date as dt_date, timedelta as dt_timedelta
    ref = dt_date.today() - dt_timedelta(days=1)
    for _ in range(10):
        if ref.weekday() < 5:  # 周一至周五
            day = ref.strftime("%Y-%m-%d")
            break
        ref -= dt_timedelta(days=1)
    raw = fetch_stock_holdranks_raw(day)
    if not raw:
        print("  [个股] 当日('%s')无数据" % day)
        return []
    records = parse_holdranks(raw, day)
    # 统计
    north = sum(1 for r in records if r["direction"] == "北向")
    south = sum(1 for r in records if r["direction"] == "南向")
    print("  [个股] 北向 %d 条, 南向 %d 条, 共 %d 条" % (north, south, len(records)))
    return records


# ══════════════════════════════════════════════════════════
#  写库
# ══════════════════════════════════════════════════════════

def ensure_columns(conn):
    """ALTER TABLE 添加新列（如不存在）。"""
    migrations = [
        "ALTER TABLE hsgt_stock_daily ADD COLUMN estimated_net_buy REAL",
        "ALTER TABLE hsgt_stock_daily ADD COLUMN estimated_net_buy_direction TEXT",
        "ALTER TABLE hsgt_stock_daily ADD COLUMN holding_market_cap REAL",
        "ALTER TABLE hsgt_stock_daily ADD COLUMN holding_pct REAL",
        "ALTER TABLE hsgt_stock_daily ADD COLUMN confidence TEXT",
        "ALTER TABLE hsgt_stock_daily ADD COLUMN industry TEXT",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass  # 列已存在
    conn.commit()


@with_retry(max_attempts=3)
def save_hsgt_stock(records: list[dict]) -> int:
    """写入 hsgt_stock_daily 表。"""
    if not records:
        return 0
    conn = get_db(write=True)
    ensure_columns(conn)

    sql = """INSERT OR REPLACE INTO hsgt_stock_daily
(trade_date, symbol, direction, rank, net_inflow, change_pct,
 estimated_net_buy, estimated_net_buy_direction,
 holding_market_cap, holding_pct, confidence, industry)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    count = 0
    try:
        for r in records:
            try:
                conn.execute(sql, (
                    r.get("trade_date"), r.get("symbol"), r.get("direction"),
                    r.get("rank"), r.get("net_inflow"), r.get("change_pct"),
                    r.get("estimated_net_buy"), r.get("estimated_net_buy_direction"),
                    r.get("holding_market_cap"), r.get("holding_pct"),
                    r.get("confidence"), r.get("industry"),
                ))
                count += 1
            except Exception as e:
                print("  [写入] 跳过 %s: %s" % (r.get("symbol"), e), file=sys.stderr)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return count


# ══════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════

def main() -> bool:
    """一次采集完整流程，返回是否全部成功。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 50)
    print("[%s] hsgt_stock_collector v2 启动" % ts)
    print("=" * 50)

    # 心跳
    heartbeat(PROCESS_NAME)

    record_count = 0
    errors = []

    # 采集所有通道个股排行
    print("\n--- 沪深港通个股 TOP ---")
    try:
        records = collect_all_stock()
        if records:
            saved = save_hsgt_stock(records)
            record_count += saved
            print("  [写入] %d 条 (hsgt_stock_daily)" % saved)
        else:
            print("  [个股] 无数据")
    except Exception as e:
        print("  [个股] 异常: %s" % e, file=sys.stderr)
        errors.append("collect: %s" % e)

    # 最终报告
    print("\n--- 完成 ---")
    if record_count > 0:
        print("  写入总量: %d 条 (hsgt_stock_daily)" % record_count)
    if errors:
        print("  错误: %d 个" % len(errors))
        for e in errors:
            print("    - %s" % e)

    # 心跳
    heartbeat(PROCESS_NAME)
    return len(errors) == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
