"""历史回填测试 — 离线（枚举模板 / 差分逻辑 / 限速器 / dry-run）。"""

from __future__ import annotations

from datetime import date

import pytest

from nous.core.db import get_db
from nous.research.pvglass import backfill as bf
from nous.research.pvglass import store
from nous.research.pvglass.registry import load_registry
from nous.research.pvglass.sources import demand


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    store.init_db()
    with get_db(store.DB_NAME, write=True) as c:
        store.ensure_schema(c)
        yield c


# ── 归档枚举 ───────────────────────────────────────────────────────────
def test_month_archive_urls_crosses_year():
    urls = bf.month_archive_urls(4, today=date(2026, 1, 15))
    assert urls == [
        "https://www.pvmeng.com/2026/01/",
        "https://www.pvmeng.com/2025/12/",
        "https://www.pvmeng.com/2025/11/",
        "https://www.pvmeng.com/2025/10/",
    ]


def test_month_archive_urls_minimum_one():
    assert len(bf.month_archive_urls(0, today=date(2026, 9, 16))) == 1


def test_stats_page_urls():
    assert bf.STATS_FIRST.endswith("/sj/zxfbhjd/")
    assert bf.STATS_LIST.format(page=3).endswith("index_3.html")


# ── 差分（回填的核心）────────────────────────────────────────────────────
def test_diff_ytd_normal_month():
    items = bf.diff_ytd([("2026-06-30", 72.07), ("2026-07-31", 86.15)])
    assert len(items) == 1
    item = items[0]
    assert item.indicator_id == "pv_install_cn_monthly"
    assert item.obs_date == "2026-07-31"
    assert item.value == pytest.approx(14.08, abs=0.01)
    assert item.unit == "GW"
    assert "86.15" in item.note and "72.07" in item.note


def test_diff_ytd_cross_year_is_labelled():
    """跨年：上一期是上年 12 月（全年累计），差值 = 新一年 1-2 月合计。"""
    items = bf.diff_ytd([("2025-12-31", 300.0), ("2026-02-28", 340.0)])
    assert len(items) == 1
    assert items[0].value == pytest.approx(40.0)
    assert "跨年" in items[0].note and "1-2 月" in items[0].note


def test_diff_ytd_skips_negative_and_next_period():
    """累计回落（疑似修订）：该期与下一期都不输出，并给出原因。"""
    rows = [("2026-08-31", 90.0), ("2026-07-31", 100.0), ("2026-09-30", 110.0)]
    items, notes = bf.diff_ytd_detailed(rows)
    assert items == []  # 宁可空，不可错：08 负差 → 08 与 09 都跳过
    assert len(notes) == 2
    assert "累计回落" in notes[0]


def test_diff_ytd_year_reset_does_not_contaminate_next_period():
    """跨年重置（上年最后一期非 12 月）：只跳该期，新年度内部差分照常。"""
    rows = [
        ("2025-10-31", 252.87),
        ("2026-02-28", 32.48),   # 跨年重置 → 跳过
        ("2026-03-31", 41.39),   # 新年度内部差分 → 保留
    ]
    items, notes = bf.diff_ytd_detailed(rows)
    assert [i.obs_date for i in items] == ["2026-03-31"]
    assert items[0].value == pytest.approx(8.91, abs=0.01)
    assert len(notes) == 1 and "跨年重置" in notes[0]


def test_diff_ytd_resumes_after_break():
    """回落之后的**第二期**应恢复输出。"""
    rows = [
        ("2026-06-30", 100.0),
        ("2026-07-31", 90.0),   # 回落 → 跳过 07 与 08
        ("2026-08-31", 120.0),
        ("2026-09-30", 135.0),  # 恢复正常
    ]
    items, notes = bf.diff_ytd_detailed(rows)
    assert [i.obs_date for i in items] == ["2026-09-30"]
    assert items[0].value == pytest.approx(15.0)
    assert len(notes) == 2


def test_diff_ytd_multiple_months_chain():
    rows = [
        ("2026-05-31", 50.0),
        ("2026-06-30", 60.0),
        ("2026-07-31", 74.08),
    ]
    items = bf.diff_ytd(rows)
    assert [round(i.value, 2) for i in items] == [10.0, 14.08]


# ── 限速器 ─────────────────────────────────────────────────────────────
class _CountingFetcher:
    def __init__(self):
        self.urls: list[str] = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return "<html>ok</html>"


def test_limiter_counts_requests():
    fetcher = _CountingFetcher()
    limiter = bf.Limiter(fetcher, sleep=0)  # type: ignore[arg-type]
    limiter.get("https://a")
    limiter.get("https://b")
    assert limiter.count == 2
    assert fetcher.urls == ["https://a", "https://b"]


# ── 统计局列表解析（复用 demand 的正则）────────────────────────────────
STATS_INDEX_PAGE = """
<li><a href="./202609/t20260915_1965308.html" title='2026年8月份规模以上工业增加值增长5.2%'>8月</a></li>
<li><a href="./202608/t20260817_1960001.html" title='2026年7月份规模以上工业增加值增长5.0%'>7月</a></li>
<li><a href="./202608/t20260810_1959000.html" title='2026年7月份居民消费价格变动情况'>CPI</a></li>
"""


def test_stats_index_parsing_filters_non_value_added():
    rows = []
    for path, title in demand._STATS_LINK_RE.findall(STATS_INDEX_PAGE):
        m = demand._STATS_TITLE_RE.search(title)
        if m:
            rows.append((path, m.groups()))
    assert len(rows) == 2  # CPI 条目被标题正则滤掉
    assert rows[0][1] == ("2026", "8")
    assert rows[1][1] == ("2026", "7")


# ── dry-run 与注册表 ───────────────────────────────────────────────────
def test_run_dry_run_writes_nothing(conn):
    registry = load_registry()
    results = bf.run(conn, registry, sources=["tender", "install"], dry_run=True)
    assert all(res.status == "skipped" for res in results)
    assert all(res.written == 0 for res in results)
    assert store.all_obs(conn, "tender_volume_pv") == []
    assert store.all_obs(conn, "pv_install_cn_ytd") == []


def test_handlers_registry():
    assert set(bf.HANDLERS) == {"install", "stats", "tender", "prices"}
    for name, handler in bf.HANDLERS.items():
        assert callable(handler), name


def test_backfill_uses_distinct_source(conn):
    """回填写入必须带 source=backfill，避免覆盖增量采集（PK 含 source）。"""
    assert bf.SOURCE == "backfill"
    store.record_obs(conn, "tender_volume_pv", "2026-08-31", 7.72, source="demand")
    store.record_obs(conn, "tender_volume_pv", "2026-08-31", 7.72, source=bf.SOURCE)
    sources = {r["source"] for r in store.all_obs(conn, "tender_volume_pv")}
    assert sources == {"demand", "backfill"}
