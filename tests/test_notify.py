"""出站推送通道测试 — 全部离线（monkeypatch requests）。"""

from __future__ import annotations

import pytest

from nous.core import notify


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        notify._ENV_CHANNELS,
        notify._ENV_MAX_CHARS,
        notify._ENV_WECOM_KEY,
        notify._ENV_WEBHOOK,
        notify._ENV_BARK_URL,
        notify._ENV_SC_KEY,
    ):
        monkeypatch.delenv(name, raising=False)
    yield


# ── 配置解析 ───────────────────────────────────────────────────────────
def test_default_is_stdout_only():
    assert notify.configured_channels() == ["stdout"]
    assert notify.deliverable_channels() == []


def test_channel_without_credential_is_skipped(monkeypatch):
    monkeypatch.setenv(notify._ENV_CHANNELS, "wecom,stdout")
    assert notify.configured_channels() == ["stdout"]  # 缺 key → 跳过

    monkeypatch.setenv(notify._ENV_WECOM_KEY, "abc-123")
    assert notify.configured_channels() == ["wecom", "stdout"]
    assert notify.deliverable_channels() == ["wecom"]


def test_unknown_channel_ignored(monkeypatch):
    monkeypatch.setenv(notify._ENV_CHANNELS, "telegram,stdout")
    assert notify.configured_channels() == ["stdout"]


def test_explicit_channels_override_env(monkeypatch):
    monkeypatch.setenv(notify._ENV_CHANNELS, "stdout")
    monkeypatch.setenv(notify._ENV_WEBHOOK, "https://example.com/hook")
    assert notify.configured_channels(["webhook"]) == ["webhook"]
    assert notify.configured_channels(["wecom"]) == []  # 未配置凭据


def test_max_chars_env(monkeypatch):
    assert notify.max_chars() == notify.DEFAULT_MAX_CHARS
    monkeypatch.setenv(notify._ENV_MAX_CHARS, "500")
    assert notify.max_chars() == 500
    monkeypatch.setenv(notify._ENV_MAX_CHARS, "abc")
    assert notify.max_chars() == notify.DEFAULT_MAX_CHARS
    monkeypatch.setenv(notify._ENV_MAX_CHARS, "10")  # 下限保护
    assert notify.max_chars() == 200


# ── 截断 ───────────────────────────────────────────────────────────────
def test_truncate_keeps_short_body():
    assert notify.truncate("abc", limit=10) == "abc"


def test_truncate_cuts_long_body():
    out = notify.truncate("x" * 100, limit=40)
    assert len(out) <= 40
    assert "截断" in out


# ── 通道载荷 ───────────────────────────────────────────────────────────
class _Resp:
    status_code = 200
    text = "ok"


def test_wecom_payload(monkeypatch):
    monkeypatch.setenv(notify._ENV_WECOM_KEY, "KEY-1")
    seen: dict = {}

    def fake_post(url, json=None, data=None, timeout=None):
        seen.update(url=url, json=json, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    results = notify.send("标题", "正文", channels=["wecom"])
    assert results[0].ok
    assert "key=KEY-1" in seen["url"]
    assert seen["json"]["msgtype"] == "markdown"
    assert "标题" in seen["json"]["markdown"]["content"]


def test_webhook_payload_shape(monkeypatch):
    monkeypatch.setenv(notify._ENV_WEBHOOK, "https://example.com/hook")
    seen: dict = {}

    def fake_post(url, json=None, data=None, timeout=None):
        seen.update(url=url, json=json)
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    notify.send("T", "B", channels=["webhook"])
    payload = seen["json"]
    assert set(payload) == {"title", "text", "source", "ts"}
    assert payload["source"] == "nous"
    assert payload["title"] == "T"


def test_bark_uses_get_with_quoted_path(monkeypatch):
    monkeypatch.setenv(notify._ENV_BARK_URL, "https://api.day.app/DEVKEY")
    seen: dict = {}

    def fake_get(url, timeout=None):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(notify.requests, "get", fake_get)
    notify.send("标题 A", "正文 B", channels=["bark"])
    assert seen["url"].startswith("https://api.day.app/DEVKEY/")
    assert "group=nous" in seen["url"]
    assert " " not in seen["url"]  # 已 URL 编码


def test_serverchan_posts_form(monkeypatch):
    monkeypatch.setenv(notify._ENV_SC_KEY, "SCT123")
    seen: dict = {}

    def fake_post(url, json=None, data=None, timeout=None):
        seen.update(url=url, data=data)
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    notify.send("T", "B", channels=["serverchan"])
    assert seen["url"] == "https://sctapi.ftqq.com/SCT123.send"
    assert seen["data"]["title"] == "T"
    assert seen["data"]["desp"] == "B"


# ── 容错 ───────────────────────────────────────────────────────────────
def test_http_error_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv(notify._ENV_WEBHOOK, "https://example.com/hook")

    class Bad:
        status_code = 500
        text = "boom"

    monkeypatch.setattr(notify.requests, "post", lambda *a, **kw: Bad())
    results = notify.send("T", "B", channels=["webhook"])
    assert results[0].status == "error"
    assert "HTTP 500" in results[0].detail


def test_network_exception_is_reported(monkeypatch):
    monkeypatch.setenv(notify._ENV_WEBHOOK, "https://example.com/hook")

    def boom(*a, **kw):
        raise ConnectionError("no route")

    monkeypatch.setattr(notify.requests, "post", boom)
    results = notify.send("T", "B", channels=["webhook"])
    assert results[0].status == "error"
    assert "ConnectionError" in results[0].detail


def test_stdout_channel_prints(capsys):
    results = notify.send("标题", "正文内容", channels=["stdout"])
    assert results[0].ok
    out = capsys.readouterr().out
    assert "nous notify" in out
    assert "正文内容" in out


def test_dry_run_skips(monkeypatch):
    monkeypatch.setenv(notify._ENV_WEBHOOK, "https://example.com/hook")
    results = notify.send("T", "B", channels=["webhook"], dry_run=True)
    assert results[0].status == "skipped"


def test_no_channel_configured_reports_skipped():
    results = notify.send("T", "B", channels=["wecom"])
    assert results[0].channel == "none"
    assert results[0].status == "skipped"


def test_describe_lists_all_channels():
    text = notify.describe()
    for channel in notify.ALL_CHANNELS:
        assert channel in text
    assert "启用通道" in text


# ── 与周度流水线的衔接 ─────────────────────────────────────────────────
def test_weekly_outcome_push_body_contains_verdict_registry():
    from nous.research.pvglass import pipeline
    from nous.research.pvglass.registry import load_registry
    from nous.research.pvglass.store import FetchResult

    reg = load_registry()
    outcome = pipeline.WeeklyOutcome(ran_at="2026-09-16T08:40:00")
    outcome.verdict_level = "watch"
    outcome.verdict_text = "1/7 信号确认"
    outcome.signal_rows = [
        {"id": "S4_supply_exit", "name": "供给侧真实退出", "status": "triggered", "passed": 2, "total": 2}
    ]
    outcome.fetches = [FetchResult("etnet", "ok", 26, "", 10)]
    body = outcome.push_body()
    assert "WATCH" in body
    assert "✅ S4_supply_exit" in body
    assert outcome.push_title().startswith("光伏玻璃周度跟踪")
    assert reg.indicators  # 保证 registry 可用（避免 import 期副作用）


def test_run_weekly_pushes_only_when_channel_configured(tmp_path, monkeypatch):
    """默认策略：没有真实通道就不推送（避免交互式使用时刷屏）。"""
    from nous.core.db import get_db
    from nous.research.pvglass import pipeline, store as pv_store
    from nous.research.pvglass.registry import load_registry

    registry = load_registry()
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path / "data"))
    pv_store.init_db()
    outcome = pipeline.run_weekly(
        registry=registry, skip_fetch=True, skip_seed=True, skip_digest=True
    )
    assert outcome.notifications == []  # 未配置通道 → 不推

    monkeypatch.setenv(notify._ENV_CHANNELS, "stdout")
    outcome2 = pipeline.run_weekly(
        registry=registry, skip_fetch=True, skip_seed=True, skip_digest=True, push=True
    )
    assert outcome2.notifications and outcome2.notifications[0].channel == "stdout"
    assert any("推送" in line for line in outcome2.summary_lines())

    with get_db(pv_store.DB_NAME) as conn:  # 库可正常打开（无副作用）
        pv_store.ensure_schema(conn)
