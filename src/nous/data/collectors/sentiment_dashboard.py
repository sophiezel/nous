#!/usr/bin/env python3
"""
sentiment_dashboard — 涨停板情绪仪表盘

采集当日涨停板池/强势池/跌停板池数据，计算涨停情绪指标：
  - 涨停家数 / 跌停家数 / 炸板率 / 最高连板数 / 首板数 / 二板数 / 高位板数
写入 limit_up_sentiment 表（日级）和 market_breadth_snapshot 表（分钟级快照）。

数据源: akshare（东方财富接口）
自愈: resilient_fetch + CircuitBreaker + 指数退避重试 + 优雅降级
看门狗: heartbeat('sentiment_dashboard')

单独运行:
    python -m src.collectors.sentiment_dashboard
"""

import sys
import os
import time
import sqlite3
from datetime import date, datetime

# ── 确保可以从 src 导入 ─────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd

from nous.data.collectors import resilient_fetch, heartbeat, CircuitBreaker
from nous.data.storage import get_db

# ── 表 DDL ──────────────────────────────────────────

DDL_LIMIT_UP_SENTIMENT = """
CREATE TABLE IF NOT EXISTS limit_up_sentiment (
    trade_date        TEXT PRIMARY KEY,
    limit_up_count    INTEGER DEFAULT 0,
    limit_down_count  INTEGER DEFAULT 0,
    board_break_count  INTEGER DEFAULT 0,
    board_break_rate  REAL DEFAULT 0.0,
    max_board_height  INTEGER DEFAULT 0,
    first_board_count INTEGER DEFAULT 0,
    second_board_count INTEGER DEFAULT 0,
    high_board_count  INTEGER DEFAULT 0,
    strong_pool_count INTEGER DEFAULT 0,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_MARKET_BREADTH = """
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

# ── 指标计算 ────────────────────────────────────────

def _parse_board_height(board_height_str) -> int:
    """从 '连板数' 字段解析连板高度（数字）。
    
    可能的值:
      - "2连板" → 2
      - "3连板" → 3
      - "首板"  → 1
      - NaN/空 → 0
    """
    if pd.isna(board_height_str):
        return 0
    s = str(board_height_str).strip()
    if not s:
        return 0
    if "首板" in s or s == "1":
        return 1
    try:
        # 尝试提取数字
        import re
        nums = re.findall(r'\d+', s)
        if nums:
            return int(nums[0])
    except Exception:
        pass
    return 0


def compute_sentiment(limit_up_df: pd.DataFrame,
                      limit_down_df: pd.DataFrame,
                      strong_df: pd.DataFrame) -> dict:
    """从三个池子计算情绪指标。
    
    Returns:
        dict with keys:
          trade_date, limit_up_count, limit_down_count, board_break_count,
          board_break_rate, max_board_height, first_board_count,
          second_board_count, high_board_count, strong_pool_count
    """
    result = {}

    # 涨停家数
    limit_up_count = len(limit_up_df) if limit_up_df is not None else 0
    result['limit_up_count'] = limit_up_count

    # 跌停家数
    limit_down_count = len(limit_down_df) if limit_down_df is not None else 0
    result['limit_down_count'] = limit_down_count

    # 强势池数量
    strong_pool_count = len(strong_df) if strong_df is not None else 0
    result['strong_pool_count'] = strong_pool_count

    # 炸板统计: 从涨停池中统计炸板次数 > 0 的股票
    board_break_count = 0
    if limit_up_df is not None and '炸板次数' in limit_up_df.columns:
        board_break_count = int(limit_up_df['炸板次数'].fillna(0).astype(int).gt(0).sum())
    # 炸板率 = 炸板数 / (涨停数 + 炸板数)
    divisor = limit_up_count + board_break_count
    board_break_rate = round(board_break_count / divisor, 4) if divisor > 0 else 0.0
    result['board_break_count'] = board_break_count
    result['board_break_rate'] = board_break_rate

    # 连板高度统计
    max_board_height = 0
    first_board_count = 0
    second_board_count = 0
    high_board_count = 0  # 3连板及以上

    if limit_up_df is not None and '连板数' in limit_up_df.columns:
        heights = limit_up_df['连板数'].apply(_parse_board_height)
        if len(heights) > 0:
            max_board_height = int(heights.max())
            first_board_count = int((heights == 1).sum())
            second_board_count = int((heights == 2).sum())
            high_board_count = int((heights >= 3).sum())

    result['max_board_height'] = max_board_height
    result['first_board_count'] = first_board_count
    result['second_board_count'] = second_board_count
    result['high_board_count'] = high_board_count

    return result


def get_trade_date() -> str:
    """获取交易日期字符串 YYYYMMDD。"""
    return date.today().strftime('%Y%m%d')


# ── 数据采集 ────────────────────────────────────────

def fetch_limit_up_pool(trade_date: str) -> pd.DataFrame:
    """获取涨停板池"""
    def _fetch():
        import akshare
        return akshare.stock_zt_pool_em(date=trade_date)

    result, status = resilient_fetch(
        'akshare', _fetch,
        fallback_fn=lambda: pd.DataFrame(),
        max_retries=3, base_delay=1.0,
    )
    if not status.get('success') or result is None:
        print(f"  [sentiment] 涨停池获取失败: {status.get('error', 'unknown')}", file=sys.stderr)
        return pd.DataFrame()
    if status.get('fallback_used'):
        print(f"  [sentiment] 涨停池使用降级数据", file=sys.stderr)
    return result


def fetch_limit_down_pool(trade_date: str) -> pd.DataFrame:
    """获取跌停板池"""
    def _fetch():
        import akshare
        return akshare.stock_zt_pool_dtgc_em(date=trade_date)

    result, status = resilient_fetch(
        'akshare', _fetch,
        fallback_fn=lambda: pd.DataFrame(),
        max_retries=3, base_delay=1.0,
    )
    if not status.get('success') or result is None:
        print(f"  [sentiment] 跌停池获取失败: {status.get('error', 'unknown')}", file=sys.stderr)
        return pd.DataFrame()
    if status.get('fallback_used'):
        print(f"  [sentiment] 跌停池使用降级数据", file=sys.stderr)
    return result


def fetch_strong_pool(trade_date: str) -> pd.DataFrame:
    """获取强势股池"""
    def _fetch():
        import akshare
        return akshare.stock_zt_pool_strong_em(date=trade_date)

    result, status = resilient_fetch(
        'akshare', _fetch,
        fallback_fn=lambda: pd.DataFrame(),
        max_retries=3, base_delay=1.0,
    )
    if not status.get('success') or result is None:
        print(f"  [sentiment] 强势池获取失败: {status.get('error', 'unknown')}", file=sys.stderr)
        return pd.DataFrame()
    if status.get('fallback_used'):
        print(f"  [sentiment] 强势池使用降级数据", file=sys.stderr)
    return result


# ── 写入 DB ─────────────────────────────────────────

def write_limit_up_sentiment(conn: sqlite3.Connection, sentiment: dict):
    """写入 limit_up_sentiment 表（日级）。"""
    conn.execute("""
        INSERT OR REPLACE INTO limit_up_sentiment
        (trade_date, limit_up_count, limit_down_count, board_break_count,
         board_break_rate, max_board_height, first_board_count,
         second_board_count, high_board_count, strong_pool_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        sentiment.get('trade_date', get_trade_date()),
        sentiment.get('limit_up_count', 0),
        sentiment.get('limit_down_count', 0),
        sentiment.get('board_break_count', 0),
        sentiment.get('board_break_rate', 0.0),
        sentiment.get('max_board_height', 0),
        sentiment.get('first_board_count', 0),
        sentiment.get('second_board_count', 0),
        sentiment.get('high_board_count', 0),
        sentiment.get('strong_pool_count', 0),
    ))
    conn.commit()
    print(f"  [sentiment] 写入 limit_up_sentiment: {sentiment['limit_up_count']}涨/{sentiment['limit_down_count']}跌 "
          f"炸板率{sentiment['board_break_rate']:.1%} 最高{sentiment['max_board_height']}连板", file=sys.stderr)


def write_market_breadth_snapshot(conn: sqlite3.Connection, sentiment: dict):
    """写入 market_breadth_snapshot 表（分钟级快照）。
    
    如果表不存在则自动跳过（DDL 中已创建）。
    """
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn.execute("""
            INSERT INTO market_breadth_snapshot
            (datetime, up_count, down_count, flat_count, limit_up_count,
             limit_down_count, board_break_rate, max_board_height, turnover_top50_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_str,
            0, 0, 0,  # up/down/flat counts — 需要在全市场统计中获取，此处留空
            sentiment.get('limit_up_count', 0),
            sentiment.get('limit_down_count', 0),
            sentiment.get('board_break_rate', 0.0),
            sentiment.get('max_board_height', 0),
            0.0,  # turnover_top50_pct — 需要额外数据源，此处留空
        ))
        conn.commit()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            print(f"  [sentiment] market_breadth_snapshot 表不存在，跳过快照", file=sys.stderr)
            return
        raise


# ── 采集主函数 ──────────────────────────────────────

def collect() -> bool:
    """执行一次完整采集。返回 True 表示成功。"""
    trade_date = get_trade_date()
    print(f"  [sentiment] ===== {datetime.now().strftime('%H:%M:%S')} 涨停板情绪采集 =====",
          file=sys.stderr)

    # 1. 获取三个池子
    limit_up_df = fetch_limit_up_pool(trade_date)
    limit_down_df = fetch_limit_down_pool(trade_date)
    strong_df = fetch_strong_pool(trade_date)

    # 所有数据源均失败 → 标记降级但不崩溃
    all_empty = all(df.empty for df in [limit_up_df, limit_down_df, strong_df])
    if all_empty:
        print(f"  [sentiment] DEGRADED: 所有数据源均失败，写入空数据", file=sys.stderr)

    # 2. 计算情绪指标
    sentiment = compute_sentiment(limit_up_df, limit_down_df, strong_df)
    sentiment['trade_date'] = trade_date

    print(f"  [sentiment] 涨停{limit_up_df.shape[0]} 跌停{limit_down_df.shape[0]} "
          f"强势{strong_df.shape[0]} 炸板{sentiment['board_break_count']} "
          f"最高连板{sentiment['max_board_height']}", file=sys.stderr)

    # 3. 写入数据库
    try:
        conn = get_db(write=True)
        try:
            # 建表
            conn.executescript(DDL_LIMIT_UP_SENTIMENT)
            conn.executescript(DDL_MARKET_BREADTH)

            write_limit_up_sentiment(conn, sentiment)
            write_market_breadth_snapshot(conn, sentiment)

            heartbeat('sentiment_dashboard')
            return True
        finally:
            conn.close()
    except Exception as e:
        print(f"  [sentiment] DB写入失败: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ── 主入口 ──────────────────────────────────────────

def main():
    """单次采集入口（独立运行时调用）。"""
    print(f"[sentiment_dashboard] 涨停板情绪仪表盘采集开始", file=sys.stderr)
    print(f"[sentiment_dashboard] 交易日期: {get_trade_date()}", file=sys.stderr)

    success = collect()

    if success:
        print(f"[sentiment_dashboard] 采集完成 ✓", file=sys.stderr)
        sys.exit(0)
    else:
        print(f"[sentiment_dashboard] 采集失败 ✗", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
