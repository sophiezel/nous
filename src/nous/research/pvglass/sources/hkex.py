"""港交所披露易采集器 — 公告/盈警/中报/回购。

关键点:
    * titleSearchServlet 需要先访问 titlesearch.xhtml 拿 cookie，否则返回空
    * stockId 不是股票代码，需要用 prefix.do 反查（如 00968 → 97365）
    * 返回 JSON 里 result 是"字符串形式的 JSON"，需二次 loads
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from nous.research.pvglass import store
from nous.research.pvglass.http import FetchError, Fetcher, duration_ms
from nous.research.pvglass.registry import Registry

SOURCE = "hkex"
BASE = "https://www1.hkexnews.hk"

#: 跟踪的上市公司
STOCKS: dict[str, str] = {
    "00968": "信义光能",
    "03868": "信义能源",
    "06865": "福莱特玻璃",
}

#: 公告分类规则（顺序敏感，先匹配先归类）
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("profit_warning", ("盈利警告", "盈利預警", "盈警", "profit warning")),
    ("positive_alert", ("正面盈利預告", "positive profit alert")),
    # 注意：「半年度業績」包含「年度業績」子串，必须先归到中期，否则会被误判为年报
    ("interim_results", ("中期業績", "中期业绩", "半年度業績", "半年度业绩", "中期報告", "半年度報告")),
    ("annual_results", ("年度業績", "年度业绩", "年度報告", "年報")),
    ("buyback", ("股份購回", "股份购回", "shares repurchase", "repurchase")),
    ("monthly_return", ("月報表", "月报表", "monthly return")),
    ("inside_info", ("內幕消息", "内幕消息", "inside information")),
    ("circular", ("通函", "circular")),
)

_BUYBACK_HINT = ("股份購回", "股份购回", "repurchase")

#: 真正的「业绩报表」标题（抽取 PDF 只认这些）
REPORT_TITLE_RE = re.compile(r"(中期業績公告|年度業績公告|中期報告|年度報告|半年度報告|年報|interim results|annual results)")
#: 这些标题即使被归入报表分类也不是报表本体，必须排除
NON_REPORT_TITLE_RE = re.compile(r"(說明會|说明会|通知|通函|摘要|月報表|月报表)")


def classify(title: str, long_text: str = "") -> str:
    blob = f"{title} {long_text}".lower()
    for category, keywords in CATEGORY_RULES:
        if any(k.lower() in blob for k in keywords):
            return category
    return "other"


def resolve_stock_id(fetcher: Fetcher, code: str) -> int | None:
    """代码 → 披露易内部 stockId。"""
    url = f"{BASE}/search/prefix.do"
    try:
        raw = fetcher.get(
            url,
            params={"callback": "cb", "lang": "ZH", "type": "A", "name": code, "market": "SEHK"},
        )
    except FetchError:
        return None
    m = re.search(r"\((.*)\)\s*;?\s*$", raw.strip(), re.S)
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    for item in payload.get("stockInfo") or []:
        if str(item.get("code")) == code:
            try:
                return int(item["stockId"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def ensure_session(fetcher: Fetcher) -> None:
    """拿 cookie（缺失会导致 titleSearchServlet 返回空列表）。"""
    try:
        fetcher.get(f"{BASE}/search/titlesearch.xhtml?lang=zh")
    except FetchError:
        pass


def fetch_announcements(
    fetcher: Fetcher,
    code: str,
    *,
    days: int = 180,
    stock_id: int | None = None,
) -> list[dict[str, Any]]:
    """抓取单只个股的公告列表。"""
    sid = stock_id or resolve_stock_id(fetcher, code)
    if sid is None:
        return []
    today = date.today()
    params = {
        "sortDir": "0",
        "sortByOptions": "DateTime",
        "category": "0",
        "market": "SEHK",
        "stockId": str(sid),
        "documentType": "-1",
        "fromDate": (today - timedelta(days=days)).strftime("%Y%m%d"),
        "toDate": today.strftime("%Y%m%d"),
        "title": "",
        "searchType": "1",
        "t1code": "-2",
        "t2Gcode": "-2",
        "t2code": "-2",
        "rowRange": "100",
        "lang": "ZH",
    }
    raw = fetcher.get(f"{BASE}/search/titleSearchServlet.do", params=params)
    try:
        payload = json.loads(raw)
        rows = json.loads(payload.get("result") or "[]")
    except (json.JSONDecodeError, TypeError):
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        title = _clean(row.get("TITLE", ""))
        long_text = _clean(row.get("LONG_TEXT", ""))
        link = row.get("FILE_LINK") or ""
        out.append(
            {
                "stock_code": code,
                "ann_date": _to_iso(row.get("DATE_TIME", "")),
                "title": title,
                "category": classify(title, long_text),
                "url": f"{BASE}{link}" if link.startswith("/") else link,
                "long_text": long_text,
            }
        )
    return out


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _to_iso(raw: str) -> str:
    """'15/09/2026 18:26' → '2026-09-15'。"""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", raw or "")
    if not m:
        return (raw or "")[:10]
    day, month, year = m.groups()
    return f"{year}-{month}-{day}"


def _configured_stock_ids(registry: Registry) -> dict[str, int]:
    """指标字典里预置的 stockId（省一次 prefix.do 反查；未命中则回退反查）。"""
    spec = registry.sources.get(SOURCE)
    raw = (spec.raw.get("stock_ids") if spec else None) or {}
    out: dict[str, int] = {}
    for code, value in raw.items():
        try:
            out[str(code)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def collect(conn: sqlite3.Connection, registry: Registry, *, days: int = 180) -> store.FetchResult:
    """抓公告 + 统计回购频次。"""
    started = datetime.now()
    fetcher = Fetcher()
    ensure_session(fetcher)

    configured = _configured_stock_ids(registry)
    all_rows: list[dict[str, Any]] = []
    for code in STOCKS:
        all_rows.extend(
            fetch_announcements(fetcher, code, days=days, stock_id=configured.get(code))
        )

    new_count = store.upsert_announcements(conn, all_rows) if all_rows else 0

    # 回购频次（近30日）→ 观测值
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    buybacks = [
        r
        for r in all_rows
        if r["stock_code"] == "00968"
        and r["ann_date"] >= cutoff
        and any(h in (r["title"] + r.get("long_text", "")) for h in _BUYBACK_HINT)
    ]
    if all_rows or buybacks:
        store.record_obs(
            conn,
            "xinyi_buyback_announcements",
            date.today(),
            len(buybacks),
            unit="次",
            source=SOURCE,
            source_url=f"{BASE}/search/titlesearch.xhtml?lang=zh",
            note=f"近30日回购公告 {len(buybacks)} 次",
        )
    conn.commit()

    duration = duration_ms(started)
    status = "ok" if all_rows else "error"
    message = f"{len(STOCKS)}只个股，{len(all_rows)}条公告，新增{new_count}，回购{len(buybacks)}次"
    return store.FetchResult(SOURCE, status, len(all_rows), message, duration)
