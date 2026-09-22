"""需求侧采集器解析测试 — 全部用实测原文片段，不联网。

片段来源：docs/research/pvglass-demand-sources.md（子代理逐条 fetch 实测），
以及本次落地时实测确认的页面结构。
"""

from __future__ import annotations

import pytest

from nous.research.pvglass.http import to_float
from nous.research.pvglass.sources import demand


# ── 工具函数 ───────────────────────────────────────────────────────────
def test_month_end_handles_leap_year():
    assert demand._month_end(2026, 2) == "2026-02-28"
    assert demand._month_end(2024, 2) == "2024-02-29"
    assert demand._month_end(2026, 9) == "2026-09-30"


def test_to_float_accepts_numeric_input():
    """回归：SQLite 直出的 float 曾被当成 str 调 .replace() 而炸。"""
    assert to_float(86.15) == 86.15
    assert to_float(0.0) == 0.0  # 0 不应被当成空值
    assert to_float(None) is None
    assert to_float(float("nan")) is None
    assert to_float("1,288.04") == 1288.04
    assert to_float("--") is None


# ── 装机（光动百科转载 NEA 一览表）────────────────────────────────
PVMENG_SUMMARY = """
<li><mark style="background-color:rgba(0, 0, 0, 0)">太阳能发电今年新增装机：8615万千瓦</mark></li>
<li><mark style="background-color:rgba(0, 0, 0, 0)">太阳能发电累计装机容量：128804万千瓦（31.58<strong>%</strong>）</mark></li>
<figure class="wp-block-table"><table><tbody>
<tr><td colspan="4"><strong>全国电力统计数据一览表(截至2026年7月)</strong></td></tr>
</tbody></table></figure>
"""

PVMENG_SEARCH_PAGE = """
<div class="search-result">
  <a href="https://www.pvmeng.com/2026/09/14/78521/">新能源装机月度观察</a>
  <a href="https://www.pvmeng.com/2026/08/25/77041/">2026年1-7月全国电力统计数据一览表</a>
  <a href="https://www.pvmeng.com/2026/07/22/76001/">2026年上半年全国电力工业统计数据</a>
</div>
"""


def test_pvmeng_summary_extraction():
    ytd = demand._PVMENG_YTD_RE.search(PVMENG_SUMMARY)
    cum = demand._PVMENG_CUM_RE.search(PVMENG_SUMMARY)
    period = demand._PVMENG_PERIOD_RE.search(PVMENG_SUMMARY)
    assert ytd and ytd.group(1) == "8615"  # 万千瓦
    assert cum and cum.group(1) == "128804"
    assert period and period.groups() == ("2026", "7")
    ytd_wan_kw = to_float(ytd.group(1).replace(",", ""))
    assert ytd_wan_kw is not None
    assert ytd_wan_kw / 100.0 == 86.15


def test_pvmeng_post_url_parsing():
    """搜索页里既有全站新文也有一览表，靠正则取到 年/月/日/id 四段。"""
    urls = demand._PVMENG_POST_RE.findall(
        "https://www.pvmeng.com/2026/08/25/77041/ https://www.pvmeng.com/2026/09/14/78521/"
    )
    assert len(urls) == 2
    assert ("2026", "08", "25", "77041") in urls


# ── 电池产量（国家统计局）──────────────────────────────────────────
STATS_LIST_FRAGMENT = """
<li>
  <a class="fl pc_1600" href="./202609/t20260915_1965308.html" target="_blank"
     title='2026年8月份规模以上工业增加值增长5.2%'> 2026年8月份规模以上工业增加值增长5.2% </a>
  <span> 2026-09-15 </span>
</li>
"""

STATS_ARTICLE_TEXT = (
    "发电机组（发电设备）（万千瓦） 2806 -10.2 23696 1.0 "
    "太阳能电池（光伏电池）（万千瓦） 6702 -12.9 50590 -14.6 "
    "微型计算机设备（万台） 2075 -25.6"
)


def test_stats_list_link_and_title():
    pairs = demand._STATS_LINK_RE.findall(STATS_LIST_FRAGMENT)
    assert pairs, "列表页锚点正则未命中（回归：href 前有 ./ 前缀）"
    path, title = pairs[0]
    assert path == "202609/t20260915_1965308.html"
    m = demand._STATS_TITLE_RE.search(title)
    assert m and m.groups() == ("2026", "8")


def test_stats_cell_output_row():
    m = demand._STATS_CELL_RE.search(STATS_ARTICLE_TEXT)
    assert m, "「太阳能电池（光伏电池）」行未命中"
    month, yoy_month, ytd, yoy_ytd = m.groups()
    month_gw = to_float(month)
    assert month_gw is not None
    assert month_gw / 100.0 == pytest.approx(67.02)  # 万千瓦 → GW
    assert yoy_month == "-12.9"
    assert ytd == "50590" and yoy_ytd == "-14.6"


# ── 组件排产（DataBM）──────────────────────────────────────────────
DATABM_WEEKLY_FRAGMENT = (
    "<strong>供应方面</strong>，本周出现<strong>头部厂商排产分化</strong>现象，TOP5厂商中大部分仍"
    "<strong>保持提产预期</strong>，行业整体排产预期仍较8月提高1-2GW左右。"
    "据数字新能源DataBM.com调研，9月组件排产集中于<strong>46-50GW</strong>之间。"
)

DATABM_JSONLD_FRAGMENT = """
{ "@type": "ListItem", "position": 4,
  "url": "https://www.databm.com/news/69115396959506115.html",
  "name": "DBM周评：&ldquo;底线&rdquo;划定！龙头集体&ldquo;挺价&rdquo;" }
"""


def test_databm_schedule_range_extraction():
    text = demand._strip(DATABM_WEEKLY_FRAGMENT)
    m = demand._SCHEDULE_RE.search(text)
    assert m, "排产区间正则未命中"
    assert m.groups() == ("9", "46", "50")
    low, high = to_float(m.group(2)), to_float(m.group(3))
    assert low is not None and high is not None
    assert (low + high) / 2 == 48.0


def test_databm_alt_pattern_without_month():
    text = demand._strip("本周组件排产集中在 47-49GW，环比略增。")
    assert demand._SCHEDULE_RE.search(text) is None
    alt = demand._SCHEDULE_ALT_RE.search(text)
    assert alt and alt.groups() == ("47", "49")


def test_databm_jsonld_pair_parsing():
    pairs = demand._DATABM_ITEM_RE.findall(DATABM_JSONLD_FRAGMENT)
    assert pairs == [("https://www.databm.com/news/69115396959506115.html",
                      "DBM周评：&ldquo;底线&rdquo;划定！龙头集体&ldquo;挺价&rdquo;")]


def test_databm_time_pattern():
    html = '<meta property="article:modified_time" content="2026-09-15T21:08:57+08:00"/>'
    m = demand._DATABM_TIME_RE.search(html)
    assert m and m.groups() == ("2026", "09")


# ── 招投标（集邦）────────────────────────────────────────────────
TENDER_TAXONOMY_TEXT = (
    "2026年8月，国内光伏组件招标7.72GW，环比大幅增长；中标量（含入围）14.11GW，"
    "协鑫、晶澳领跑定标榜；N型组件投标均价0.722元/W"
)

TENDER_ARTICLE_TEXT = (
    "据集邦光储观察不完全统计，2026年8月国内光伏组件招标规模达7.72GW，"
    "中国铁建开启3GW TOPCon组件集采。"
)


def test_tender_taxonomy_and_article_patterns():
    tax = demand._TENDER_TAXONOMY_RE.findall(TENDER_TAXONOMY_TEXT)
    assert tax == [("2026", "8", "7.72")]
    art = demand._TENDER_ARTICLE_RE.search(TENDER_ARTICLE_TEXT)
    assert art and art.groups() == ("2026", "8", "7.72")
    # 两条正则都只取「招标」口径，不取中标 14.11
    assert "14.11" not in str(tax)


def test_tender_seven_month_value():
    assert demand._TENDER_TAXONOMY_RE.findall("2026年7月，国内光伏组件招标2.7GW，环比大幅下降") == [
        ("2026", "7", "2.7")
    ]


# ── 汇总行为 ─────────────────────────────────────────────────────────
def test_collect_reports_per_step_notes(tmp_path, monkeypatch):
    """四步任一步失败都不该让整条 demand 采集抛异常（用空 Fetcher 模拟断网）。"""
    from nous.core.db import get_db
    from nous.research.pvglass import store
    from nous.research.pvglass.registry import load_registry

    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    reg = load_registry()
    store.init_db()

    class DeadFetcher:
        def get(self, *a, **kw):
            from nous.research.pvglass.http import FetchError

            raise FetchError("offline")

    monkeypatch.setattr(demand, "Fetcher", lambda *a, **kw: DeadFetcher())
    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        result = demand.collect(conn, reg)
    assert result.source == "demand"
    assert result.status in {"partial", "error"}
    assert "装机" in result.message  # 每步都有说明
