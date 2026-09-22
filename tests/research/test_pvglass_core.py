"""指标字典 / 时序库 / 信号引擎 / 盈利模型 的核心测试（离线、隔离数据库）。"""

from __future__ import annotations

import pytest

from nous.core.db import get_db
from nous.research.pvglass import signals as sig
from nous.research.pvglass import store, xinyi
from nous.research.pvglass.registry import RegistryError, load_registry


@pytest.fixture()
def registry():
    return load_registry()


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    store.init_db()
    with get_db(store.DB_NAME, write=True) as c:
        store.ensure_schema(c)
        yield c


# ── registry ──────────────────────────────────────────────────────────
def test_registry_loads_and_validates(registry):
    assert len(registry.indicators) >= 40
    assert str(registry.meta["anchor"]).startswith("00968.HK")
    for ind in registry.indicators.values():
        assert ind.source in registry.sources, f"{ind.id} 引用了未声明数据源"
        assert ind.group in registry.groups, f"{ind.id} 的分组未在 groups 中声明"
    stats = registry.stats()
    assert stats["total"] == len(registry.indicators)
    assert stats["auto"] + stats["seed"] + stats["manual"] == stats["total"]


def test_registry_signal_conditions_reference_real_indicators(registry):
    assert registry.signals
    for signal in registry.signals:
        for cond in signal.conditions:
            assert cond.indicator in registry.indicators
            assert cond.op in (
                "gte",
                "lte",
                "gt",
                "lt",
                "pct_chg_gte",
                "pct_chg_lte",
                "mom_streak_gte",
                "mom_streak_lte",
            )


def test_registry_unknown_indicator_message(registry):
    with pytest.raises(KeyError) as exc:
        registry.get("not_exist")
    assert "可用" in str(exc.value)


def test_registry_rejects_unknown_source(tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(
        """
sources:
  manual: {name: 人工, kind: manual}
indicators:
  - {id: x, name: X, group: price, source: nope}
""",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="未声明的 source"):
        load_registry(cfg)


def test_registry_rejects_unknown_operator(tmp_path):
    cfg = tmp_path / "bad2.yaml"
    cfg.write_text(
        """
sources:
  manual: {name: 人工, kind: manual}
indicators:
  - {id: x, name: X, group: price, source: manual}
signals:
  - id: S
    name: s
    logic: all
    conditions: [{indicator: x, op: whatever, value: 1}]
""",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="不支持的算子"):
        load_registry(cfg)


def test_registry_rejects_signal_with_undefined_indicator(tmp_path):
    cfg = tmp_path / "bad3.yaml"
    cfg.write_text(
        """
sources:
  manual: {name: 人工, kind: manual}
indicators:
  - {id: x, name: X, group: price, source: manual}
signals:
  - id: S
    name: s
    logic: all
    conditions: [{indicator: y, op: gte, value: 1}]
""",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="未定义指标"):
        load_registry(cfg)


# ── store ─────────────────────────────────────────────────────────────
def test_record_obs_is_idempotent_per_source(conn):
    store.record_obs(conn, "glass_price_2_0_single", "2026-09-11", 10.5, source="mysteel", unit="元/㎡")
    store.record_obs(conn, "glass_price_2_0_single", "2026-09-11", 10.6, source="mysteel", unit="元/㎡")
    rows = store.series(conn, "glass_price_2_0_single", days=3650)
    assert len(rows) == 1
    assert rows[0]["value"] == 10.6  # 同键覆盖
    # 不同来源并存（seed 与自动采集不互相覆盖）
    store.record_obs(conn, "glass_price_2_0_single", "2026-09-11", 10.4, source="seed", unit="元/㎡")
    assert len(store.series(conn, "glass_price_2_0_single", days=3650)) == 2


def test_latest_previous_and_window(conn):
    for day, value in (("2026-08-31", 9.5), ("2026-09-04", 10.5), ("2026-09-11", 10.5)):
        store.record_obs(conn, "glass_price_2_0_single", day, value, source="mysteel")
    latest = store.latest(conn, "glass_price_2_0_single")
    assert latest is not None
    assert latest["obs_date"] == "2026-09-11"
    prev = store.previous(conn, "glass_price_2_0_single")
    assert prev is not None
    assert prev["obs_date"] == "2026-09-04"
    assert len(store.window(conn, "glass_price_2_0_single", 30)) == 3
    assert store.latest(conn, "never_seen") is None


def test_announcements_and_consensus_roundtrip(conn, tmp_path):
    n = store.upsert_announcements(
        conn,
        [
            {
                "stock_code": "00968",
                "ann_date": "2026-09-15",
                "title": "翌日披露報表",
                "category": "buyback",
                "url": "https://example.com/a.pdf",
            }
        ],
    )
    assert n == 1
    # 重复写入不新增
    assert (
        store.upsert_announcements(
            conn,
            [
                {
                    "stock_code": "00968",
                    "ann_date": "2026-09-15",
                    "title": "翌日披露報表",
                    "category": "buyback",
                    "url": "https://example.com/a.pdf",
                }
            ],
        )
        == 0
    )
    assert len(store.recent_announcements(conn, days=60, stock_code="00968")) == 1
    assert store.recent_announcements(conn, days=60, stock_code="03868") == []

    store.upsert_consensus(
        conn,
        [
            {
                "stock_code": "00968",
                "fiscal_year": "2026",
                "metric": "net_profit",
                "broker": "高盛",
                "value": -927.0,
                "as_of": "2026-07-27",
            }
        ],
    )
    rows = store.consensus_for(conn, "00968", "2026")
    assert len(rows) == 1
    assert rows[0]["broker"] == "高盛"


def test_fetch_log(conn):
    store.log_fetch(conn, store.FetchResult("etnet", "ok", 26, "写入26行", 327))
    logs = store.last_fetch_log(conn, limit=5)
    assert logs[0]["source"] == "etnet"
    assert logs[0]["items"] == 26


# ── signals ───────────────────────────────────────────────────────────
def test_signal_s1_triggers_when_price_and_inventory_align(conn, registry):
    store.record_obs(conn, "glass_price_2_0_single", "2026-09-11", 10.6, source="mysteel")
    store.record_obs(conn, "glass_inventory_days", "2026-09-11", 38.0, source="manual")
    results = sig.evaluate(conn, registry, "S1_glass_price")
    assert results[0].status == sig.TRIGGERED
    assert results[0].passed == 2


def test_signal_pending_when_partial_data(conn, registry):
    store.record_obs(conn, "glass_price_2_0_single", "2026-09-11", 10.6, source="mysteel")
    result = sig.evaluate(conn, registry, "S1_glass_price")[0]
    assert result.status == sig.PENDING  # 价格达标但库存缺数据
    assert result.passed == 1


def test_signal_insufficient_without_any_data(conn, registry):
    result = sig.evaluate(conn, registry, "S3_demand")[0]
    assert result.status == sig.INSUFFICIENT


def test_pct_change_condition(conn, registry):
    for day, value in (("2026-06-30", 100.0), ("2026-07-31", 120.0)):
        store.record_obs(conn, "consensus_np_fy2027", day, value, source="etnet")
    cond = sig.Condition(indicator="consensus_np_fy2027", op="pct_chg_gte", value=10, window_days=90)
    result = sig.evaluate_condition(conn, registry, cond)
    assert result.status == sig.PASS
    assert "+20.0%" in result.detail


def test_mom_streak_condition(conn, registry):
    # 注意：全部用“过去”的日期——store 现在带 as_of 上界（今天），未来日期不可见
    for day, value in (
        ("2026-03-31", 10.0),
        ("2026-04-30", 11.0),
        ("2026-05-31", 12.0),
        ("2026-06-30", 13.0),
    ):
        store.record_obs(conn, "module_schedule_cn", day, value, source="manual")
    cond = sig.Condition(indicator="module_schedule_cn", op="mom_streak_gte", value=3, window_days=200)
    assert sig.evaluate_condition(conn, registry, cond).status == sig.PASS
    cond5 = sig.Condition(indicator="module_schedule_cn", op="mom_streak_gte", value=5, window_days=200)
    assert sig.evaluate_condition(conn, registry, cond5).status == sig.FAIL


def test_verdict_levels(conn, registry):
    # 无数据 → pending/bearish 分支不抛异常
    level, text = sig.verdict(sig.evaluate(conn, registry))
    assert level in {"bullish", "watch", "pending", "bearish"}
    assert isinstance(text, str)


# ── CLI 装配 ──────────────────────────────────────────────────────────
def test_pv_command_group_is_registered():
    """`nous pv` 子命令组必须挂到主 app 上，且每个子命令都能 --help。"""
    from typer.testing import CliRunner

    from nous.cli import app

    assert "pv" in {g.name for g in app.registered_groups}
    runner = CliRunner()
    for cmd in ("fetch", "show", "signal", "xinyi", "digest", "seed", "set", "log"):
        result = runner.invoke(app, ["pv", cmd, "--help"])
        assert result.exit_code == 0, f"pv {cmd} --help 失败: {result.output[:200]}"


# ── xinyi model ───────────────────────────────────────────────────────
def test_model_scenarios_ordering():
    model = xinyi.load_model()
    bear, base, bull = (xinyi.run(model, name) for name in ("bear", "base", "bull"))
    assert bull.fy_attributable > base.fy_attributable > bear.fy_attributable
    # Q4 实现价越高，玻璃毛利率越高
    assert bull.h2_glass_gm > base.h2_glass_gm > bear.h2_glass_gm


def test_model_calibrates_to_h1_actuals():
    """默认参数下，模型应能复现 2026H1 的经营利润拆解（毛利-费用块）。"""
    model = xinyi.load_model()
    h1 = model["h1_actual"]
    assert pytest.approx(h1["gross_profit"] - h1["opex"], rel=1e-6) == h1["operating_profit"]
    assert pytest.approx(h1["operating_profit"] - h1["finance_tax"], rel=1e-6) == h1["net_profit"]
    minority = h1["renewable_group_net"] * model["h2_model"]["minority_ratio"]
    assert minority == pytest.approx(h1["minority"], rel=0.01)


def test_model_unit_economics():
    """每 0.1 元/㎡ 约对应单季毛利 0.37 亿元（口径自检）。"""
    model = xinyi.load_model()
    a = xinyi.run(model, "base")
    patched = dict(model)
    scenarios = dict(patched["scenarios"])
    scenarios["_p"] = {**scenarios["base"], "asp_q4": scenarios["base"]["asp_q4"] + 0.1}
    patched["scenarios"] = scenarios
    b = xinyi.run(patched, "_p")
    delta = b.fy_attributable - a.fy_attributable
    assert 30 < delta < 45  # 百万元量级


def test_sensitivity_is_monotonic():
    model = xinyi.load_model()
    rows = xinyi.sensitivity(model, asp_min=9.4, asp_max=11.0, step=0.2)
    values = [r["fy_attributable"] for r in rows]
    assert values == sorted(values)
    assert len(rows) == 9


def test_eps_and_pe_consistency():
    model = xinyi.load_model()
    bull = xinyi.run(model, "bull")
    shares = model["company"]["shares_million"]
    assert bull.fy_eps_fen == pytest.approx(bull.fy_attributable / shares * 100, rel=1e-6)
    assert bull.pe and bull.pe > 0
    bear = xinyi.run(model, "bear")
    assert bear.pe is None  # 亏损不给 PE
