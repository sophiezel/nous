"""光伏玻璃价格采集器 — 我的钢铁(Mysteel)/隆众 正文正则抽取。

为什么不用接口: Mysteel 的每日价格行情表是 JS 渲染的空表，公开可抓的是
"综述/早间提示"正文，里面同时含报价、结算价、库存天数、冷修产能——
因此策略是「正文 + 正则 + 原文留证」。

索引不稳定，故支持三级降级:
    1) index_candidates（频道/列表页）自动发现最新光伏玻璃文章
    2) seed_urls（指标字典里登记的种子文章）
    3) --url 手工指定
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from typing import Any

from nous.research.pvglass import store
from nous.research.pvglass.http import FetchError, Fetcher, duration_ms, html_to_text, to_float
from nous.research.pvglass.registry import Registry

SOURCE = "mysteel"
_ARTICLE_RE = re.compile(r"https?://www\.mysteel\.com/oilchem/a/\d+/[A-Z0-9]+\.html")
_GLASS_HINT = ("光伏玻璃", "玻璃")

#: 指标 → 正则（第一个捕获组为数值；范围为 低-高 时取中点）
PATTERNS: dict[str, list[str]] = {
    "glass_price_2_0_single": [
        r"2\.0\s*mm\s*[（(]?\s*单镀\s*[)）]?\s*(?:面板)?[^\d]{0,14}(\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?)",
        r"2\.0\s*mm\s*镀膜\s*(?:光伏玻璃)?[^\d]{0,14}(\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?)",
    ],
    "glass_price_2_0_double": [
        r"2\.0\s*mm\s*[（(]?\s*双镀\s*[)）]?\s*(?:面板)?[^\d]{0,14}(\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?)",
    ],
    "glass_price_2_0_back": [
        r"2\.0\s*mm\s*[（(]?\s*背板\s*[)）]?[^\d]{0,14}(\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?)",
    ],
    "glass_price_3_2_single": [
        r"3\.2\s*mm\s*[（(]?\s*单镀\s*[)）]?\s*(?:面板)?[^\d]{0,14}(\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?)",
    ],
    "glass_settlement_price": [
        # 优先取「结算价」前面的数字，且中间不能跨过另一个数字：
        # 「较8月末9.5/平米的结算价格实现1元/平米的上涨」→ 9.5
        r"(\d+(?:\.\d+)?)\s*(?:元)?\s*/\s*平米[^\d\n]{0,12}?结算价",
        r"(\d+(?:\.\d+)?)\s*元/平[米方][^\d\n]{0,16}结算",
        # 兼容「结算价为 9.5 元/平米」句式
        r"结算价[^\d]{0,10}?(\d+(?:\.\d+)?)\s*元",
    ],
    "glass_inventory_days": [
        r"库存(?:天数|水平)?[^\d]{0,16}?(\d+(?:\.\d+)?)\s*天",
        r"(\d+(?:\.\d+)?)\s*天[^\n]{0,10}(?:的)?库存",
    ],
    "glass_capacity_cold_repair_ytd": [
        r"冷修[^\d]{0,24}?(\d+(?:\.\d+)?)\s*万吨",
    ],
    "glass_capacity_operating": [
        r"(?:在产|日熔量)[^\d]{0,24}?(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*万吨",
        r"在产[^\d]{0,16}?(\d+(?:\.\d+)?)\s*万吨",
    ],
}

#: 单位换算：把"万吨/日熔量"折算成"吨/日"
_TON_PER_DAY = {"glass_capacity_cold_repair_ytd", "glass_capacity_operating"}


def _midpoint(token: str) -> float | None:
    nums: list[float] = []
    for part in re.split(r"[-~至]", token):
        value = to_float(part)
        if value is not None:
            nums.append(value)
    if not nums:
        return None
    return sum(nums) / len(nums)


def extract(text: str) -> dict[str, dict[str, Any]]:
    """从正文抽取指标 → {indicator_id: {value, raw}}。"""
    found: dict[str, dict[str, Any]] = {}
    for indicator_id, patterns in PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, text)
            if not m:
                continue
            token = m.group(1)
            # glass_capacity_operating 的第二个捕获组是国内口径
            if indicator_id == "glass_capacity_operating" and m.lastindex and m.lastindex >= 2:
                token = m.group(2)
            value = _midpoint(token)
            if value is None:
                continue
            if indicator_id in _TON_PER_DAY:
                value = value * 10000.0
            found[indicator_id] = {"value": value, "raw": m.group(0).strip()[:120]}
            break
    return found


def _article_date(url: str) -> str | None:
    """URL 形如 /oilchem/a/26090409/... → 2026-09-04。"""
    m = re.search(r"/a/(\d{2})(\d{2})(\d{2})\d{2}/", url)
    if not m:
        return None
    yy, mm, dd = m.groups()
    return f"20{yy}-{mm}-{dd}"


def _age_days(url: str) -> int | None:
    day = _article_date(url)
    if not day:
        return None
    try:
        return (date.today() - date.fromisoformat(day)).days
    except ValueError:
        return None


def discover_articles(
    fetcher: Fetcher,
    registry: Registry,
    limit: int = 5,
    max_age_days: int = 45,
) -> list[str]:
    """按索引页 → 种子URL 顺序找候选文章。

    索引页（list1.mysteel.com/zhishi/*）经常返回**多年前**的文章，故:
      1) 按 URL 里的日期降序排序，剔除超过 max_age_days 的旧文
      2) 索引页全是旧文时，回退到指标字典里登记的种子URL（近期精选）
    """
    spec = registry.sources.get(SOURCE)
    fresh: list[str] = []
    for index_url in list((spec.raw.get("index_candidates") if spec else []) or []):
        try:
            page = fetcher.get(index_url)
        except FetchError:
            continue
        for link in _ARTICLE_RE.findall(page):
            if link in fresh:
                continue
            age = _age_days(link)
            if age is None or age <= max_age_days:
                fresh.append(link)
    # 新到旧
    fresh.sort(key=lambda u: _article_date(u) or "", reverse=True)
    if fresh:
        return fresh[:limit]

    seeds = list(spec.raw.get("seed_urls") or []) if spec else []
    seeds = [u for u in seeds if (lambda a: a is None or a <= 400)(_age_days(u))]
    seeds.sort(key=lambda u: _article_date(u) or "", reverse=True)
    return seeds[:limit]


def collect(
    conn: sqlite3.Connection,
    registry: Registry,
    *,
    urls: list[str] | None = None,
    limit: int = 5,
    max_age_days: int = 45,
    **_: Any,
) -> store.FetchResult:
    started = datetime.now()
    fetcher = Fetcher()
    targets = urls or discover_articles(
        fetcher, registry, limit=limit, max_age_days=max_age_days
    )
    if not targets:
        return store.FetchResult(
            SOURCE,
            "error",
            0,
            "未发现候选文章（索引页只有旧文，请用 --url 指定或补 config 里的 seed_urls）",
            0,
        )

    written = 0
    failures: list[str] = []
    for url in targets:
        try:
            page = fetcher.get(url)
        except FetchError as exc:
            failures.append(str(exc)[:60])
            continue
        text = html_to_text(page)
        found = extract(text)
        obs_date = _article_date(url) or date.today().isoformat()
        for indicator_id, payload in found.items():
            unit = registry.indicators[indicator_id].unit if indicator_id in registry.indicators else ""
            store.record_obs(
                conn,
                indicator_id,
                obs_date,
                payload["value"],
                unit=unit,
                source=SOURCE,
                source_url=url,
                note=payload["raw"],
            )
            written += 1
    conn.commit()

    duration = duration_ms(started)
    status = "ok" if written else ("partial" if targets else "error")
    message = f"扫描{len(targets)}篇，写入{written}条" + (f"，失败{len(failures)}" if failures else "")
    return store.FetchResult(SOURCE, status, written, message, duration)
