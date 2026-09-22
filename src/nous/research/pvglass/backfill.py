"""历史回填 — 遍历可枚举的归档页批量重放，让回测有真样本。

与 fetch 的区别
---------------
    fetch    增量：只取最新一期（源 = 具体采集器）
    backfill 重放：遍历归档页逐期写库（source='backfill'，与增量源**互不覆盖**，
             因为 obs 主键是 (indicator_id, obs_date, source)）

可回填 / 不可回填（全部实测，见 docs/research/pvglass-backfill-sources.md）
------------------------------------------------------------------------
    ✅ 招标      集邦 taxonomy/term/5334，1 次请求 → 12-13 个月
    ✅ 产业链价格 7 个 taxonomy term，逐篇跑现成 energytrend.extract() → 12-14 个月周度
    ✅ 装机      光动百科月归档 + 搜索页 → 逐帖 YTD → 差分 → 18-24 个月单月
    ✅ 电池产量   统计局 index_N.html 逐页 → ≥18 个月
    ❌ 玻璃2.0mm价格 四家源全在付费墙/JS 后（Mysteel 无翻页、卓创404、SMM 登录墙）
                 → 只能从本期开始向前累积；backtest 会显式标注该腿样本为 0

工程约定：普通站点限速（默认 0.6s/请求）、失败不中断、任何写入都带 source=backfill。
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from nous.research.pvglass import store
from nous.research.pvglass.http import FetchError, Fetcher, duration_ms, html_to_text
from nous.research.pvglass.registry import Registry
from nous.research.pvglass.sources import demand, energytrend

SOURCE = "backfill"
PVMENG_ARCHIVE = "https://www.pvmeng.com/{year:04d}/{month:02d}/"
STATS_LIST = "https://www.stats.gov.cn/sj/zxfbhjd/index_{page}.html"
STATS_FIRST = "https://www.stats.gov.cn/sj/zxfbhjd/"

#: 集邦价格归档 taxonomy term（实测各类 21-22 篇，约 12-14 个月周度）
TRENDFORCE_PRICE_TERMS: dict[int, str] = {
    1831: "硅料",
    11304: "电池片价格",
    5455: "硅片价格",
    10809: "光伏组件价格",
}

_PVMENG_ANY_POST = re.compile(r"https://www\.pvmeng\.com/\d{4}/\d{2}/\d{2}/\d+/")
# 归档页里 66 个锚点大多是导航链接（能源导航/专题…），必须按**锚文本**过滤：
#   风光装机总量：16.9624亿千瓦||国家能源局发布2025年1-8月份全国电力工业统计数据20250926
_PVMENG_ARCHIVE_PAIRS = re.compile(
    r'href="(https://www\.pvmeng\.com/\d{4}/\d{2}/\d{2}/\d+/)"[^>]*>(.{0,220}?)</a>', re.S
)
_PVMENG_ANCHOR_HIT = re.compile(r"全国电力(?:工业)?统计数据|风光装机总量")
_ET_ARTICLE = re.compile(r"https://www\.energytrend\.cn/(?:pricequotes|news|research)/\d{8}-\d+\.html")


@dataclass
class Item:
    indicator_id: str
    obs_date: str
    value: float
    unit: str
    note: str


@dataclass
class BackfillResult:
    source: str
    written: int = 0
    requests: int = 0
    status: str = "ok"
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.notes.append(text)


class Limiter:
    """普通站点限速：两次请求之间至少间隔 sleep 秒。"""

    def __init__(self, fetcher: Fetcher, sleep: float = 0.6) -> None:
        self.fetcher = fetcher
        self.sleep = sleep
        self.count = 0
        self._last = 0.0

    def get(self, url: str, **kwargs: Any) -> str:
        wait = self.sleep - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.count += 1
        return self.fetcher.get(url, **kwargs)


# ── 装机：月归档枚举 → YTD → 差分 ──────────────────────────────────────
def month_archive_urls(months: int, *, today: date | None = None) -> list[str]:
    """最近 N 个月的归档页 URL（新到旧）。"""
    today = today or date.today()
    year, month = today.year, today.month
    out: list[str] = []
    for _ in range(max(months, 1)):
        out.append(PVMENG_ARCHIVE.format(year=year, month=month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out


def _pvmeng_archive_posts(limiter: Limiter, months: int) -> list[str]:
    """月归档里按**锚文本**筛 NEA 统计帖（按月天然可分，用于补搜索页召回不足）。"""
    found: list[str] = []
    for url in month_archive_urls(months):
        try:
            page = limiter.get(url)
        except FetchError:
            continue
        for link, inner in _PVMENG_ARCHIVE_PAIRS.findall(page):
            text = re.sub(r"<[^>]+>", "", inner)
            if _PVMENG_ANCHOR_HIT.search(text) and link not in found:
                found.append(link)
    return found


def _post_date(url: str) -> str | None:
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/\d+/?$", url)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def period_lag(post_date: str | None, period_end: str, *, max_lag_days: int = 60) -> int | None:
    """发布日相对期末的滞后天数；不合法返回 None。

    这是防止「数值配错期间」的关键校验：导航页/旧文里的历史数字一旦被当成当期值，
    就会造出「2025-11 与 2026-06 同为 277.17」这种假数据（实测踩过）。
    """
    if not post_date:
        return None
    try:
        posted = date.fromisoformat(post_date)
        period = date.fromisoformat(period_end)
    except ValueError:
        return None
    lag = (posted - period).days
    return lag if 0 <= lag <= max_lag_days else None


def period_pair_ok(post_date: str | None, period_end: str, *, max_lag_days: int = 60) -> bool:
    return period_lag(post_date, period_end, max_lag_days=max_lag_days) is not None


def _months_between(prev_date: str, obs_date: str) -> int:
    """两个期末之间相隔的月数（用于标注「中间期缺失」的合并差分）。"""
    try:
        py, pm = int(prev_date[:4]), int(prev_date[5:7])
        oy, om = int(obs_date[:4]), int(obs_date[5:7])
    except ValueError:
        return 1
    return max((oy * 12 + om) - (py * 12 + pm), 1)


def diff_ytd_detailed(
    rows: Iterable[tuple[str, float]],
) -> tuple[list[Item], list[str]]:
    """相邻两期累计值差分 → 单月新增，并返回被跳过的原因。

    特判：
      * 跨年：上一期是上一年 12 月（全年累计），差值 = 新一年 1-2 月合计
        （NEA 惯例 1-2 月合发一期）
      * 跨年重置：上年最后一期非 12 月时跨年差值无意义 → 跳过该期，但不牵连下一期
      * 累计回落：口径修订/数据错配 → 该期**与下一期**都不输出（宁可空，不可错），
        并在 notes 里说明原因
    """
    ordered = sorted(rows)
    out: list[Item] = []
    notes: list[str] = []
    skip_next = False
    for (prev_date, prev_value), (obs_date, value) in zip(ordered, ordered[1:]):
        # 跨年要先判：NEA 的累计值每年 1 月从零重新计数，
        #   * 上一期是上年 12 月 → 差值 = 新一年 1-2 月合计（**有效**）
        #   * 上一期不是 12 月（中间缺月）→ 跨年差值无意义，跳过但**不牵连下一期**
        if prev_date[:4] != obs_date[:4]:
            if prev_date[5:7] == "12":
                pass  # 走下面的正常差分，note 会标注 1-2 月合计
            else:
                notes.append(
                    f"{obs_date[:7]}：跨年重置（上年最后一期为 {prev_date[:7]}，非 12 月），"
                    "跨年差值无意义，已跳过（不影响新年度内部差分）"
                )
                continue
        delta = value - prev_value
        if delta < 0:
            notes.append(
                f"{obs_date[:7]}：累计回落（{prev_value:.2f}→{value:.2f}），"
                "疑似修订/口径变更，该期与下一期差分均跳过"
            )
            skip_next = True
            continue
        if skip_next:
            notes.append(f"{obs_date[:7]}：上一期累计回落，本差可能失真，已跳过")
            skip_next = False
            continue
        cross_year = prev_date[:4] != obs_date[:4]
        span = _months_between(prev_date, obs_date)
        note = (
            f"单月差分 = 累计 {value:.2f} − {prev_value:.2f}（{prev_date[:7]}）"
            + ("；**跨年（上年 12 月 → 新一年），含 1-2 月合计期**" if cross_year else "")
            + (f"；⚠️ 跨 {span} 个月（中间期缺失，非单月值）" if span > 1 else "")
        )
        out.append(Item("pv_install_cn_monthly", obs_date, round(delta, 2), "GW", note))
    return out, notes


def diff_ytd(rows: Iterable[tuple[str, float]]) -> list[Item]:
    """仅要结果时的便捷包装（原因见 diff_ytd_detailed）。"""
    return diff_ytd_detailed(rows)[0]


def backfill_install(
    conn: sqlite3.Connection, registry: Registry, limiter: Limiter, *, months: int = 18
) -> BackfillResult:
    res = BackfillResult("install")
    candidates = demand._pvmeng_candidates(limiter.fetcher, limit=6)
    for url in _pvmeng_archive_posts(limiter, months):
        if url not in candidates:
            candidates.append(url)

    # 期间 → (发布滞后, url, YTD/GW)：同一期间多帖时保留发布最贴近期末的那篇
    best: dict[str, tuple[int, str, float]] = {}
    for url in candidates:
        try:
            text = html_to_text(limiter.get(url))
        except FetchError:
            continue
        ytd_match = demand._PVMENG_YTD_RE.search(text)
        period = demand._PVMENG_PERIOD_RE.search(text)
        # 必须同时有「一览表(截至X年M月)」标题——导航页/旧文没有，直接丢
        if not ytd_match or not period:
            continue
        value = demand.to_float(ytd_match.group(1).replace(",", ""))
        year, month = demand._to_int(period.group(1)), demand._to_int(period.group(2))
        if value is None or year is None or month is None:
            continue
        obs_date = demand._month_end(year, month)
        lag = period_lag(_post_date(url), obs_date)
        if lag is None:
            continue
        if obs_date not in best or lag < best[obs_date][0]:
            best[obs_date] = (lag, url, value / 100.0)

    ytd: list[tuple[str, float]] = []
    for obs_date, (_, url, ytd_gw) in sorted(best.items()):
        ytd.append((obs_date, ytd_gw))
        store.record_obs(
            conn,
            "pv_install_cn_ytd",
            store.cap_future(obs_date),
            ytd_gw,
            unit="GW",
            source=SOURCE,
            source_url=url,
            note=f"回填：累计新增装机（截至 {obs_date[:7]}）",
        )
        res.written += 1

    if not ytd:
        res.status = "partial"
        res.note("未抓到有效累计值（归档召回不稳，可加大 --months）")
        conn.commit()
        return res

    # 已有库内 YTD（含 seed 的 H1 基数）一起参与差分，保证序列连续
    rows: dict[str, float] = {d: v for d, v in ytd}
    for row in store.all_obs(conn, "pv_install_cn_ytd"):
        if str(row["source"]) == SOURCE:
            continue  # 回填自己写的别重复并入（best 已按期间去重）
        value = demand.to_float(row["value"])
        if value is not None:
            rows.setdefault(str(row["obs_date"]), value)

    monthly, diff_notes = diff_ytd_detailed(rows.items())
    for note in diff_notes:
        res.note(note)
    for item in monthly:
        store.record_obs(
            conn,
            item.indicator_id,
            store.cap_future(item.obs_date),
            item.value,
            unit=item.unit,
            source=SOURCE,
            note=item.note,
        )
        res.written += 1
    res.note(f"YTD {len(rows)} 期 → 单月 {len(monthly)} 条")
    conn.commit()
    return res
    return res


# ── 电池产量：统计局 index_N 翻页 ──────────────────────────────────────
def backfill_stats(
    conn: sqlite3.Connection, registry: Registry, limiter: Limiter, *, pages: int = 18
) -> BackfillResult:
    res = BackfillResult("stats")
    seen_periods: set[str] = set()
    for page_no in range(1, max(pages, 1) + 1):
        url = STATS_FIRST if page_no == 1 else STATS_LIST.format(page=page_no)
        try:
            page = limiter.get(url)
        except FetchError:
            continue
        targets: list[tuple[str, str]] = []
        for path, title in demand._STATS_LINK_RE.findall(page):
            m = demand._STATS_TITLE_RE.search(title)
            if not m:
                continue
            year, month = demand._to_int(m.group(1)), demand._to_int(m.group(2))
            if year is None or month is None:
                continue
            period = demand._month_end(year, month)
            if period in seen_periods:
                continue
            seen_periods.add(period)
            targets.append((period, f"https://www.stats.gov.cn/sj/zxfbhjd/{path.lstrip('/')}"))
        for period, article in targets[:1]:  # 同一发布有多个链接变体，取一个
            try:
                text = re.sub(
                    r"[\u00a0\s]+", " ", re.sub(r"<[^>]+>", " ", limiter.get(article))
                )
            except FetchError:
                continue
            m = demand._STATS_CELL_RE.search(text)
            if not m:
                continue
            value = demand.to_float(m.group(1))
            if value is None:
                continue
            store.record_obs(
                conn,
                "cell_output_cn",
                store.cap_future(period),
                value / 100.0,
                unit="GW",
                source=SOURCE,
                source_url=article,
                note=f"回填：统计局规上「太阳能电池（光伏电池）」单月，同比 {m.group(2)}%",
            )
            res.written += 1
    res.note(f"覆盖 {len(seen_periods)} 个月份页")
    conn.commit()
    return res


# ── 招标：1 次 taxonomy + 正文兜底 ──────────────────────────────────────
def backfill_tender(
    conn: sqlite3.Connection, registry: Registry, limiter: Limiter, *, limit: int = 8
) -> BackfillResult:
    res = BackfillResult("tender")
    try:
        taxonomy = demand._strip(limiter.get(demand.TRENDFORCE_TENDER_TAXONOMY))
    except FetchError:
        res.status = "error"
        res.note("taxonomy 不可达")
        return res

    for year_s, month_s, gw in demand._TENDER_TAXONOMY_RE.findall(taxonomy):
        year, month, value = demand._to_int(year_s), demand._to_int(month_s), demand.to_float(gw)
        if value is None or year is None or month is None:
            continue
        store.record_obs(
            conn,
            "tender_volume_pv",
            demand._month_end(year, month),
            value,
            unit="GW",
            source=SOURCE,
            source_url=demand.TRENDFORCE_TENDER_TAXONOMY,
            note=f"回填：集邦光储观察 {year}年{month}月国内组件招标 {value} GW（分类页摘要）",
        )
        res.written += 1

    try:
        page = limiter.get(demand.TRENDFORCE_TENDER_TAXONOMY)
        links = sorted(set(re.findall(r"https://www\.energytrend\.cn/(?:research|news)/\d{8}-\d+\.html", page)))
    except FetchError:
        links = []
    for link in list(reversed(links))[:limit]:
        try:
            text = demand._strip(limiter.get(link))
        except FetchError:
            continue
        m = demand._TENDER_ARTICLE_RE.search(text)
        if not m:
            continue
        year, month, value = demand._to_int(m.group(1)), demand._to_int(m.group(2)), demand.to_float(m.group(3))
        if value is None or year is None or month is None:
            continue
        store.record_obs(
            conn,
            "tender_volume_pv",
            demand._month_end(year, month),
            value,
            unit="GW",
            source=SOURCE,
            source_url=link,
            note=f"回填：集邦 {year}年{month}月招标规模 {value} GW（正文兜底）",
        )
        res.written += 1
    res.note(f"{res.written} 期招标")
    conn.commit()
    return res


# ── 产业链价格：集邦 taxonomy 逐篇（复用 energytrend.extract）──────────
def backfill_prices(
    conn: sqlite3.Connection, registry: Registry, limiter: Limiter, *, limit: int = 8
) -> BackfillResult:
    res = BackfillResult("prices")
    for term, label in TRENDFORCE_PRICE_TERMS.items():
        url = f"https://www.energytrend.cn/taxonomy/term/{term}/"
        try:
            page = limiter.get(url)
        except FetchError:
            res.note(f"{label}: taxonomy 不可达")
            continue
        links = sorted(set(_ET_ARTICLE.findall(page)), reverse=True)[:limit]
        hit = 0
        for link in links:
            try:
                text = html_to_text(limiter.get(link))
            except FetchError:
                continue
            found = energytrend.extract(text)
            if not found:
                continue
            m = re.search(r"/(?:pricequotes|news|research)/(\d{4})(\d{2})(\d{2})-", link)
            obs_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else date.today().isoformat()
            for indicator_id, payload in found.items():
                unit = registry.indicators[indicator_id].unit if indicator_id in registry.indicators else ""
                store.record_obs(
                    conn,
                    indicator_id,
                    store.cap_future(obs_date),
                    payload["value"],
                    unit=unit,
                    source=SOURCE,
                    source_url=link,
                    note=f"回填（{label}）：{payload['raw'][:80]}",
                )
                res.written += 1
                hit += 1
        res.note(f"{label}: {hit} 条")
    conn.commit()
    return res


# ── 汇总 ───────────────────────────────────────────────────────────────
HANDLERS = {
    "install": backfill_install,
    "stats": backfill_stats,
    "tender": backfill_tender,
    "prices": backfill_prices,
}


#: reset 时各来源要清掉的指标（source=backfill 的行）
RESET_INDICATORS: dict[str, list[str]] = {
    "install": ["pv_install_cn_ytd", "pv_install_cn_monthly"],
    "stats": ["cell_output_cn"],
    "tender": ["tender_volume_pv"],
    "prices": [
        "polysilicon_price",
        "polysilicon_inventory",
        "wafer_price_182",
        "wafer_inventory",
        "cell_price_182",
        "module_price_topcon",
    ],
}


def reset(conn: sqlite3.Connection, sources: Iterable[str]) -> int:
    """清掉这些来源此前回填的行（source=backfill），便于重跑修正过的解析器。"""
    deleted = 0
    for name in sources:
        for indicator_id in RESET_INDICATORS.get(name, []):
            cur = conn.execute(
                "DELETE FROM obs WHERE indicator_id = ? AND source = ?",
                (indicator_id, SOURCE),
            )
            deleted += cur.rowcount
    conn.commit()
    return deleted


def run(
    conn: sqlite3.Connection,
    registry: Registry,
    *,
    sources: list[str] | None = None,
    sleep: float = 0.6,
    months: int = 18,
    pages: int = 18,
    limit: int = 8,
    dry_run: bool = False,
    reset_first: bool = False,
) -> list[BackfillResult]:
    """执行回填。dry_run 时只枚举不写库（用于先看能拿到多少）。"""
    import nous.research.pvglass.http as _http

    if dry_run:
        return [
            BackfillResult(name, 0, 0, "skipped", ["dry-run：未写库"])
            for name in (sources or list(HANDLERS))
        ]

    targets = list(sources or list(HANDLERS))
    if reset_first and not dry_run:
        reset(conn, targets)

    fetcher = _http.Fetcher(timeout=30)
    limiter = Limiter(fetcher, sleep=sleep)
    started = datetime.now()
    results: list[BackfillResult] = []
    for name in targets:
        handler = HANDLERS.get(name)
        if handler is None:
            results.append(BackfillResult(name, status="skipped", notes=[f"未知来源 {name}"]))
            continue
        kwargs: dict[str, Any] = {}
        if name == "install":
            kwargs["months"] = months
        elif name == "stats":
            kwargs["pages"] = pages
        elif name in ("tender", "prices"):
            kwargs["limit"] = limit
        try:
            res = handler(conn, registry, limiter, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 单个来源失败不影响其他
            res = BackfillResult(name, status="error", notes=[f"{type(exc).__name__}: {exc}"[:160]])
        res.requests = limiter.count
        results.append(res)
    _ = duration_ms(started)
    return results
