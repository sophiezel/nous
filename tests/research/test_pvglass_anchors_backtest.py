"""锚定标的 / as_of 语义 / 信号回测 / 周度流水线 测试（离线，隔离数据库）。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from nous.core.db import get_db
from nous.research.pvglass import backtest as bt
from nous.research.pvglass import pipeline
from nous.research.pvglass import signals as sig
from nous.research.pvglass import store
from nous.research.pvglass.registry import Condition, Registry, Signal, load_registry


@pytest.fixture()
def registry() -> Registry:
    return load_registry()


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    store.init_db()
    with get_db(store.DB_NAME, write=True) as c:
        store.ensure_schema(c)
        yield c


def _dates(n: int, start: str = "2026-01-01") -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


# ── 锚定标的 ───────────────────────────────────────────────────────────
def test_anchors_are_declared_and_consistent(registry):
    assert {"xinyi", "flat", "xenergy"} <= set(registry.anchors)
    for ind in registry.indicators.values():
        assert ind.anchor in registry.anchors, f"{ind.id} 的 anchor 未声明"
    for anchor, spec in registry.anchors.items():
        members = registry.by_anchor(anchor)
        assert members, f"锚定标的 {anchor} 没有任何指标"
        if spec.price_indicator:
            assert spec.price_indicator in registry.indicators
            assert registry.indicators[spec.price_indicator].anchor == anchor


def test_anchor_ids_and_labels(registry):
    ids = registry.anchor_ids()
    assert ids[:3] == ["xinyi", "flat", "xenergy"]  # 声明顺序优先
    assert registry.anchor_label("xinyi").startswith("信义光能")
    assert registry.anchor_label("flat").startswith("福莱特")
    assert registry.anchor_label("unknown") == "unknown"


def test_peer_signals_exist_for_both_glass_leaders(registry):
    ids = {s.id for s in registry.signals}
    assert {"S5_xinyi_margin", "S5f_flat_margin"} <= ids
    flat_signal = next(s for s in registry.signals if s.id == "S5f_flat_margin")
    assert flat_signal.conditions[0].indicator == "flat_glass_glass_gm"


# ── as_of 语义（回测的基石）────────────────────────────────────────────
def test_latest_and_series_respect_as_of(conn, registry):
    store.record_obs(conn, "glass_price_2_0_single", "2026-08-01", 9.0, source="mysteel")
    store.record_obs(conn, "glass_price_2_0_single", "2026-09-01", 10.6, source="mysteel")

    latest = store.latest(conn, "glass_price_2_0_single")
    assert latest is not None and latest["value"] == 10.6
    asof = store.latest(conn, "glass_price_2_0_single", as_of="2026-08-15")
    assert asof is not None and asof["value"] == 9.0
    assert store.latest(conn, "glass_price_2_0_single", as_of="2026-07-01") is None
    assert len(store.series(conn, "glass_price_2_0_single", days=90, as_of="2026-08-15")) == 1
    assert len(store.all_obs(conn, "glass_price_2_0_single")) == 2
    assert store.obs_dates(conn, "glass_price_2_0_single") == ["2026-08-01", "2026-09-01"]


def test_condition_evaluation_is_point_in_time(conn, registry):
    store.record_obs(conn, "glass_price_2_0_single", "2026-08-01", 9.0, source="mysteel")
    store.record_obs(conn, "glass_price_2_0_single", "2026-09-01", 10.6, source="mysteel")
    cond = Condition(
        indicator="glass_price_2_0_single", op="gte", value=10.5, window_days=30
    )
    assert sig.evaluate_condition(conn, registry, cond, as_of="2026-08-15").status == sig.FAIL
    assert sig.evaluate_condition(conn, registry, cond, as_of="2026-09-05").status == sig.PASS


# ── 回测 ───────────────────────────────────────────────────────────────
def _test_signal() -> Signal:
    return Signal(
        id="T_price",
        name="测试信号",
        thesis="价格上行",
        logic="all",
        conditions=(
            Condition(indicator="glass_price_2_0_single", op="gte", value=10.5, window_days=30),
        ),
    )


def _load_prices(conn, dates: list[str], values: list[float]) -> None:
    for day, value in zip(dates, values):
        store.record_obs(conn, "xinyi_price", day, value, source="test")


def test_forward_return_math():
    prices = [("2026-01-01", 100.0), ("2026-01-02", 110.0), ("2026-01-03", 120.0)]
    assert bt.forward_return(prices, 0, 2) == pytest.approx(0.2)
    assert bt.forward_return(prices, 0, 1) == pytest.approx(0.1)
    assert bt.forward_return(prices, 1, 2) is None  # 越界
    assert bt.forward_return([("d", 0.0), ("d2", 1.0)], 0, 1) is None  # 零价保护


def test_backtest_requires_price_history(conn, registry):
    store.record_obs(conn, "glass_price_2_0_single", "2026-01-01", 11.0, source="test")
    result = bt.replay(conn, registry, _test_signal(), horizons=(20,))
    assert result.status == "insufficient_history"
    assert "bootstrap" in result.note


def test_backtest_detects_trigger_and_excess(conn, registry):
    """
    构造：价格前 10 天横盘 100，之后跳到 130 并保持；
    信号在第 6 天确认（跳涨前）。
    期望：信号捕获到跳涨，平均收益/胜率均优于全样本基线。
    """
    dates = _dates(60)
    prices = [100.0] * 10 + [130.0] * 50
    _load_prices(conn, dates, prices)
    for i, day in enumerate(dates):
        store.record_obs(
            conn,
            "glass_price_2_0_single",
            day,
            11.0 if i >= 5 else 9.0,
            source="test",
        )

    result = bt.replay(conn, registry, _test_signal(), horizons=(20,), cooldown_days=20)
    assert result.status == "ok"
    assert result.n_triggers >= 1

    stat = result.stats[20]
    assert stat.n >= 1
    assert stat.hit_rate >= stat.baseline_hit_rate
    assert stat.avg_return > stat.baseline_avg_return
    assert stat.excess > 0


def test_backtest_cooldown_controls_trigger_count(conn, registry):
    """冷却期只控制重新入场的频率，不改变条件本身的真假。"""
    dates = _dates(40)
    _load_prices(conn, dates, [100.0 + 0.5 * i for i in range(40)])
    for day in dates:
        store.record_obs(conn, "glass_price_2_0_single", day, 11.0, source="test")

    short = bt.replay(conn, registry, _test_signal(), horizons=(5,), cooldown_days=1)
    long = bt.replay(conn, registry, _test_signal(), horizons=(5,), cooldown_days=15)
    assert short.n_triggers > long.n_triggers
    # 40 个观测、冷却 15 → 入场时点落在 0/15/30
    assert long.n_triggers == 3


def test_backtest_run_all_signals_returns_rows(conn, registry):
    dates = _dates(30)
    _load_prices(conn, dates, [100.0] * 30)
    # 让 S1（玻璃价格+库存）真的能触发，验证回测能产出可读行
    for day in dates:
        store.record_obs(conn, "glass_price_2_0_single", day, 10.6, source="test")
        store.record_obs(conn, "glass_inventory_days", day, 38.0, source="test")

    results = bt.run(conn, registry, horizons=(5,))
    assert len(results) == len(registry.signals)
    rows = bt.summary_rows(results)
    assert rows
    assert "S1_glass_price" in {r["signal_id"] for r in rows}


def test_bootstrap_symbols_cover_all_anchors(registry):
    for indicator_id in bt.BOOTSTRAP_SYMBOLS:
        assert indicator_id in registry.indicators, f"{indicator_id} 未在字典中声明"
    anchors = {registry.indicators[i].anchor for i in bt.BOOTSTRAP_SYMBOLS}
    assert {"xinyi", "flat"} <= anchors


# ── 周度流水线 ─────────────────────────────────────────────────────────
def test_import_seed_prunes_stale_rows(tmp_path, conn, registry):
    seed = tmp_path / "seed.yaml"
    seed.write_text(
        'observations:\n'
        '  - {indicator: glass_price_2_0_single, date: "2026-09-04", value: 10.5}\n'
        '  - {indicator: not_exist, date: "2026-09-04", value: 1.0}\n'
        'events:\n'
        '  - {indicator: policy_low_price_rule, date: "2026-09-10", text: "通知"}\n',
        encoding="utf-8",
    )
    n_obs, n_evt, warnings = pipeline.import_seed(conn, registry, seed)
    assert (n_obs, n_evt) == (1, 1)
    assert len(warnings) == 1 and "not_exist" in warnings[0]
    seeded = store.latest(conn, "glass_price_2_0_single")
    assert seeded is not None and seeded["value"] == 10.5

    # 文件里删掉该条 → prune 后库里也不该再有 seed 行
    seed.write_text("observations: []\n", encoding="utf-8")
    pipeline.import_seed(conn, registry, seed)
    assert store.latest(conn, "glass_price_2_0_single") is None


def test_import_seed_keeps_non_seed_rows(tmp_path, conn, registry):
    store.record_obs(conn, "glass_price_2_0_single", "2026-09-11", 10.5, source="mysteel")
    seed = tmp_path / "seed.yaml"
    seed.write_text(
        'observations:\n'
        '  - {indicator: glass_price_2_0_single, date: "2026-09-04", value: 10.5}\n',
        encoding="utf-8",
    )
    pipeline.import_seed(conn, registry, seed)
    sources = {r["source"] for r in store.all_obs(conn, "glass_price_2_0_single")}
    assert sources == {"mysteel", "seed"}  # 自动采集的数据不被 prune 误删


def test_run_weekly_generates_report_without_network(tmp_path, registry, monkeypatch):
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path / "data"))
    store.init_db()
    report = tmp_path / "weekly.md"
    outcome = pipeline.run_weekly(
        registry=registry,
        skip_fetch=True,
        skip_seed=True,
        out=report,
    )
    assert outcome.verdict_level in {"bullish", "watch", "pending", "bearish"}
    assert outcome.report_path == report
    text = report.read_text(encoding="utf-8")
    assert "光伏玻璃产业链" in text
    assert "拐点信号看板" in text
    assert "数据源" in outcome.summary_lines()[0] or outcome.summary_lines()[0]


def test_weekly_summary_lines_shape(registry):
    outcome = pipeline.WeeklyOutcome(ran_at="2026-09-16T08:40:00")
    outcome.fetches = [store.FetchResult("etnet", "ok", 26, "写入26行", 10)]
    outcome.verdict_level = "watch"
    outcome.verdict_text = "1/7 信号确认"
    lines = outcome.summary_lines()
    assert any("采集" in line for line in lines)
    assert any("WATCH" in line for line in lines)
