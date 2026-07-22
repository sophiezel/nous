#!/usr/bin/env python3
"""minute_collector — 分钟级行情采集 (09:31-15:00 每60s)

从 realtime_pool 读取活跃标的, 通过 Sina 实时行情 API 获取行情,
写入 intraday_minute 表, 同时更新 market_breadth_snapshot (涨跌家数/涨跌比).

数据流:
  realtime_pool (active=1) → Sina API (resilient_fetch) → intraday_minute
                                                         → market_breadth_snapshot

只拉池内标的 (~150只), 非全量 5514 只.

自愈: resilient_fetch('sina', ...) + CircuitBreaker + 指数退避重试
看门狗: heartbeat('minute_collector')

单独运行 (采集一次):
    python -m src.collectors.minute_collector

持续模式:
    python -m src.collectors.minute_collector --loop
"""

from __future__ import annotations

import sys
import os
import time
import re
import sqlite3
from datetime import datetime, date
from pathlib import Path

# ── 确保可以从 src 导入 ─────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import requests

from nous.data.collectors import resilient_fetch, heartbeat, collector_main_loop
from nous.data.storage import get_db

# ── 配置 ────────────────────────────────────────────

SINA_URL = "https://hq.sinajs.cn/list={codes}"
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Sina 行情 API 前缀映射: 数字前缀 → 交易所前缀
MARKET_PREFIX = {
    "6": "sh",
    "5": "sh",
    "9": "sh",
    "0": "sz",
    "3": "sz",
    "2": "sz",
    "8": "bj",
    "4": "bj",
}

# 最大单次请求symbol数 (Sina限制约200个)
BATCH_SIZE = 200

# 表 DDL (确保存在)
DDL_INTRADAY = """
CREATE TABLE IF NOT EXISTS intraday_minute (
    symbol    TEXT NOT NULL,
    datetime  TEXT NOT NULL,
    price     REAL,
    volume    REAL,
    amount    REAL,
    pct_change REAL,
    PRIMARY KEY (symbol, datetime)
);
CREATE INDEX IF NOT EXISTS idx_minute_symbol ON intraday_minute(symbol, datetime);
"""

DDL_BREADTH = """
CREATE TABLE IF NOT EXISTS market_breadth_snapshot (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    datetime          TEXT NOT NULL,
    up_count          INTEGER DEFAULT 0,
    down_count        INTEGER DEFAULT 0,
    flat_count        INTEGER DEFAULT 0,
    limit_up_count    INTEGER DEFAULT 0,
    limit_down_count  INTEGER DEFAULT 0,
    board_break_rate  REAL DEFAULT 0.0,
    max_board_height  INTEGER DEFAULT 0,
    turnover_top50_pct REAL DEFAULT 0.0,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_market_breadth_dt ON market_breadth_snapshot(datetime);
"""


# ══════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════

def symbol_to_sina(symbol: str) -> str | None:
    """将纯数字代码转为 Sina API 格式 (sh600000 / sz000001)

    非A股代码 (含字母前缀如 HK/HSTECH/KWEB/IF00) 返回 None。
    """
    # 如果是纯数字, 补齐6位
    if symbol.isdigit():
        sym = symbol.zfill(6)
        prefix = None
        for k, v in MARKET_PREFIX.items():
            if sym.startswith(k):
                prefix = v
                break
        if prefix is None:
            prefix = "sz"  # 默认深市
        return f"{prefix}{sym}"

    # 如果是 sh/sz 前缀的指数代码
    if symbol.startswith(("sh", "sz", "SH", "SZ")):
        return symbol.lower()

    # 非A股标的 (HK, HSTECH, KWEB, IF00 等) → 不支持 Sina 实时行情
    return None


def sina_to_symbol(sina_code: str) -> str:
    """将 Sina 格式转回纯数字"""
    # 去掉 sh/sz/bj 前缀
    match = re.match(r'^[a-z]{2}(\d+)$', sina_code)
    if match:
        return match.group(1)
    return sina_code


def parse_sina_response(text: str) -> list[dict]:
    """解析 Sina 行情 API 返回的 GBK 文本

    Args:
        text: Sina 原始响应文本 (GBK编码)

    Returns:
        [{symbol, price, volume, amount, pct_change}, ...]

    指数格式:
        var hq_str_sh000001="上证指数,3436.32,3435.01,3449.32,3450.29,3433.56,0,0,357364646,4126400104,...,2026-05-17,15:00:00";
        字段: name, open, prev_close, price, high, low, ..., volume(手), amount(元), ..., date, time

    个股格式:
        var hq_str_sh600519="贵州茅台,1800.00,1795.00,1810.00,1815.00,1798.00,...,18189000,328400000,...";
        字段: name, open, prev_close, price, high, low, ..., volume(手), amount(元), ...
    """
    results = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue

        # 解析: var hq_str_sh000001="...";
        match = re.match(r'var hq_str_(\w+)="(.+)"', line)
        if not match:
            continue

        sina_code = match.group(1)
        fields = match.group(2).split(",")

        # 至少需要10个字段: name(0), open(1), prev_close(2), price(3),
        # high(4), low(5), ..., volume(8), amount(9)
        if len(fields) < 10:
            continue

        try:
            prev_close = float(fields[2]) if fields[2] else 0
            price = float(fields[3]) if fields[3] else 0
            # 涨跌幅
            pct_change = round((price - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0.0
            # 成交量(手)
            volume = float(fields[8]) if fields[8] else 0
            # 成交额(元)
            amount = float(fields[9]) if fields[9] else 0

            # 判断是指数还是个股: 指数通常在字段30-31有日期时间, volume/amount可能为0
            is_index = (volume == 0 and amount == 0)

            results.append({
                "symbol": sina_to_symbol(sina_code),
                "price": price,
                "volume": volume,
                "amount": amount,
                "pct_change": pct_change,
                "is_index": is_index,
            })
        except (ValueError, IndexError):
            continue

    return results


def collect_market_breadth(quotes: list[dict]) -> dict:
    """从行情数据计算涨跌家数/涨跌比

    Args:
        quotes: parse_sina_response 返回的数据列表

    Returns:
        {up_count, down_count, flat_count, up_down_ratio}
    """
    # 只对非指数标的统计涨跌家数
    stocks = [q for q in quotes if not q.get("is_index", False) and q["price"] > 0]

    up_count = sum(1 for q in stocks if q["pct_change"] > 0)
    down_count = sum(1 for q in stocks if q["pct_change"] < 0)
    flat_count = sum(1 for q in stocks if q["pct_change"] == 0)

    total_up_down = up_count + down_count
    up_down_ratio = round(up_count / total_up_down, 4) if total_up_down > 0 else 0.0

    return {
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "up_down_ratio": up_down_ratio,
    }


# ══════════════════════════════════════════════════════
# 核心采集逻辑
# ══════════════════════════════════════════════════════

def fetch_sina_quotes(sina_codes: list[str]) -> str:
    """调用 Sina 行情 API (供 resilient_fetch 使用)

    Args:
        sina_codes: 带交易所前缀的代码列表, 如 ['sh000001','sz000001','sh600519']

    Returns:
        原始响应文本 (GBK编码)
    """
    url = SINA_URL.format(codes=",".join(sina_codes))
    resp = requests.get(url, headers=SINA_HEADERS, timeout=15)
    resp.encoding = "gbk"
    return resp.text


def collect_minute_data() -> int:
    """采集一次分时数据: 读取池 → 请求Sina → 写入DB → 更新涨跌统计

    Returns:
        成功写入的行数
    """
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 60)
    print(f" minute_collector — {now_str}")
    print("=" * 60)

    # 1. 读取活跃池
    conn = get_db(write=False)
    try:
        rows = conn.execute(
            "SELECT symbol, pool_source, weight FROM realtime_pool WHERE active=1 ORDER BY symbol"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("  [minute_collector] realtime_pool 无活跃标的", file=sys.stderr)
        return 0

    pool_symbols = [r["symbol"] for r in rows]
    print(f"  [minute_collector] 池中 {len(pool_symbols)} 个活跃标的")

    # 2. 过滤出A股标的 (可被Sina查询)
    sina_ready = []
    skipped = []
    for sym in pool_symbols:
        sina_code = symbol_to_sina(sym)
        if sina_code:
            sina_ready.append(sina_code)
        else:
            skipped.append(sym)

    if skipped:
        print(f"  [minute_collector] 跳过非A股 {len(skipped)} 只: {', '.join(skipped[:10])}", file=sys.stderr)

    if not sina_ready:
        print("  [minute_collector] 无可查询的A股标的", file=sys.stderr)
        return 0

    # 3. 分批请求Sina API (每批最多200个)
    all_quotes = []
    for i in range(0, len(sina_ready), BATCH_SIZE):
        batch = sina_ready[i:i + BATCH_SIZE]

        def _fetch() -> str:
            return fetch_sina_quotes(batch)

        result, status = resilient_fetch(
            "sina", _fetch,
            fallback_fn=lambda: "",
            max_retries=3, base_delay=1.0,
        )

        if not status.get("success") or not result:
            print(f"  [minute_collector] 批次 {i//BATCH_SIZE+1} 获取失败: {status.get('error', 'unknown')}",
                  file=sys.stderr)
            continue

        batch_quotes = parse_sina_response(result)
        all_quotes.extend(batch_quotes)
        print(f"  [minute_collector] 批次 {i//BATCH_SIZE+1}/{len(sina_ready)//BATCH_SIZE+1}: "
              f"请求{len(batch)}个, 解析{len(batch_quotes)}个")

        # 批次间短暂间隔
        if i + BATCH_SIZE < len(sina_ready):
            time.sleep(0.3)

    if not all_quotes:
        print("  [minute_collector] ⚠ 未获取到任何行情数据", file=sys.stderr)
        return 0

    print(f"  [minute_collector] API 获取到 {len(all_quotes)} 个行情数据")

    # 4. 写入 intraday_minute
    conn = get_db(write=True)
    try:
        conn.executescript(DDL_INTRADAY)

        inserted = 0
        for q in all_quotes:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO intraday_minute (symbol, datetime, price, volume, amount, pct_change) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (q["symbol"], now_str, q["price"], q["volume"], q["amount"], q["pct_change"]),
                )
                inserted += 1
            except Exception as e:
                print(f"  [minute_collector] 写入 {q['symbol']} 失败: {e}", file=sys.stderr)

        conn.commit()
        print(f"  [minute_collector] intraday_minute 写入: {inserted} 条")
    finally:
        conn.close()

    # 5. 计算并写入 market_breadth_snapshot
    breadth = collect_market_breadth(all_quotes)
    print(f"  [minute_collector] 涨跌: ↑{breadth['up_count']} ↓{breadth['down_count']} "
          f"—{breadth['flat_count']} | 比={breadth['up_down_ratio']:.2%}")

    conn = get_db(write=True)
    try:
        conn.executescript(DDL_BREADTH)
        conn.execute(
            """INSERT INTO market_breadth_snapshot
               (datetime, up_count, down_count, flat_count)
               VALUES (?, ?, ?, ?)""",
            (now_str, breadth["up_count"], breadth["down_count"], breadth["flat_count"]),
        )
        conn.commit()
        print(f"  [minute_collector] market_breadth_snapshot 写入完成")
    except Exception as e:
        print(f"  [minute_collector] 写入 market_breadth_snapshot 失败: {e}", file=sys.stderr)
    finally:
        conn.close()

    return inserted


# ══════════════════════════════════════════════════════
# 交易时段判断
# ══════════════════════════════════════════════════════

def is_trading_time() -> bool:
    """判断当前是否在交易时段 (09:31-11:30 或 13:01-15:00)"""
    now = datetime.now()
    if now.weekday() >= 5:  # 周六日
        return False
    t = now.hour * 100 + now.minute
    return (931 <= t <= 1130) or (1301 <= t <= 1500)


# ══════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════

def main():
    """独立运行入口 — 采集一次立即退出"""
    if not is_trading_time():
        sys.exit(0)
    count = collect_minute_data()
    heartbeat("minute_collector")
    print(f"\n  minute_collector 完成, 写入 {count} 条")


def main_loop():
    """持续运行模式 — 每60s采集一次, 使用 collector_main_loop 骨架"""
    def collect_fn() -> bool:
        if not is_trading_time():
            now = datetime.now()
            print(f"  [minute_collector] {now.strftime('%H:%M:%S')} 非交易时段, 跳过",
                  file=sys.stderr)
            # 非交易时段返回True (不算失败)
            heartbeat("minute_collector")
            return True
        count = collect_minute_data()
        heartbeat("minute_collector")
        return count > 0

    collector_main_loop(
        name="minute_collector",
        collect_fn=collect_fn,
        interval_seconds=60,
        max_consecutive_failures=10,
        gc_interval=100,
    )


if __name__ == "__main__":
    # 支持 --loop 参数进入持续模式
    if "--loop" in sys.argv:
        main_loop()
    else:
        main()
