"""新鲜度门禁 — 确保数据在有效期内且来源正确

规则:
- T0 指标: 超过60秒拒绝
- T1 指标: A股15:00前/HK 16:10前的收盘数据拒绝
- T2 指标: 财报超过5天未更新则警告
- T3 指标: 不过期

市场感知: 先检查是否交易日，非交易日用最近交易日数据
"""

from datetime import date, datetime, time, timedelta
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Freshness(Enum):
    FRESH = "fresh"       # 数据新鲜，可用
    STALE = "stale"       # 数据过期但可用（标记）
    REJECTED = "rejected" # 数据过期，不可用
    NON_TRADING = "non_trading"  # 非交易日，使用最近数据


@dataclass
class GateResult:
    freshness: Freshness
    reason: str = ""
    actual_date: Optional[date] = None  # 如果使用了非当日数据，标记实际日期


# 市场收盘时间
MARKET_CLOSE = {
    "a": time(15, 0),
    "hk": time(16, 10),  # 收市竞价结束
}

T0_MAX_AGE = 60  # 秒
T2_MAX_AGE_DAYS = 5


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _get_trading_days() -> set:
    """获取交易日集合 — 委托统一 trading_calendar."""
    try:
        from nous.data.quality.trading_calendar import get_trading_days
        from datetime import date, timedelta
        today = date.today()
        days = get_trading_days(
            (today - timedelta(days=60)).isoformat(),
            (today + timedelta(days=14)).isoformat(),
        )
        return set(days)
    except Exception:
        pass
    import json
    from pathlib import Path
    cache = Path.home() / ".cache" / "trading_calendar.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            return set(data.get("days", []))
        except Exception:
            pass
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return {((monday + timedelta(days=i)).isoformat()) for i in range(5)}


def check(
    tier: str,
    market: str = "a",
    data_timestamp: Optional[datetime] = None,
    data_date: Optional[date] = None,
) -> GateResult:
    """
    检查数据新鲜度。

    Args:
        tier: T0/T1/T2/T3
        market: 'a' 或 'hk'
        data_timestamp: 数据时间戳（T0/T1使用）
        data_date: 数据日期（T2使用）

    Returns:
        GateResult 包含 fresh/stale/rejected 判定
    """
    today = date.today()

    # 1. 交易日历检查（T0/T1需要）
    if tier in ("T0", "T1"):
        trading_days = _get_trading_days()
        is_trading = today.isoformat() in trading_days and not _is_weekend(today)

        if not is_trading:
            # 非交易日: 使用最近交易日数据，不拒绝
            d = today - timedelta(days=1)
            while d.isoformat() not in trading_days and not _is_weekend(d):
                d -= timedelta(days=1)
            # 如果整个星期都没有，找上周五
            while _is_weekend(d) or d.isoformat() not in trading_days:
                d -= timedelta(days=1)
            return GateResult(
                freshness=Freshness.NON_TRADING,
                reason=f"非交易日，使用 {d} 数据",
                actual_date=d,
            )

    # 2. T0 实时指标: 时间戳不能超过60秒
    if tier == "T0":
        if data_timestamp:
            age = (datetime.now() - data_timestamp).total_seconds()
            if age > T0_MAX_AGE:
                return GateResult(
                    freshness=Freshness.REJECTED,
                    reason=f"T0数据过期({age:.0f}s > {T0_MAX_AGE}s)",
                )
        return GateResult(freshness=Freshness.FRESH, reason="T0实时")

    # 3. T1 日级指标: 市场收盘时间门禁
    if tier == "T1":
        close_time = MARKET_CLOSE.get(market, time(15, 0))

        if data_timestamp:
            ts_date = data_timestamp.date()
            ts_time = data_timestamp.time()

            # 如果数据时间戳在收盘前 → 这是盘中数据，拒绝
            if ts_date == today and ts_time < close_time:
                return GateResult(
                    freshness=Freshness.REJECTED,
                    reason=f"T1数据时间戳{ts_time}在收盘前(<{close_time})——这是盘中数据",
                )
            # 数据是历史的 → 检查是否过期
            if ts_date < today:
                gap = (today - ts_date).days
                if gap > 2 and not _is_weekend(today):
                    return GateResult(
                        freshness=Freshness.STALE,
                        reason=f"T1数据来自{ts_date}，已过期{gap}天",
                    )

        return GateResult(freshness=Freshness.FRESH, reason="T1收盘数据")

    # 4. T2 周期级指标: 检查是否超过最大天数
    if tier == "T2":
        if data_date:
            gap = (today - data_date).days
            if gap > T2_MAX_AGE_DAYS:
                return GateResult(
                    freshness=Freshness.STALE,
                    reason=f"T2数据来自{data_date}，已超过{T2_MAX_AGE_DAYS}天",
                )
        return GateResult(freshness=Freshness.FRESH, reason="T2周期数据")

    # 5. T3 静态指标: 永不过期
    if tier == "T3":
        return GateResult(freshness=Freshness.FRESH, reason="T3静态数据")

    return GateResult(freshness=Freshness.FRESH, reason="未分类")


def get_market_ready_time(market: str) -> time:
    """获取某市场T1数据就绪时间"""
    return MARKET_CLOSE.get(market, time(15, 0))


if __name__ == "__main__":
    # 自测
    print("T0 test:", check("T0", data_timestamp=datetime.now()))
    print("T1 A-share before close:", check("T1", "a",
          data_timestamp=datetime.combine(date.today(), time(14, 0))))
    print("T1 HK before close:", check("T1", "hk",
          data_timestamp=datetime.combine(date.today(), time(16, 5))))
    print("T1 A-share after close:", check("T1", "a",
          data_timestamp=datetime.combine(date.today(), time(15, 30))))
    print("T2:", check("T2", data_date=date.today()))
    print("T3:", check("T3"))
