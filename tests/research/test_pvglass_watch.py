"""观察清单跃迁告警测试（离线，隔离数据库）。

这里测的是**比较机制**，不是市场判断：给定"上次状态"与"这次状态"，
能不能可靠地算出该不该告警。所以下面的期望值直接写成状态对，不依赖任何真实行情。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from nous.core.db import get_db
from nous.research.pvglass import signals as sig
from nous.research.pvglass import store
from nous.research.pvglass import watch
from nous.research.pvglass.registry import Registry, load_registry


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


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _set(conn, indicator_id: str, value: float, days_ago: int = 1) -> None:
    store.record_obs(conn, indicator_id, _days_ago(days_ago), value, source="manual")
    conn.commit()


def _s1_keys() -> tuple[str, str]:
    """S1 的两条条件键（与 watch.build_items 的键格式一致）。"""
    return (
        "cond:S1_glass_price@glass_price_2_0_single:gte:10.5",
        "cond:S1_glass_price@glass_inventory_days:lte:40",
    )


# ── 基线语义 ───────────────────────────────────────────────────────────
def test_first_run_builds_baseline_without_alerting(conn, registry):
    """首次运行必须只建基线、不告警：否则装完当天会把全部历史状态当成新变化。"""
    out = watch.check(conn, registry)
    assert out.is_baseline_run is True
    assert out.changes == []
    assert out.alerted is False
    assert out.persisted == len(out.items) > 0
    # 基线之后状态表里应有内容，第二次运行就不是基线了
    assert conn.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0] > 0


def test_second_run_without_change_alerts_nothing(conn, registry):
    watch.check(conn, registry)
    out = watch.check(conn, registry)
    assert out.is_baseline_run is False
    assert out.changes == []
    assert out.alerted is False


def test_repeated_runs_are_idempotent(conn, registry):
    """同一状态连跑 3 次：只有第 1 次建基线，之后都不告警。"""
    first = watch.check(conn, registry)
    second = watch.check(conn, registry)
    third = watch.check(conn, registry)
    assert first.is_baseline_run and not first.alerted
    assert not second.alerted and not third.alerted


def test_reset_clears_baseline_and_next_run_is_silent_again(conn, registry):
    watch.check(conn, registry)
    n = watch.reset(conn)
    assert n > 0
    out = watch.check(conn, registry)
    assert out.is_baseline_run is True
    assert out.changes == []


def test_dry_run_does_not_persist(conn, registry):
    watch.check(conn, registry)
    before = conn.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0]
    watch.check(conn, registry, persist=False)
    assert conn.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0] == before


# ── 跃迁检测（核心）────────────────────────────────────────────────────
def test_condition_flip_is_detected(conn, registry):
    """库存 45 → 39 应当被检出（价格条件仍不满足，所以 S1 本身还不触发）。"""
    _set(conn, "glass_inventory_days", 45.0)
    watch.check(conn, registry)  # 建基线：45 > 40 → fail
    _set(conn, "glass_inventory_days", 39.0, days_ago=0)
    out = watch.check(conn, registry)

    flipped = [c for c in out.changes if c.item.key == _s1_keys()[1]]
    assert len(flipped) == 1, [c.item.key for c in out.changes]
    assert flipped[0].before == sig.FAIL
    assert flipped[0].after == sig.PASS
    assert out.alerted is True


def test_signal_trigger_is_critical(conn, registry):
    """两条条件都满足 → S1 从非触发变为 triggered，且判为 critical。"""
    _set(conn, "glass_price_2_0_single", 10.5)
    _set(conn, "glass_inventory_days", 45.0)
    watch.check(conn, registry)  # 基线：价格 ✓ 库存 ✗ → not_triggered
    _set(conn, "glass_inventory_days", 39.0, days_ago=0)
    out = watch.check(conn, registry)

    sig_changes = [c for c in out.changes if c.item.key == "signal:S1_glass_price"]
    assert len(sig_changes) == 1
    assert sig_changes[0].after == sig.TRIGGERED
    assert sig_changes[0].severity == watch.CRITICAL
    assert out.critical_count >= 1


def test_no_data_to_pass_is_critical(conn, registry):
    """数据补齐后条件直接成立 —— 最容易被漏掉的一类，定为 critical。"""
    _set(conn, "glass_price_2_0_single", 10.5)
    # 库存完全没数据 → S1 该条件为 no_data
    watch.check(conn, registry)
    _set(conn, "glass_inventory_days", 30.0, days_ago=0)
    out = watch.check(conn, registry)

    flipped = [c for c in out.changes if c.item.key == _s1_keys()[1]]
    assert len(flipped) == 1
    assert flipped[0].before == sig.NO_DATA
    assert flipped[0].after == sig.PASS
    assert flipped[0].severity == watch.CRITICAL


def test_verdict_level_change_is_tracked(conn, registry):
    """汇总判读本身也是一个被观察项（转 bullish 视为 critical）。"""
    watch.check(conn, registry)
    keys = {i.key for i in watch.check(conn, registry).items}
    assert "verdict:level" in keys


# ── watch: 段（不进 verdict 的独立阈值）────────────────────────────────
def test_watch_group_is_evaluated_and_not_in_verdict(conn, registry):
    """watch 段的条件要真求值（有状态），但不得混进 signals/verdict。"""
    assert [s.id for s in registry.watch], "watch 段没解析出来"
    out = watch.check(conn, registry)
    watch_keys = {i.key for i in out.items if i.kind == "watch"}
    assert watch_keys, "watch 组没有被求值成观察项"

    results = sig.evaluate(conn, registry)
    signal_ids = {r.signal.id for r in results}
    for wid in (s.id for s in registry.watch):
        assert wid not in signal_ids, f"{wid} 混进了 signals，会污染 verdict"


def test_watch_condition_note_travels_with_alert(conn, registry):
    """口径警告必须随告警一起到达，不能只留在 YAML 里。"""
    _set(conn, "glass_inventory_days", 44.0)
    out = watch.check(conn, registry)
    items = [i for i in out.items if i.key.startswith("watch:W2_inventory_35")]
    assert items, "W2 观察项缺失"
    assert "隆众" in items[0].detail, items[0].detail


def test_watch_group_thresholds_match_declared_values(conn, registry):
    """W1/W2 的阈值就是 §7 观察清单里那两个产业阈值。

    “库存进 40 天”**故意不单列**：S1 自带该条件，逐条件比较已经能报出来，
    单列会导致同一件事报两行（在真实推送文案里验证过）。
    """
    declared = {
        (c.indicator, c.op, c.value)
        for s in registry.watch  # watch 段是 Signal 定义 → conditions 是 Condition 本体
        for c in s.conditions
    }
    assert ("glass_capacity_operating", "lte", 65000.0) in declared
    assert ("glass_inventory_days", "lte", 35.0) in declared
    assert ("glass_inventory_days", "lte", 40.0) not in declared, "与 S1 的条件重复了"


def test_watch_keys_are_stable_across_runs(conn, registry):
    """键必须稳定，否则每轮都会误报'新对象'。"""
    a = {i.key for i in watch.check(conn, registry).items}
    b = {i.key for i in watch.check(conn, registry).items}
    assert a == b


# ── 推送文案 ───────────────────────────────────────────────────────────
def test_push_body_marks_baseline_run(conn, registry):
    out = watch.check(conn, registry)
    body = out.push_body()
    assert "首次运行" in body
    assert "不告警" in body


def test_push_body_lists_changes_with_severity(conn, registry):
    _set(conn, "glass_price_2_0_single", 10.5)
    _set(conn, "glass_inventory_days", 45.0)
    watch.check(conn, registry)
    _set(conn, "glass_inventory_days", 39.0, days_ago=0)
    out = watch.check(conn, registry)
    body = out.push_body()
    assert "S1_glass_price" in body
    assert "🔴" in body  # critical 有独立标记
    assert "→" in body


def test_push_title_reports_no_change_explicitly(conn, registry):
    watch.check(conn, registry)
    out = watch.check(conn, registry)
    assert "无变化" in out.push_title()


# ── 调度器接入 ─────────────────────────────────────────────────────────
def test_scheduler_job_is_registered_with_expected_cron():
    """任务必须真的注册在调度器里（否则告警永远不会自己跑）。"""
    from nous.scheduler import JOBS

    entry = [j for j in JOBS if j[0] == "pvglass-watch"]
    assert len(entry) == 1, [j[0] for j in JOBS if "pvglass" in j[0]]
    _name, cron, cmd, label, timeout = entry[0]
    assert cron == "30 18 * * 1-5", cron  # 工作日盘后
    assert cmd.rstrip().endswith("src/nous/scheduler/jobs/research/pvglass_watch.py")
    assert label
    assert 0 < timeout <= 300, "本任务不采集数据，不该给很长超时"


def test_scheduler_job_main_smoke(tmp_path, monkeypatch, capsys):
    """调度入口能跑通：空库→建基线→不推送→退出码 0。"""
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOUS_NOTIFY_CHANNELS", "stdout")
    from nous.scheduler.jobs.research import pvglass_watch

    rc = pvglass_watch.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "===REPORT_START===" in out and "===REPORT_END===" in out
    assert "已建基线" in out  # 冷启动只建基线，不告警
    assert "推送 stdout" not in out  # 没有跃迁就不该推


def test_job_does_not_fetch_and_does_not_push_without_change(conn, registry):
    """本任务只读库：无跃迁时 alerted 为假 → 调度器不会推送。"""
    watch.check(conn, registry)  # 建基线
    out = watch.check(conn, registry)
    assert out.alerted is False
    assert out.changes == []
