"""公告 PDF 抽取 — 从港交所业绩公告里抓季度级分部数据（分部毛利率/收入/归母）。

为什么必须用 pdftotext
----------------------
实测：pypdf 抽港交所中文 PDF 会**汉字全乱码**（HKEX 中文子集字体缺 glyph→Unicode 映射），
只剩数字可读，无法按关键词定位表格；`pdftotext -layout` 中文完全可读且保留表格列对齐。
所以本采集器**依赖 poppler 的 pdftotext**，缺失时明确报错而不是静默降级。

    macOS: brew install poppler      （提供 /opt/homebrew/bin/pdftotext）

抓哪份 PDF
----------
直接复用 hkex 采集器已入库的 announcements 表：取该公司最新的
`interim_results` / `annual_results` 公告 PDF。这样不用再写一遍公告发现逻辑。

覆盖的指标（均以实测原文片段校验过）
------------------------------------
信义光能中期/年度公告 P1+P14:  xinyi_revenue / xinyi_attributable_profit /
                               xinyi_glass_gm / xinyi_renewable_gm
福莱特中期公告 P16 主要产品毛利表: flat_glass_glass_gm
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nous.research.pvglass import store
from nous.research.pvglass.http import FetchError, Fetcher, duration_ms
from nous.research.pvglass.registry import Registry

SOURCE = "report_pdf"
PDFTOTEXT = "pdftotext"

#: 公司 → (代码, 公告分类)
COMPANIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "xinyi": ("00968", ("interim_results", "annual_results")),
    "flat": ("06865", ("interim_results", "annual_results")),
}

# ── 信义光能：分部表 + 财务摘要 ─────────────────────────────────────────
# P14 分部表（pdftotext -layout 后逐字形态）：
#   來自外部客戶的收益      7,162,693     1,210,427      —     56,986    8,430,106
#   毛利               237,832      673,833       —       8,653    920,318
_XINYI_SEG_REVENUE = re.compile(r"來自外部客戶的收益\s+([\d,]+)\s+([\d,]+)")
# 分部表的毛利行必须有 5 列（第三列可能是「—」）：
#   毛利               237,832      673,833       —       8,653    920,318
# 而利润表的毛利行只有 2 列（920,318 / 1,998,536）——必须区分，否则会把 2025 总毛利
# 当成可再生的分部毛利（曾导致 165% 越界）。
_XINYI_SEG_GROSS = re.compile(
    r"^毛利\s+([\d,]+)\s+([\d,]+)\s+(?:[—–\-]|[\d,.]+)\s+([\d,]+)\s+([\d,]+)\s*$", re.M
)
#: 分部表内「來自外部客戶的收益」到「毛利」行的最大跨度
_SEG_WINDOW = 500
# P1 摘要：本公司權益持有人應佔溢利               39.0         745.8   -94.8%
_XINYI_ATTR = re.compile(r"本公司權益持有人應佔溢利\s+([\d,.]+)")
# 收益                      8,430.1      10,931.8   -22.9%
_XINYI_REVENUE_SUMMARY = re.compile(r"^收益\s+([\d,.]+)\s+([\d,.]+)", re.M)

# ── 福莱特：P16 主要产品毛利表 ────────────────────────────────────────
# 光伏玻璃              394,432.53        6.73    854,843.90       12.31
# 表头（-layout 可能把汉字拆开，故允许字间空白）：
#                       毛利          毛利率         毛利          毛利率
# 產品種類           人 民 幣（千 元）          (%) 人 民 幣（千 元）           (%)
# 光伏玻璃              394,432.53        6.73    854,843.90       12.31
# 锚点必须用「毛利 + 毛利率」这组表头，而不是「產品種類」——
# 同一份报告里有 3 张以「產品種類」开头的表（按产品的收益表、毛利表…），
# 用「產品種類」会命中收益表，把收入占比 87.90% 当成毛利率（已实测踩坑）。
_FLAT_TABLE_ANCHOR = re.compile(r"毛\s*利\s+毛\s*利\s*率")
#: 兜底锚点：毛利表上方的表标题（-layout 会把汉字拆开）
_FLAT_TABLE_ANCHOR_ALT = re.compile(r"主\s*要\s*產\s*品\s*毛\s*利")
_FLAT_WINDOW = 900
# 结构校验：产品毛利表必然同时出现「浮法玻璃」（分部收入表没有这一行）
_FLAT_TABLE_MARKER = re.compile(r"浮\s*法\s*玻\s*璃")
_FLAT_GLASS_ROW = re.compile(
    r"^光\s*伏\s*玻\s*璃\s+([\-\d,.]+)\s+([\-\d.]+)(?:\s+([\-\d,.]+)\s+([\-\d.]+))?\s*$",
    re.M,
)
_FLAT_TOTAL_ROW = re.compile(
    r"^合\s*計\s+([\-\d,.]+)\s+([\-\d.]+)(?:\s+([\-\d,.]+)\s+([\-\d.]+))?\s*$", re.M
)


@dataclass
class PdfExtract:
    indicator_id: str
    value: float
    unit: str
    note: str


def pdftotext_path() -> str | None:
    """返回可用的 pdftotext 路径；缺失返回 None（调用方需明确报错）。"""
    return shutil.which(PDFTOTEXT)


def _num(token: str | None) -> float | None:
    if token is None:
        return None
    cleaned = token.replace(",", "").strip()
    if cleaned in {"", "—", "-", "–"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def pdf_to_text(pdf_path: Path) -> str:
    """pdftotext -layout（保留表格列对齐）→ 文本。"""
    binary = pdftotext_path()
    if binary is None:
        raise RuntimeError("缺少 pdftotext；请先 brew install poppler")
    proc = subprocess.run(
        [binary, "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdftotext 退出码 {proc.returncode}: {proc.stderr[:160]}")
    return proc.stdout


# ── 抽取器（纯函数，便于离线测试）────────────────────────────────────
def segment_rows(text: str) -> tuple[re.Match[str] | None, re.Match[str] | None]:
    """定位信义分部表的两行：收入行与毛利行（以收入行为锚点向后限窗）。"""
    rev = _XINYI_SEG_REVENUE.search(text)
    if not rev:
        return None, None
    window = text[rev.end() : rev.end() + _SEG_WINDOW]
    return rev, _XINYI_SEG_GROSS.search(window)


def extract_xinyi(text: str) -> list[PdfExtract]:
    out: list[PdfExtract] = []

    rev, gross = segment_rows(text)
    if rev and gross:
        glass_rev, renew_rev = _num(rev.group(1)), _num(rev.group(2))
        glass_gp, renew_gp = _num(gross.group(1)), _num(gross.group(2))
        if glass_rev and glass_gp and glass_rev > 0:
            gm = glass_gp / glass_rev * 100
            out.append(
                PdfExtract(
                    "xinyi_glass_gm",
                    round(gm, 2),
                    "%",
                    f"分部表：玻璃收入 {rev.group(1)} − 毛利 {gross.group(1)} → {gm:.2f}%",
                )
            )
        if renew_rev and renew_gp and renew_rev > 0:
            gm = renew_gp / renew_rev * 100
            out.append(
                PdfExtract(
                    "xinyi_renewable_gm",
                    round(gm, 2),
                    "%",
                    f"分部表：可再生收入 {rev.group(2)} − 毛利 {gross.group(2)} → {gm:.2f}%",
                )
            )

    summary_rev = _XINYI_REVENUE_SUMMARY.search(text)
    if summary_rev:
        value = _num(summary_rev.group(1))
        if value is not None:
            out.append(
                PdfExtract("xinyi_revenue", value, "百万元", f"财务摘要：收益 {summary_rev.group(1)}")
            )

    attr = _XINYI_ATTR.search(text)
    if attr:
        value = _num(attr.group(1))
        if value is not None:
            out.append(
                PdfExtract(
                    "xinyi_attributable_profit",
                    value,
                    "百万元",
                    f"财务摘要：权益持有人应占溢利 {attr.group(1)}",
                )
            )
    return out


def product_table_window(text: str) -> str | None:
    """定位福莱特「主要产品毛利」表体。

    锚点是**表头**「產品種類」而不是正文里的「主要產品毛利」——后者在 MD&A 里也出现，
    会把后面的分部收入/占比表罩进来（实测错取到 87.90% 的分部收入占比）。
    再要求窗口内出现「浮法玻璃」做结构校验（分部收入表没有该行）。
    """
    for pattern in (_FLAT_TABLE_ANCHOR, _FLAT_TABLE_ANCHOR_ALT):
        for anchor in pattern.finditer(text):
            window = text[anchor.end() : anchor.end() + _FLAT_WINDOW]
            if _FLAT_TABLE_MARKER.search(window) and _FLAT_GLASS_ROW.search(window):
                return window
    return None


def extract_flat(text: str) -> list[PdfExtract]:
    out: list[PdfExtract] = []
    window = product_table_window(text)
    if not window:
        return out

    row = _FLAT_GLASS_ROW.search(window)
    if row:
        gm = _num(row.group(2))
        if gm is not None and -100.0 <= gm <= 100.0:
            out.append(
                PdfExtract(
                    "flat_glass_glass_gm",
                    gm,
                    "%",
                    f"主要产品毛利表：光伏玻璃毛利 {row.group(1)} 千元、毛利率 {row.group(2)}%",
                )
            )
    total = _FLAT_TOTAL_ROW.search(window)
    if total:
        gm = _num(total.group(2))
        if gm is not None and -100.0 <= gm <= 100.0:
            out.append(
                PdfExtract(
                    "flat_overall_gm",
                    gm,
                    "%",
                    f"主要产品毛利表：合计毛利 {total.group(1)} 千元、毛利率 {total.group(2)}%",
                )
            )
    return out


EXTRACTORS = {"xinyi": extract_xinyi, "flat": extract_flat}

#: 允许写入的指标 → 合理区间（防抽错：毛利率必须 0~100）
_SANITY: dict[str, tuple[float, float]] = {
    "xinyi_glass_gm": (0.0, 100.0),
    "xinyi_renewable_gm": (0.0, 100.0),
    "flat_glass_glass_gm": (0.0, 100.0),
    "xinyi_revenue": (0.0, 200_000.0),
    "xinyi_attributable_profit": (-100_000.0, 200_000.0),
}


def _period_end(ann_date: str, category: str) -> str:
    """公告类型 → 报告期末（中期=6/30，年度=12/31）。"""
    try:
        year = int(str(ann_date)[:4])
    except ValueError:
        return ann_date
    month = 6 if category == "interim_results" else 12
    day = 30 if month == 6 else 31
    return f"{year:04d}-{month:02d}-{day:02d}"


def _latest_report(
    conn: sqlite3.Connection, code: str, categories: tuple[str, ...]
) -> sqlite3.Row | None:
    """最新的**报表本体** PDF：先按分类候选，再用标题正则排掉说明会/通知/摘要。"""
    from nous.research.pvglass.sources.hkex import NON_REPORT_TITLE_RE, REPORT_TITLE_RE

    placeholders = ",".join("?" * len(categories))
    rows = conn.execute(
        f"""
        SELECT * FROM announcements
        WHERE stock_code = ? AND category IN ({placeholders}) AND url LIKE '%.pdf'
        ORDER BY ann_date DESC LIMIT 20
        """,
        (code, *categories),
    ).fetchall()
    for row in rows:
        title = str(row["title"])
        if NON_REPORT_TITLE_RE.search(title):
            continue
        if REPORT_TITLE_RE.search(title):
            return row
    return None


def _already_extracted(conn: sqlite3.Connection, indicator_id: str, period: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*) FROM obs
        WHERE indicator_id = ? AND obs_date >= ? AND source = ?
        """,
        (indicator_id, period, SOURCE),
    ).fetchone()
    return bool(row and row[0])


def collect(conn: sqlite3.Connection, registry: Registry, **_: Any) -> store.FetchResult:
    started = datetime.now()
    if pdftotext_path() is None:
        return store.FetchResult(
            SOURCE,
            "skipped",
            0,
            "缺少 pdftotext（brew install poppler 后重试）；不静默降级到 pypdf（中文会乱码）",
            duration_ms(started),
        )

    fetcher = Fetcher()
    written = 0
    notes: list[str] = []
    for anchor, (code, categories) in COMPANIES.items():
        row = _latest_report(conn, code, categories)
        if row is None:
            notes.append(f"{code}: 库里没有业绩公告（先跑 nous pv fetch -s hkex）")
            continue
        period = _period_end(str(row["ann_date"]), str(row["category"]))
        if _already_extracted(conn, _probe_indicator(anchor), period):
            notes.append(f"{code}: {period} 已抽取，跳过")
            continue

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "report.pdf"
            try:
                content = fetcher.session.get(row["url"], timeout=60).content
                pdf_path.write_bytes(content)
                text = pdf_to_text(pdf_path)
            except (FetchError, RuntimeError, OSError) as exc:
                notes.append(f"{code}: 抽取失败 {type(exc).__name__}: {exc}"[:120])
                continue

        extracts = EXTRACTORS[anchor](text)
        if not extracts:
            notes.append(f"{code}: 文本已拿到（{len(text)} 字符）但正则未命中，可能报表模板变更")
            continue
        for item in extracts:
            if item.indicator_id not in registry.indicators:
                continue  # 例如 flat_overall_gm 未声明 → 只保留在 note
            low, high = _SANITY.get(item.indicator_id, (-1e12, 1e12))
            if not (low <= item.value <= high):
                notes.append(f"{code}: {item.indicator_id} 越界({item.value})，已跳过")
                continue
            store.record_obs(
                conn,
                item.indicator_id,
                period,
                item.value,
                unit=item.unit,
                source=SOURCE,
                source_url=str(row["url"]),
                note=item.note,
            )
            written += 1
        notes.append(f"{code}: 抽取 {len(extracts)} 项（{period}）")
    conn.commit()

    status = "ok" if written else ("partial" if notes else "error")
    return store.FetchResult(SOURCE, status, written, " | ".join(notes)[:300], duration_ms(started))


def _probe_indicator(anchor: str) -> str:
    return "xinyi_glass_gm" if anchor == "xinyi" else "flat_glass_glass_gm"
