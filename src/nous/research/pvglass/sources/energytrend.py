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

#: 量纲合理区间（下限, 上限）。抽错宁可跳过 —— 与 report_pdf 的 sanity 同一原则。
#: 越界说明正则吃到的是涨跌幅/单价单位不同的数字，而不是该指标的价格。
PRICE_RANGES: dict[str, tuple[float, float]] = {
    "polysilicon_inventory": (5.0, 200.0),  # 万吨
    "wafer_inventory": (1.0, 200.0),  # GW
    "polysilicon_price": (20.0, 500.0),  # 元/kg
    "wafer_price_182": (0.4, 5.0),  # 元/片
    "cell_price_182": (0.15, 2.0),  # 元/W
    "module_price_topcon": (0.4, 3.0),  # 元/W
}

#: 产品词。同一句里“组件/电池/硅片”的价格量级差很多，命中错词就等于取错价。
PRODUCT_NOUNS: tuple[str, ...] = ("组件", "电池", "硅片")

#: 各指标**不允许**出现在数字之前的最近产品词（串味则跳过）。
CONFLICT_NOUNS: dict[str, tuple[str, ...]] = {
    "wafer_price_182": ("组件", "电池"),
    "cell_price_182": ("组件", "硅片"),
    "module_price_topcon": ("电池", "硅片"),
}

#: 一个指标在同一篇文章里可能出现多个同类价（如硅料：致密料/颗粒硅/混包料）。
#: 指标名写了“致密料”，就得优先取致密料，而不是“正文里第一个 X 元/kg”。
PREFERRED_NOUNS: dict[str, tuple[str, ...]] = {
    "polysilicon_price": ("致密料",),
}

#: 数字前面是“涨跌幅/差值”语境 —— 这类句子里的数字是变化量，不是价格。
#: 实测坑：“价格较上周继续降低0.01元/W”“拍价相较主流水平低0.005元/W”。
#:
#: 注意 **不允许**动词后面跟“至/到/为”：那是**目标价位**
#:（“致密料报价已上调至40元/kg”里的 40 就是价格）。早期把 `至` 写成可选尾巴，
#: 导致致密料价位被判为变化量、跌落到同一句里的**颗粒硅**价（40→38、43→41 两处真实回归）。
_DELTA_TAIL_RE = re.compile(
    r"(?:较|比)[^，。；\n]{0,12}?(?:上周|上期|上月|前周|前期|主流水平|主流价)"
    r"|环比|同比"
    r"|(?:降低|下降|下跌|上涨|上调|下调|减少|增加|提升|回落|松动|降|涨|跌)[了约]?\s*$"
)

#: 句边界。回搜产品词必须限制在本句内，否则会把上一句的“电池片”当成
#: 本句“TOPCon报价”的归属词，从而误杀干净样本（实测过）。
_CLAUSE_BOUNDARY = "。；;！？\n"


def _clause_start(text: str, pos: int) -> int:
    """pos 所在句子的起始下标（逗号不算句界）。"""
    return max(text.rfind(ch, 0, pos) for ch in _CLAUSE_BOUNDARY) + 1


def _looks_like_delta(text: str, pos: int, window: int = 28) -> bool:
    """数字前一小段是否处于“变化量”语境。"""
    return bool(_DELTA_TAIL_RE.search(text[max(0, pos - window) : pos]))


def _nearest_product_noun(text: str, pos: int) -> str | None:
    """数字之前**本句内**最近的产品词（决定这个数字到底是谁的价格）。"""
    start = _clause_start(text, pos)
    best: tuple[int, str] | None = None
    for noun in PRODUCT_NOUNS:
        at = text.rfind(noun, start, pos)
        if at < 0:
            continue
        if best is None or at > best[0]:
            best = (at, noun)
    return best[1] if best else None

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
    """正文 → 链价指标。

    每层都要过四道闸，任一不过就跳过这个匹配、继续找下一个：

    1. **不是涨跌幅** —— “较上周继续降低0.01元/W”里的 0.01 是变化量，
       但“上调至40元/kg”里的 40 是目标价位，不能当变化量误杀；
    2. **产品词不串味** —— 数字前**本句内**最近的产品词不能是别的环节（组件≠电池≠硅片）；
    3. **量纲在区间内** —— 价格 / 库存的合理上下限，防单位错位与脏值；
    4. **优先取指标名指的那个价** —— 如“致密料”优先于同句的颗粒硅/混包料。

    宁可跳过、不猜：过不了闸的正文就不产出观测，
    与 ``report_pdf`` 的 sanity 校验同一原则。
    """
    found: dict[str, dict[str, Any]] = {}
    for indicator_id, patterns in PRICE_PATTERNS.items():
        rng = PRICE_RANGES.get(indicator_id)
        conflicts = CONFLICT_NOUNS.get(indicator_id, ())
        preferred = PREFERRED_NOUNS.get(indicator_id, ())
        # 汇总**所有**过闸候选，再按“指名词”优先级挑一个；
        # 不提前 break：否则“先出现的颗粒硅”会遮蔽后面的“致密料”。
        candidates: list[tuple[float, str, bool]] = []
        for pattern in patterns:
            for m in re.finditer(pattern, text):
                groups = [g for g in m.groups() if g]
                value = to_float(groups[0])
                if value is None:
                    continue
                if indicator_id == "module_price_topcon" and len(groups) >= 2:
                    high_v = to_float(groups[1])
                    if high_v is not None:
                        value = (value + high_v) / 2
                at = m.start(1) if m.group(1) is not None else m.start()
                if _looks_like_delta(text, at):
                    continue
                if _nearest_product_noun(text, at) in conflicts:
                    continue
                if rng is not None and not rng[0] <= value <= rng[1]:
                    continue
                clause = text[_clause_start(text, at) : at]
                candidates.append(
                    (value, m.group(0).strip()[:120], any(n in clause for n in preferred))
                )
        if not candidates:
            continue
        # 有指名词的命中优先；否则回退为“第一个 pattern 的第一个匹配”（保持原优先级）
        pick = next((c for c in candidates if c[2]), candidates[0])
        found[indicator_id] = {"value": pick[0], "raw": pick[1]}
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
