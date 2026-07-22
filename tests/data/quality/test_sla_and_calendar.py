"""Unit tests for trading calendar + SLA registry selection."""
from datetime import date, timedelta

from nous.data.quality.trading_calendar import (
    trading_day_lag,
    previous_trading_day,
    get_trading_days,
)
from nous.data.quality.sla_registry import CONSUMERS, DOMAIN_KEYS, asset_by_key
from nous.data.quality.data_assert import select_assets


def test_weekday_lag_same_day():
    # Without DB, fallback weekdays — Friday vs Friday
    friday = date(2026, 7, 17)  # Friday
    assert friday.weekday() == 4
    lag = trading_day_lag(friday.isoformat(), friday.isoformat())
    assert lag == 0


def test_previous_trading_day_skips_weekend():
    # Sunday → should land on Friday in weekday fallback
    sunday = date(2026, 7, 19)
    prev = previous_trading_day(sunday.isoformat(), n=1)
    assert date.fromisoformat(prev).weekday() < 5


def test_get_trading_days_nonempty():
    days = get_trading_days("2026-07-01", "2026-07-17")
    assert len(days) >= 10
    assert all(isinstance(d, str) and len(d) == 10 for d in days)


def test_consumer_contracts_exist():
    for name in ("recommend", "trl", "review", "backtest", "all"):
        assert name in CONSUMERS
        assert len(CONSUMERS[name].required) >= 1


def test_select_assets_recommend_subset():
    assets = select_assets(consumer="recommend")
    keys = {a.key for a in assets}
    assert "stock_daily_a" in keys
    assert "factors_latest" in keys
    assert "lhb_daily" not in keys  # not in recommend required/optional


def test_select_assets_domain_capital():
    assets = select_assets(domain="capital")
    keys = {a.key for a in assets}
    assert keys == set(DOMAIN_KEYS["capital"])


def test_asset_by_key():
    a = asset_by_key("index_daily")
    assert a is not None
    assert a.max_lag_trading_days == 1
