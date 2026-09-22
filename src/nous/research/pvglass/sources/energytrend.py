"""集邦新能源网(TrendForce)采集器 — 产业链周度价格/库存 + 玻璃产业事件。

自动索引: https://www.energytrend.cn/pricequotes/ 列出最新"X.X光伏价格"文章。
正文含硅料/硅片/电池/组件成交价、库存与"有价无市/减产"判断——是判断
反内卷是否从口号变成成交的最佳免费来源。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from typing import Any

from nous.research.pvglass import store
from nous.research.pvglass.http import FetchError, Fetcher, duration_ms, html_to_text, to_float
from nous.research.pvglass.registry import Registry

SOURCE = "energytrend"
DEFAULT_INDEX = "https://www.energytrend.cn/pricequotes/"
_NEWS_SEARCH = "https://www.energytrend.cn/?s=光伏玻璃"

PRICE_PATTERNS: dict[str, list[str]] = {
    "polysilicon_inventory": [
        r"硅料库存[^\d]{0,24}?(\d+(?:\.\d+)?)\s*万吨",
        r"库存(?:维持|仍)?\s*(\d+(?:\.\d+)?)\s*万吨以上",
    ],
    "wafer_inventory": [
        r"硅片[^\d]{0,40}?库存约?\s*(\d+(?:\.\d+)?)\s*GW",
        r"库存约\s*(\d+(?:\.\d+)?)\s*GW",
    ],
    "polysilicon_price": [
        r"(\d+(?:\.\d+)?)\s*万元/吨",
        r"(\d+(?:\.\d+)?)\s*元/千克",
        r"(\d+(?:\.\d+)?)\s*元/kg",
    ],
    "wafer_price_182": [
        r"183[、,，]\s*210R\s*[、,，]\s*210\s*成交均价约?\s*(\d+(?:\.\d+)?)",
        r"183[^元]{0,20}?成交均价约?\s*(\d+(?:\.\d+)?)",
    ],
    "cell_price_182": [
        r"183[^元]{0,28}?成交价格已?接近\s*(\d+(?:\.\d+)?)\s*元/W",
        r"183[^元]{0,28}?(\d\.\d+)\s*元/W",
    ],
    "module_price_topcon": [
        r"TOPCon\s*报价约?\s*(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*元/W",
        r"TOPCon[^\d]{0,16}?(\d\.\d+)\s*元/W",
    ],
}

#: 供给侧事件关键词 → 事件类指标
EVENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("glass_capacity_restart", ("点火", "复产", "投产")),
    ("glass_capacity_cold_repair_ytd", ("冷修", "堵窑", "停窑", "减产")),
    ("polysilicon_platform", ("收储", "平台公司", "产能整合")),
    ("policy_low_price_rule", ("反内卷", "成本核算", "低价无序竞争")),
    ("policy_mandatory_standard", ("强制性国家标准", "能耗限额", "强标")),
)


def discover_latest_article(fetcher: Fetcher, index_url: str = DEFAULT_INDEX) -> str | None:
    try:
        page = fetcher.get(index_url)
    except FetchError:
        return None
    links = re.findall(r"https://www\.energytrend\.cn/pricequotes/\d{8}-\d+\.html", page)
    if not links:
        return None
    return sorted(set(links), reverse=True)[0]


def extract(text: str) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for indicator_id, patterns in PRICE_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, text)
            if not m:
                continue
            groups = [g for g in m.groups() if g]
            value = to_float(groups[0])
            if value is None:
                continue
            if indicator_id == "module_price_topcon" and len(groups) >= 2:
                high = to_float(groups[1])
                if high is not None:
                    value = (value + high) / 2
            found[indicator_id] = {"value": value, "raw": m.group(0).strip()[:120]}
            break
    return found


def collect_events(fetcher: Fetcher) -> list[dict[str, str]]:
    """新闻检索页 → 供给侧/政策事件（文本型观测）。"""
    try:
        page = fetcher.get(_NEWS_SEARCH)
    except FetchError:
        return []
    items = re.findall(
        r'href="(https://www\.energytrend\.cn/news/\d{8}-\d+\.html)"[^>]*>\s*([^<]{6,90})',
        page,
    )
    events: list[dict[str, str]] = []
    seen: set[str] = set()
    for url, title in items:
        title = re.sub(r"\s+", " ", title).strip()
        if url in seen or not title:
            continue
        seen.add(url)
        for indicator_id, keywords in EVENT_RULES:
            if any(k in title for k in keywords):
                events.append({"indicator_id": indicator_id, "title": title, "url": url})
                break
    return events


def collect(conn: sqlite3.Connection, registry: Registry, **_: Any) -> store.FetchResult:
    started = datetime.now()
    fetcher = Fetcher()
    spec = registry.sources.get(SOURCE)
    index_url = (spec.raw.get("index") if spec else None) or DEFAULT_INDEX

    written = 0
    article = discover_latest_article(fetcher, index_url)
    obs_date = date.today().isoformat()
    if article:
        try:
            text = html_to_text(fetcher.get(article))
        except FetchError:
            text = ""
        found = extract(text)
        m = re.search(r"/pricequotes/(\d{4})(\d{2})(\d{2})-", article)
        if m:
            obs_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        for indicator_id, payload in found.items():
            unit = registry.indicators[indicator_id].unit if indicator_id in registry.indicators else ""
            store.record_obs(
                conn,
                indicator_id,
                obs_date,
                payload["value"],
                unit=unit,
                source=SOURCE,
                source_url=article,
                note=payload["raw"],
            )
            written += 1

    events = collect_events(fetcher)
    for ev in events:
        store.record_obs(
            conn,
            ev["indicator_id"],
            obs_date,
            None,
            source=SOURCE,
            source_url=ev["url"],
            confidence="event",
            value_text=ev["title"],
            note="事件线索（正文需人工/LLM确认）",
        )
    conn.commit()

    duration = duration_ms(started)
    status = "ok" if written else ("partial" if events else "error")
    message = f"价格{written}条，事件{len(events)}条" + (f"，文章 {article}" if article else "，未发现价格文章")
    return store.FetchResult(SOURCE, status, written + len(events), message, duration)
