#!/usr/bin/env python3
"""
从 screener.db stock_daily 表回填历史情绪数据到 dashboard SQLite。

计算逻辑：
- 涨停家数：pct_chg >= 涨停限制 * 0.99（容忍四舍五入）
- 跌停家数：pct_chg <= -跌停限制 * 0.99
- 炸板数：当日最高触及涨停但收盘未封板
- 上涨/下跌家数
- 涨跌比

涨跌停限制：
- 主板 60xxxx / 00xxxx: 10%
- 科创板 688xxx: 20%
- 创业板 300xxx / 301xxx: 20%
- 北交所 8xxxxx / 4xxxxx / 92xxxx: 30%

输出到 ~/code/dashboard/data/reports.db sentiment 表。
"""

import sqlite3
import sys
import os
from datetime import date, datetime, timedelta
from collections import defaultdict

SCREENER_DB = os.path.expanduser("~/code/stock-screener/data/screener.db")
DASHBOARD_DB = os.path.expanduser("~/code/dashboard/data/reports.db")


def get_limit_pct(code: str) -> float:
    """根据股票代码返回涨跌幅限制百分比"""
    if not code:
        return 10.0
    # 北交所
    if code.startswith("8") or code.startswith("4") or code.startswith("92"):
        return 30.0
    # 科创板
    if len(code) >= 3 and code[:3] == "688":
        return 20.0
    # 创业板
    if len(code) >= 3 and (code[:3] == "300" or code[:3] == "301"):
        return 20.0
    # 主板默认
    return 10.0


def is_limit_up(pct_chg: float, prev_close: float, limit_pct: float) -> bool:
    """判断是否涨停：涨幅 >= 涨停限制 * 0.99"""
    if prev_close <= 0:
        return False
    threshold = limit_pct * 0.99
    return pct_chg >= threshold


def is_limit_down(pct_chg: float, limit_pct: float) -> bool:
    """判断是否跌停：跌幅 <= -跌停限制 * 0.99"""
    threshold = -limit_pct * 0.99
    return pct_chg <= threshold


def compute_sentiment_for_date(
    screener_db: sqlite3.Connection, trade_date: str
) -> dict | None:
    """计算指定日期的情绪指标"""

    # 获取前一交易日
    prev_date_row = screener_db.execute(
        "SELECT MAX(trade_date) FROM stock_daily WHERE trade_date < ? AND (SELECT COUNT(*) FROM stock_daily s2 WHERE s2.trade_date=stock_daily.trade_date) >= 1000",
        (trade_date,),
    ).fetchone()
    prev_date = prev_date_row[0] if prev_date_row else None

    # 获取当日数据
    today_rows = screener_db.execute(
        "SELECT symbol, open, high, close FROM stock_daily WHERE trade_date = ?",
        (trade_date,),
    ).fetchall()

    if not today_rows or len(today_rows) < 100:
        return None

    # 获取前一日收盘价
    prev_close_map = {}
    if prev_date:
        prev_rows = screener_db.execute(
            "SELECT symbol, close FROM stock_daily WHERE trade_date = ?",
            (prev_date,),
        ).fetchall()
        prev_close_map = {r[0]: r[1] for r in prev_rows if r[1] is not None}

    limit_up_count = 0
    limit_down_count = 0
    busted_count = 0
    advancer_count = 0
    decliner_count = 0
    total = len(today_rows)

    for symbol, open_p, high_p, close_p in today_rows:
        if close_p is None:
            continue
        close_val = float(close_p)

        prev_close = prev_close_map.get(symbol)
        if prev_close is None or prev_close <= 0:
            continue
        prev_close_val = float(prev_close)

        pct = (close_val - prev_close_val) / prev_close_val * 100
        limit_pct = get_limit_pct(str(symbol))

        # 涨跌统计
        if pct > 0:
            advancer_count += 1
        elif pct < 0:
            decliner_count += 1

        # 涨停
        if is_limit_up(pct, prev_close_val, limit_pct):
            limit_up_count += 1

        # 跌停
        if is_limit_down(pct, limit_pct):
            limit_down_count += 1

        # 炸板：最高价触及涨停但收盘没涨停
        if high_p is not None and close_p is not None:
            high_pct = (float(high_p) - prev_close_val) / prev_close_val * 100
            close_pct_val = (float(close_p) - prev_close_val) / prev_close_val * 100
            if high_pct >= limit_pct * 0.99 and close_pct_val < limit_pct * 0.99:
                busted_count += 1

    limit_up_rate = round(limit_up_count / total, 4) if total > 0 else 0
    bust_ratio = round(busted_count / (limit_up_count + busted_count), 4) if (limit_up_count + busted_count) > 0 else 0
    adv_decl_ratio = round(advancer_count / decliner_count, 4) if decliner_count > 0 else 99.0

    # 简单情绪评分（基于涨停数和涨跌比）
    limit_score = min(100, limit_up_count / 200 * 100) * 0.3  # 涨停数贡献30%
    ratio_score = min(100, adv_decl_ratio * 50) * 0.25  # 涨跌比贡献25%
    bust_score = (1 - bust_ratio) * 100 * 0.2  # 炸板率贡献20%
    advancer_score = (advancer_count / total * 100) * 0.25  # 上涨占比贡献25%
    score = int(round(limit_score + ratio_score + bust_score + advancer_score))
    score = max(0, min(100, score))

    return {
        "date": trade_date,
        "score": score,
        "limit_up_count": limit_up_count,
        "limit_up_rate": round(limit_up_count / 5514, 4),
        "details": {
            "涨停家数": limit_up_count,
            "跌停家数": limit_down_count,
            "炸板数": busted_count,
            "炸板率": bust_ratio,
            "上涨家数": advancer_count,
            "下跌家数": decliner_count,
            "涨跌比": adv_decl_ratio,
        },
    }


def main():
    start_date = sys.argv[1] if len(sys.argv) > 1 else "2020-01-02"
    end_date = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()

    screener = sqlite3.connect(SCREENER_DB)
    screener.row_factory = sqlite3.Row

    # 写入 screener.db.sentiment_cache（dashboard 读的就是这个）
    screener.execute("PRAGMA journal_mode=WAL")
    screener.executescript("""
    CREATE TABLE IF NOT EXISTS sentiment_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        score INTEGER,
        limit_up_count INTEGER,
        limit_up_rate REAL,
        details TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sentiment_date ON sentiment_cache(date);
    """)

    # 获取所有交易日
    dates = screener.execute(
        "SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (start_date, end_date),
    ).fetchall()

    total = len(dates)
    inserted = 0
    skipped = 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for i, (trade_date,) in enumerate(dates):
        # 检查是否已存在
        existing = screener.execute(
            "SELECT 1 FROM sentiment_cache WHERE date = ?", (trade_date,)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        result = compute_sentiment_for_date(screener, trade_date)
        if result is None:
            continue

        import json

        screener.execute(
            "INSERT OR REPLACE INTO sentiment_cache (date, score, limit_up_count, limit_up_rate, details, created_at) VALUES (?,?,?,?,?,?)",
            (
                result["date"],
                result["score"],
                result["limit_up_count"],
                result["limit_up_rate"],
                json.dumps(result["details"], ensure_ascii=False),
                now,
            ),
        )

        inserted += 1
        if (i + 1) % 200 == 0:
            screener.commit()
            print(f"  {i+1}/{total} 已插入 {inserted}, 跳过 {skipped}")

    screener.commit()
    screener.close()

    print(f"\n✅ 完成: 总计 {total} 个交易日, 新增 {inserted}, 跳过 {skipped}")
    print(f"   日期范围: {start_date} → {end_date}")


if __name__ == "__main__":
    main()
