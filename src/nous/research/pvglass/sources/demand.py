"""需求侧采集器 — 装机 / 电池产量 / 组件排产 / 招投标。

来源与可行性均经实测（详见 docs/research/pvglass-demand-sources.md）：

| 指标 | 主源 | 形态 | 关键坑 |
|---|---|---|---|
| pv_install_cn_monthly | 光动百科转载 NEA《全国电力统计数据一览表》 | SSR HTML | NEA 官网该表 2025-11 起是**图片**；月值必须**差分** |
| cell_output_cn | 国家统计局《规模以上工业增加值》附表 | SSR 表格 | 口径是「太阳能电池（光伏电池）」含电池片/出口，**≠组件产量** |
| module_schedule_cn | 数字新能源 DataBM《DBM 周评》 | SSR 正文 | 是**调研预估值**且只有区间；与 SMM（含海外口径，约 +7GW）方向相反 |
| tender_volume_pv | 集邦 TrendForce 光储观察 | SSR 分类页+正文 | 同篇有「招标」与「中标（含入围）」两个口径，本采集器只取**招标** |

不可自动抓取（已记录原因，不做无效重试）：北极星（混淆 JS 挑战）、SMM 正文（登录墙）、
InfoLink/集邦月度排产（付费报告）、智汇光伏（仅公众号）。
"""

from __future__ import annotations

import calendar
import re
import sqlite3
from datetime import date, datetime
from typing import Any, Callable

from nous.research.pvglass import store
from nous.research.pvglass.http import FetchError, Fetcher, duration_ms, html_to_text, to_float
from nous.research.pvglass.registry import Registry

SOURCE = "demand"

PVMENG_SEARCH = "https://www.pvmeng.com/"
PVMENG_QUERY = "全国电力统计数据"
STATS_LIST = "https://www.stats.gov.cn/sj/zxfbhjd/"
# 关键词搜索命中正文，越具体越准（实测「排产集中于」直接命中目标文章且排第一）
DATABM_KEYWORDS: tuple[str, ...] = ("排产集中于", "组件排产", "排产")
DATABM_CHANNEL = "https://www.databm.com/photovoltaic/"
DATABM_SEARCH = "https://www.databm.com/news/search/list-1.html"  # ?keyword=...
TRENDFORCE_TENDER_TAXONOMY = "https://www.energytrend.cn/taxonomy/term/5334/"

_PVMENG_POST_RE = re.compile(r"https://www\.pvmeng\.com/(\d{4})/(\d{2})/(\d{2})/(\d+)/")
_PVMENG_YTD_RE = re.compile(r"太阳能发电今年新增装机[：:]\s*([\d,]+)\s*万千瓦")
_PVMENG_CUM_RE = re.compile(r"太阳能发电累计装机容量[：:]\s*([\d,]+)\s*万千瓦")
_PVMENG_PERIOD_RE = re.compile(r"一览表\s*[（(]?\s*截至\s*(\d{4})\s*年\s*(\d{1,2})\s*月")
_STATS_LINK_RE = re.compile(
    r'href="\.?(?:/sj/zxfbhjd/|/)?(\d{6}/t\d{8}_\d+\.html)"[^>]*title=[\'"]([^\'"]+)[\'"]'
)
_STATS_TITLE_RE = re.compile(r"(\d{4})年(\d{1,2})月份规模以上工业增加值增长")
_STATS_CELL_RE = re.compile(
    r"太阳能电池（光伏电池）（万千瓦）\s*(-?[\d.]+)\s*(-?[\d.]+)\s*(-?[\d.]+)\s*(-?[\d.]+)"
)
# DataBM 频道页的 JSON-LD ItemList 是最稳的发现入口（实测 url+name 成对出现）
_DATABM_NEWS_RE = re.compile(r'href="(https://www\.databm\.com/news/\d+\.html)"[^>]*>([^<]{4,90})')
_DATABM_ITEM_RE = re.compile(
    r'"url"\s*:\s*"(https://www\.databm\.com/news/\d+\.html)"\s*,\s*"name"\s*:\s*"([^"]{4,120})"'
)
_DATABM_TIME_RE = re.compile(r'article:modified_time"\s+content="(\d{4})-(\d{2})')
_SCHEDULE_RE = re.compile(
    r"(\d{1,2})月组件排产集中于\s*(\d{1,2})\s*[-~—至]\s*(\d{1,2})\s*GW"
)
#: 兼容未写明月份的写法（月份从文章时间/当期推断）
_SCHEDULE_ALT_RE = re.compile(r"组件排产[^。；]{0,24}?(\d{1,2})\s*[-~—至]\s*(\d{1,2})\s*GW")
_TENDER_TAXONOMY_RE = re.compile(r"(\d{4})年(\d{1,2})月，国内光伏组件招标([\d.]+)GW")
_TENDER_ARTICLE_RE = re.compile(r"(\d{4})年(\d{1,2})月国内光伏组件招标规模达([\d.]+)GW")


def _month_end(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def _strip(html: str) -> str:
    return re.sub(r"[\u00a0\s]+", "", re.sub(r"<[^>]+>", "", html))


def _to_int(value: Any) -> int | None:
    """宽松转 int（脏值/None 不抛异常）。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ── 装机（pvmeng 转载 NEA 一览表）────────────────────────────────────
def _pvmeng_candidates(fetcher: Fetcher, limit: int = 3) -> list[str]:
    """搜索页拿候选文章：标题含「全国电力统计/一览表」的优先，其次按日期新到旧。

    坑：搜索页会返回全站最新文章（实测 2026-09-14 那篇就不是 NEA 一览表），
    所以必须「标题关键词排序 + 逐个探测摘要行」，不能直接取第一篇。
    """
    try:
        page = fetcher.get(PVMENG_SEARCH, params={"s": PVMENG_QUERY})
    except FetchError:
        return []
    pairs = re.findall(
        r'href="(https://www\.pvmeng\.com/\d{4}/\d{2}/\d{2}/\d+/)"(.{0,240}?)</a>', page, re.S
    )
    scored: list[tuple[int, str, str]] = []
    for url, inner in pairs:
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
        hit = 1 if any(k in title for k in ("全国电力统计", "一览表", "电力工业统计")) else 0
        stamp = ""
        m = _PVMENG_POST_RE.search(url)
        if m:
            stamp = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        scored.append((hit, stamp, url))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    out: list[str] = []
    for _, _, url in scored:
        if url not in out:
            out.append(url)
        if len(out) >= limit:
            break
    return out


def collect_install(conn: sqlite3.Connection, registry: Registry, fetcher: Fetcher) -> tuple[int, str]:
    """累计新增装机（GW）→ 差分得单月 → 3 个指标。"""
    candidates = _pvmeng_candidates(fetcher)
    if not candidates:
        return 0, "未发现 pvmeng 候选文章（搜索页不可用）"

    tried: list[str] = []
    for url in candidates:
        try:
            text = html_to_text(fetcher.get(url))
        except FetchError as exc:
            tried.append(f"{url}({type(exc).__name__})")
            continue
        ytd_match = _PVMENG_YTD_RE.search(text)
        if not ytd_match:
            tried.append(url)
            continue
        ytd_gw = to_float(ytd_match.group(1).replace(",", ""))
        if ytd_gw is None:
            tried.append(url)
            continue
        ytd_gw /= 100.0  # 万千瓦 → GW

        period_match = _PVMENG_PERIOD_RE.search(text)
        year = month = None
        if period_match:
            year, month = _to_int(period_match.group(1)), _to_int(period_match.group(2))
        if year is None or month is None:  # 退化：用发布日期所在月
            url_match = _PVMENG_POST_RE.search(url)
            if url_match:
                year, month = _to_int(url_match.group(1)), _to_int(url_match.group(2))
        if year is None or month is None:
            return 0, f"无法确定数据期间（公告期与发布日期均未解析）: {url}"
        obs_date = _month_end(year, month)

        written = 0
        unit_gw = (
            registry.indicators["pv_install_cn_ytd"].unit
            if "pv_install_cn_ytd" in registry.indicators
            else "GW"
        )
        store.record_obs(
            conn,
            "pv_install_cn_ytd",
            store.cap_future(obs_date),
            ytd_gw,
            unit=unit_gw,
            source=SOURCE,
            source_url=url,
            note=f"累计新增装机 {ytd_gw:.2f} GW（截至 {obs_date[:7]}）",
        )
        written += 1

        cum_match = _PVMENG_CUM_RE.search(text)
        if cum_match:
            cum_gw = to_float(cum_match.group(1).replace(",", ""))
            if cum_gw is not None:
                store.record_obs(
                    conn,
                    "pv_install_cn_cumulative",
                    store.cap_future(obs_date),
                    cum_gw / 100.0,
                    unit="GW",
                    source=SOURCE,
                    source_url=url,
                    note="太阳能发电累计装机容量",
                )
                written += 1

        # 差分：上一个「更早期间」的累计值 → 单月新增
        prev = conn.execute(
            """
            SELECT obs_date, value FROM obs
            WHERE indicator_id = 'pv_install_cn_ytd' AND obs_date < ?
            ORDER BY obs_date DESC LIMIT 1
            """,
            (obs_date,),
        ).fetchone()
        prev_value = to_float(prev["value"]) if prev else None
        if prev is not None and prev_value is not None:
            monthly = ytd_gw - prev_value
            if monthly >= 0:
                store.record_obs(
                    conn,
                    "pv_install_cn_monthly",
                    store.cap_future(obs_date),
                    monthly,
                    unit="GW",
                    source=SOURCE,
                    source_url=url,
                    note=(
                        f"单月差分 = 累计 {ytd_gw:.2f} − {prev_value:.2f}"
                        f"（{prev['obs_date'][:7]}）；NEA 从不给单月值"
                    ),
                )
                written += 1
                return written, f"装机 {monthly:.2f} GW（{obs_date[:7]}，差分）"
        return written, f"装机累计 {ytd_gw:.2f} GW，缺上一期，未能差分"

    return 0, f"候选文章均未含「今年新增装机」摘要行: {', '.join(tried[:3])}"


# ── 电池产量（国家统计局）──────────────────────────────────────────
def _latest_stats_article(fetcher: Fetcher) -> tuple[str, str] | None:
    try:
        page = fetcher.get(STATS_LIST)
    except FetchError:
        return None
    best: tuple[str, str] | None = None
    for path, title in _STATS_LINK_RE.findall(page):
        m = _STATS_TITLE_RE.search(title)
        if not m:
            continue
        year, month = _to_int(m.group(1)), _to_int(m.group(2))
        if year is None or month is None:
            continue
        url = f"https://www.stats.gov.cn/sj/zxfbhjd/{path.lstrip('/')}"
        period = _month_end(year, month)
        if best is None or period > best[0]:
            best = (period, url)
    return best


def collect_cell_output(
    conn: sqlite3.Connection, registry: Registry, fetcher: Fetcher
) -> tuple[int, str]:
    found = _latest_stats_article(fetcher)
    if not found:
        return 0, "统计局列表页未发现「规模以上工业增加值」文章"
    period, url = found
    text = re.sub(r"[\u00a0\s]+", " ", re.sub(r"<[^>]+>", " ", fetcher.get(url)))
    m = _STATS_CELL_RE.search(text)
    if not m:
        return 0, f"未匹配到「太阳能电池（光伏电池）」行: {url}"
    month_wan_kw, yoy_month, ytd_wan_kw, yoy_ytd = m.groups()
    month_gw = to_float(month_wan_kw)
    if month_gw is None:
        return 0, "电池产量解析失败"
    store.record_obs(
        conn,
        "cell_output_cn",
        store.cap_future(period),
        month_gw / 100.0,
        unit="GW",
        source=SOURCE,
        source_url=url,
        note=(
            f"统计局规上口径「太阳能电池（光伏电池）」，同比 {yoy_month}%；"
            f"累计 {ytd_wan_kw} 万千瓦（同比 {yoy_ytd}%）——含电池片与出口，≠组件产量"
        ),
    )
    return 1, f"电池产量 {month_gw / 100.0:.2f} GW（{period[:7]}，同比 {yoy_month}%）"


# ── 组件排产（DataBM 周评）──────────────────────────────────────────
def _databm_candidates(fetcher: Fetcher, limit: int = 6) -> list[str]:
    """DataBM → 候选文章：关键词搜索（正文级，最相关）优先，其次频道页周评。

    坑（均实测）：
      * 频道页 JSON-LD 只给最新 5 篇，最新那篇周评未必含排产数字；
      * 频道页锚点文字为空，靠标题筛「周评」筛不出来；
      * 泛关键词（排产/组件排产）会先返回**多晶硅**文章，因为它们正文也提排产。
    所以按「排产集中于 → 组件排产 → 排产」逐级放宽关键词，并逐个探测排产区间。
    """
    urls: list[str] = []
    for keyword in DATABM_KEYWORDS:
        try:
            page = fetcher.get(DATABM_SEARCH, params={"keyword": keyword})
        except FetchError:
            continue
        urls += re.findall(r"https://www\.databm\.com/news/\d+\.html", page)
    try:
        page = fetcher.get(DATABM_CHANNEL)
        pairs = _DATABM_ITEM_RE.findall(page) + _DATABM_NEWS_RE.findall(page)
        urls += [url for url, title in pairs if "周评" in title]
        urls += [url for url, title in pairs if "周评" not in title]
    except FetchError:
        pass

    out: list[str] = []
    for url in urls:
        if url not in out:
            out.append(url)
        if len(out) >= limit:
            break
    return out


def collect_schedule(
    conn: sqlite3.Connection, registry: Registry, fetcher: Fetcher
) -> tuple[int, str]:
    candidates = _databm_candidates(fetcher)
    if not candidates:
        return 0, "DataBM 频道页未发现候选文章"

    tried: list[str] = []
    for url in candidates:
        try:
            raw = fetcher.get(url)
        except FetchError as exc:
            tried.append(f"{url}({type(exc).__name__})")
            continue
        m = _SCHEDULE_RE.search(_strip(raw))
        month_hint: int | None = None
        if m:
            month_raw, low_raw, high_raw = m.group(1), m.group(2), m.group(3)
        else:
            alt = _SCHEDULE_ALT_RE.search(_strip(raw))
            if not alt:
                tried.append(url)
                continue
            month_raw, low_raw, high_raw = None, alt.group(1), alt.group(2)
        time_match = _DATABM_TIME_RE.search(raw)
        article_year = _to_int(time_match.group(1)) if time_match else None
        article_month = _to_int(time_match.group(2)) if time_match else None
        month = _to_int(month_raw) if month_raw is not None else (article_month or date.today().month)
        low, high = to_float(low_raw), to_float(high_raw)
        if month is None or low is None or high is None:
            tried.append(url)
            continue
        mid = (low + high) / 2
        year = article_year or date.today().year
        obs_date = _month_end(year, month)
        note_prefix = "" if month_raw is not None else "文章未写明月份，按发布时间推断；"
        store.record_obs(
            conn,
            "module_schedule_cn",
            store.cap_future(obs_date),
            mid,
            unit="GW",
            source=SOURCE,
            source_url=url,
            note=(
                f"{note_prefix}DataBM 调研区间 {low:g}-{high:g}GW，取中值；为**排产预估**；"
                "SMM 同月口径（含海外产出）约 +7GW（方向相反），不可混用"
            ),
        )
        return 1, f"组件排产 {mid:g} GW（{month}月，区间 {low:g}-{high:g}）"

    return 0, f"候选文章均未含排产区间: {', '.join(tried[:3])}"


# ── 招投标（集邦 TrendForce）────────────────────────────────────────
def collect_tender(
    conn: sqlite3.Connection, registry: Registry, fetcher: Fetcher
) -> tuple[int, str]:
    """分类页摘要一次拿多期历史（实测摘要里就含 GW 数字），再取最新正文兜底。"""
    written = 0
    latest_note = ""
    try:
        taxonomy = _strip(fetcher.get(TRENDFORCE_TENDER_TAXONOMY))
    except FetchError:
        taxonomy = ""
    for year_s, month_s, gw in _TENDER_TAXONOMY_RE.findall(taxonomy):
        value = to_float(gw)
        year, month = _to_int(year_s), _to_int(month_s)
        if value is None or year is None or month is None:
            continue
        store.record_obs(
            conn,
            "tender_volume_pv",
            store.cap_future(_month_end(year, month)),
            value,
            unit="GW",
            source=SOURCE,
            source_url=TRENDFORCE_TENDER_TAXONOMY,
            note=f"集邦光储观察：{year}年{month}月国内光伏组件招标 {value} GW（分类页摘要）",
        )
        written += 1

    # 正文兜底：A 式口径为「招标规模达 X GW」，与 B 式「中标（含入围）」严格区分
    try:
        page = fetcher.get(TRENDFORCE_TENDER_TAXONOMY)
        links = re.findall(r"https://www\.energytrend\.cn/(?:research|news)/\d{8}-\d+\.html", page)
    except FetchError:
        links = []
    for link in sorted(set(links), reverse=True)[:2]:
        try:
            text = _strip(fetcher.get(link))
        except FetchError:
            continue
        m = _TENDER_ARTICLE_RE.search(text)
        if not m:
            continue
        value = to_float(m.group(3))
        year, month = _to_int(m.group(1)), _to_int(m.group(2))
        if value is None or year is None or month is None:
            continue
        obs_date = _month_end(year, month)
        store.record_obs(
            conn,
            "tender_volume_pv",
            store.cap_future(obs_date),
            value,
            unit="GW",
            source=SOURCE,
            source_url=link,
            note=f"集邦：{year}年{month}月组件招标规模 {value} GW（只取招标口径，不含中标）",
        )
        written += 1
        latest_note = f"最新 {obs_date[:7]} 招标 {value} GW"
    return written, latest_note or f"招投标写入 {written} 期（分类页摘要）"


# ── 汇总 ───────────────────────────────────────────────────────────────
def collect(conn: sqlite3.Connection, registry: Registry, **_: Any) -> store.FetchResult:
    started = datetime.now()
    fetcher = Fetcher()
    steps: list[tuple[str, Callable[[], tuple[int, str]]]] = [
        ("装机", lambda: collect_install(conn, registry, fetcher)),
        ("电池产量", lambda: collect_cell_output(conn, registry, fetcher)),
        ("组件排产", lambda: collect_schedule(conn, registry, fetcher)),
        ("招投标", lambda: collect_tender(conn, registry, fetcher)),
    ]

    items = 0
    notes: list[str] = []
    for label, step in steps:
        try:
            n, msg = step()
        except Exception as exc:  # noqa: BLE001 - 单步失败不影响其他需求指标
            n, msg = 0, f"{type(exc).__name__}: {exc}"[:80]
        items += n
        notes.append(f"{label}:{msg}")
    conn.commit()

    status = "ok" if items else "partial"
    duration = duration_ms(started)
    return store.FetchResult(SOURCE, status, items, " | ".join(notes)[:300], duration)
