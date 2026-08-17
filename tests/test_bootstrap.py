from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


def test_bootstrap_seeds_db_and_daily(tmp_path, monkeypatch):
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    from nous.core.config import Config

    Config.reset()
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))

    spot = [
        {
            "f12": "600519",
            "f14": "贵州茅台",
            "f9": 20.0,
            "f23": 8.0,
            "f20": 2e12,
        },
        {
            "f12": "000001",
            "f14": "平安银行",
            "f9": 8.0,
            "f23": 0.8,
            "f20": 3e11,
        },
    ]
    hist = pd.DataFrame(
        {
            "日期": ["2026-01-05", "2026-01-06"],
            "开盘": [10.0, 10.2],
            "最高": [10.5, 10.6],
            "最低": [9.9, 10.0],
            "收盘": [10.2, 10.4],
            "成交量": [1000, 1100],
            "成交额": [1e7, 1.1e7],
        }
    )

    with (
        patch("nous.data.collectors.unified._fetch_a_spot_em", return_value=spot),
        patch("nous.data.bootstrap._backfill_one", return_value=[
            ("600519", "2026-01-06", 10.2, 10.6, 10.0, 10.4, 1100, 1.1e7),
        ]),
        patch("nous.data.collectors.unified.collect_index_daily", return_value={"status": "ok", "count": 1}),
    ):
        from nous.data.bootstrap import run_bootstrap

        result = run_bootstrap(universe=2, lookback_calendar_days=30, workers=1)

    assert result["ok"] is True
    assert result["stock_basic"] == 2
    assert result["stock_daily_rows"] >= 1
    assert (tmp_path / "screener.db").exists()
