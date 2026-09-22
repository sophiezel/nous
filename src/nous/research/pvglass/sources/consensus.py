"""券商一致预期采集器 — etnet 盈利预测概览。

关键设计: 同时落"逐家预测"和"中位数"，因为均值会被极值污染。
实例: 2026 年信义光能 8 家中位数 371.3 百万，但均值 484.9 百万，
仅因摩根士丹利一行 2,830.6（其 2026/2027/2028 EPS 均为 30.00 分，疑似占位错误）。
"""

from __future__ import annotations

import html as _html
import re
import sqlite3
import statistics
from datetime import date, datetime
from typing import Any

from nous.research.pvglass import store
from nous.research.pvglass.http import FetchError, Fetcher, duration_ms, to_float
from nous.research.pvglass.registry import Registry

SOURCE = "etnet"
URL_TMPL = "https://www.etnet.com.hk/www/tc/stocks/realtime/quote_profit.php?code={code}"
STATS_URL_TMPL = "https://www.etnet.com.hk/www/tc/stocks/realtime/quote.php?code={code}"

#: 指标字典里的个股 → etnet 代码（etnet 用 5 位：968 而非 00968）
TRACKED: dict[str, str] = {"00968": "968"}

FY_METRIC_MAP = {
    "2026": "consensus_np_fy2026",
    "2027": "consensus_np_fy2027",
    "2028": "consensus_np_fy2028",
}


def _cells(row_html: str) -> list[str]:
    raw = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row_html)
    out = []
    for cell in raw:
        text = _html.unescape(re.sub(r"(?s)<[^>]+>", " ", cell))
        out.append(re.sub(r"\s+", " ", text).strip())
    return [c for c in out if c != ""]


def parse(html: str) -> dict[str, Any]:
    """返回 {'consensus': [...], 'brokers': [...]}。"""
    rows = [_cells(r) for r in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html)]
    consensus: list[dict[str, Any]] = []
    brokers: list[dict[str, Any]] = []
    mode: str | None = None
    for cells in rows:
        joined = " ".join(cells)
        if "最高" in joined and "最低" in joined:
            mode = "consensus"
            continue
        if "證券商" in joined or "證券商" in joined or "證券商" in joined:
            mode = "brokers"
            continue
        if mode == "consensus" and len(cells) >= 7 and re.fullmatch(r"\d{4}", cells[0]):
            consensus.append(
                {
                    "fiscal_year": cells[0],
                    "net_profit": to_float(cells[1]),
                    "eps": to_float(cells[2]),
                    "dps": to_float(cells[3]),
                    "max": to_float(cells[5]),
                    "min": to_float(cells[6]),
                }
            )
        elif mode == "brokers" and len(cells) >= 8 and re.fullmatch(r"\d{4}", cells[0]):
            brokers.append(
                {
                    "fiscal_year": cells[0],
                    "net_profit": to_float(cells[1]),
                    "eps": to_float(cells[2]),
                    "dps": to_float(cells[3]),
                    "broker": cells[4],
                    "rating": cells[5],
                    "target_price": to_float(cells[6]),
                    "updated": cells[7],
                }
            )
    return {"consensus": consensus, "brokers": brokers}


def _as_of(brokers: list[dict[str, Any]]) -> str:
    dates = []
    for b in brokers:
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", b.get("updated", ""))
        if m:
            dates.append(f"{m.group(3)}-{m.group(2)}-{m.group(1)}")
    return max(dates) if dates else date.today().isoformat()


def collect(conn: sqlite3.Connection, registry: Registry, **_: Any) -> store.FetchResult:
    started = datetime.now()
    fetcher = Fetcher(encoding_hint=None) if False else Fetcher()
    written = 0
    messages: list[str] = []

    for code, etnet_code in TRACKED.items():
        try:
            page = fetcher.get(URL_TMPL.format(code=etnet_code))
        except FetchError as exc:
            messages.append(f"{code} 拉取失败: {str(exc)[:50]}")
            continue
        parsed = parse(page)
        brokers = parsed["brokers"]
        consensus = parsed["consensus"]
        as_of = _as_of(brokers)

        rows: list[dict[str, Any]] = []
        # 逐家券商
        for b in brokers:
            rows.append(
                {
                    "stock_code": code,
                    "fiscal_year": b["fiscal_year"],
                    "metric": "net_profit",
                    "broker": b["broker"],
                    "value": b["net_profit"],
                    "rating": b["rating"],
                    "target_price": b["target_price"],
                    "as_of": as_of,
                    "source": SOURCE,
                }
            )
        # 综合（保留 etnet 自己的口径）
        for c in consensus:
            rows.append(
                {
                    "stock_code": code,
                    "fiscal_year": c["fiscal_year"],
                    "metric": "net_profit",
                    "broker": "CONSENSUS",
                    "value": c["net_profit"],
                    "as_of": as_of,
                    "source": SOURCE,
                }
            )
        written += store.upsert_consensus(conn, rows)

        # 观测值：中位数（抗极值）+ 目标价均值
        for fy, indicator_id in FY_METRIC_MAP.items():
            values = [
                b["net_profit"]
                for b in brokers
                if b["fiscal_year"] == fy and b["net_profit"] is not None
            ]
            if not values:
                continue
            median = statistics.median(values)
            unit = "百万元"
            store.record_obs(
                conn,
                indicator_id,
                as_of,
                median,
                unit=unit,
                source=SOURCE,
                source_url=URL_TMPL.format(code=etnet_code),
                note=f"n={len(values)} 中位数; 均值={statistics.fmean(values):.1f}; 区间[{min(values):.0f},{max(values):.0f}]",
            )
            # 极值预警：任一家超过中位数 3 倍或为负
            outliers = [v for v in values if v > 3 * median or v < 0]
            if outliers:
                messages.append(
                    f"{code} FY{fy} 存在极值 {outliers}（中位数 {median:.0f}），均值不可用"
                )

        tps = [b["target_price"] for b in brokers if b["target_price"] is not None]
        if tps:
            store.record_obs(
                conn,
                "consensus_tp_avg",
                as_of,
                statistics.fmean(tps),
                unit="HKD",
                source=SOURCE,
                source_url=URL_TMPL.format(code=etnet_code),
                note=f"n={len(tps)} 区间[{min(tps):.2f},{max(tps):.2f}]",
            )
    conn.commit()

    duration = duration_ms(started)
    status = "ok" if written else "error"
    message = f"写入{written}行" + ("; " + "; ".join(messages[:3]) if messages else "")
    return store.FetchResult(SOURCE, status, written, message, duration)
