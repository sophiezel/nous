"""中国A股交易日历

策略: DB优先(index_daily有实际交易日期) → 回退硬编码假日表+跳周末
"""

from datetime import date, timedelta
from pathlib import Path
import sqlite3

from nous.core.db import _resolve_path
DB_PATH = Path(_resolve_path("screener.db"))

# 2025-2026 中国法定假日(非周末), 来源: 国务院办公厅
# 每年需更新一次
_CN_HOLIDAYS_2025 = {
    date(2025, 1, 1),           # 元旦
    date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30),  # 春节
    date(2025, 1, 31), date(2025, 2, 1), date(2025, 2, 2), date(2025, 2, 3),
    date(2025, 4, 4),           # 清明
    date(2025, 5, 1), date(2025, 5, 2), date(2025, 5, 5),  # 劳动节
    date(2025, 5, 31),          # 端午
    date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3),  # 国庆
    date(2025, 10, 6), date(2025, 10, 7),
}

_CN_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 2),           # 元旦
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),  # 春节
    date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 23),
    date(2026, 4, 5), date(2026, 4, 6),           # 清明
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),  # 劳动节
    date(2026, 6, 19),                              # 端午
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),  # 国庆
    date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8),
}

# 调休上班日(周末但开市)
_CN_WORKDAYS_2025 = {
    date(2025, 1, 26),  # 春节调休
    date(2025, 2, 8),   # 春节调休
    date(2025, 4, 27),  # 劳动节调休
    date(2025, 9, 28),  # 国庆调休
    date(2025, 10, 11), # 国庆调休
}

_CN_WORKDAYS_2026 = {
    date(2026, 2, 14),  # 春节调休
    date(2026, 2, 28),  # 春节调休
    date(2026, 4, 26),  # 劳动节调休
    date(2026, 10, 10), # 国庆调休
}

ALL_HOLIDAYS = _CN_HOLIDAYS_2025 | _CN_HOLIDAYS_2026
ALL_WORKDAYS = _CN_WORKDAYS_2025 | _CN_WORKDAYS_2026


def is_trading_day(d: date) -> bool:
    """判断某天是否为A股交易日"""
    # 调休上班日 → 交易日
    if d in ALL_WORKDAYS:
        return True
    # 周末 → 非交易日
    if d.weekday() >= 5:
        return False
    # 法定假日 → 非交易日
    if d in ALL_HOLIDAYS:
        return False
    return True


def next_trading_day(d: date) -> date:
    """返回d之后的下一个交易日(不含d本身)

    优先查DB: index_daily有数据 → 确认是交易日
    回退: 硬编码日历
    """
    # DB优先: 查index_daily最近的交易日
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        conn.execute("PRAGMA busy_timeout=3000")
        row = conn.execute(
            "SELECT MIN(trade_date) FROM index_daily WHERE trade_date > ?",
            (d.isoformat(),)
        ).fetchone()
        conn.close()
        if row and row[0]:
            return date.fromisoformat(row[0])
    except Exception:
        pass

    # 回退: 日历推算
    candidate = d + timedelta(days=1)
    for _ in range(10):  # 最多找10天(覆盖国庆长假)
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)

    # 极端情况: 直接+1
    return d + timedelta(days=1)


def prev_trading_day(d: date) -> date:
    """返回d之前的最近一个交易日(不含d本身)"""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        conn.execute("PRAGMA busy_timeout=3000")
        row = conn.execute(
            "SELECT MAX(trade_date) FROM index_daily WHERE trade_date < ?",
            (d.isoformat(),)
        ).fetchone()
        conn.close()
        if row and row[0]:
            return date.fromisoformat(row[0])
    except Exception:
        pass

    candidate = d - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return d - timedelta(days=1)


if __name__ == "__main__":
    # 测试
    test_dates = [
        date(2026, 5, 29),  # 周五
        date(2026, 5, 30),  # 周六
        date(2026, 5, 31),  # 周日
        date(2026, 6, 1),   # 周一
        date(2026, 6, 19),  # 端午
    ]
    for d in test_dates:
        ntd = next_trading_day(d)
        print(f"{d} ({d.strftime('%A')}) → next_trading_day = {ntd} ({ntd.strftime('%A')})")
