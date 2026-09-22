"""业绩公告 PDF 抽取测试 — 用实测原文片段，覆盖两个真实踩过的错行坑。

坑 1（信义）：利润表的「毛利 920,318 1,998,536」只有 2 列，
              分部表的「毛利 237,832 673,833 — 8,653 920,318」有 5 列。
              不加区分会把 2025 年总毛利当成可再生能源分部毛利（算出 165%）。
坑 2（福莱特）：同一份报告里有 3 张以「產品種類」开头的表，
              收益表的「光伏玻璃 5,861,594.65 87.90」是**收入占比**，
              毛利表才是「光伏玻璃 394,432.53 6.73」。
"""

from __future__ import annotations

import pytest

from nous.core.db import get_db
from nous.research.pvglass import store
from nous.research.pvglass.sources import hkex, hkex_pdf
from nous.research.pvglass.sources.hkex_pdf import PdfExtract

# ── 信义光能（pdftotext -layout 后的逐字形态）────────────────────────
XINYI_SEGMENT_TABLE = """
截至二零二六年及二零二五年六月三十日止六個月的分部資料如下：

                     截至二零二六年六月三十日止六個月（未經審核）
                  太陽能          可再生
                玻璃銷售         能源業務        其他分部     未分配           總計
               人民幣千元 人民幣千元 人民幣千元 人民幣千元 人民幣千元

分部收益
 於某個時間點確認      7,162,693     1,210,427      —          —    8,373,120
 隨着時間確認                —           —        —     56,986       56,986

來自外部客戶的收益      7,162,693     1,210,427      —     56,986    8,430,106
銷售成本           (6,924,861)   (536,594)      —     (48,333) (7,509,788)

毛利               237,832      673,833       —       8,653    920,318
"""

XINYI_SUMMARY = """
                      截至六月三十日止六個月
                      二零二六年        二零二五年          變動
                    人民幣百萬元        人民幣百萬元

收益                      8,430.1      10,931.8   -22.9%

本公司權益持有人應佔溢利               39.0         745.8   -94.8%

每股盈利－基本             人民幣 0.43 分    人民幣 8.21 分    -94.8%
"""

XINYI_INCOME_STATEMENT = """
綜合損益表
                          二零二六年      二零二五年
                          人民幣千元      人民幣千元

毛利                              920,318    1,998,536
其他收入                           65,000       48,000
"""

# ── 福莱特（三种表混在同一份文本里，顺序与真实报告一致）──────────────
FLAT_REVENUE_TABLE = """
收益

下 表 載 列 本 集 團 按 產 品 種 類 及 地 域 劃 分 的 收 益 明 細：

                     2026 年 1–6 月               2025 年 1–6 月
產品種類           人 民 幣（千 元）             (%) 人 民 幣（千 元）                     (%)

光伏玻璃             5,861,594.65        87.90      6,944,929.38           89.76
家居玻璃               260,000.00         3.90        250,000.00            3.23
浮法玻璃                80,000.00         1.20         90,000.00            1.16

合計               6,668,210.14       100.00      7,737,028.14          100.00
"""

FLAT_MARGIN_TABLE = """
下 表 載 列 本 集 團 主 要 產 品 毛 利 情 況：

                     2026 年 1–6 月            2025 年 1–6 月
                      毛利          毛利率         毛利          毛利率
產品種類           人 民 幣（千 元）          (%) 人 民 幣（千 元）           (%)

光伏玻璃              394,432.53        6.73    854,843.90       12.31
家居玻璃               15,991.36       13.35     20,885.95       17.12
工程玻璃               35,312.79       16.21     83,725.56       34.49
浮法玻璃              -12,233.59      -12.01     -1,948.36       -6.96
發電收入               78,172.28       35.00     75,298.68       30.76
採礦產品               18,390.45       16.43       -798.02      -68.87
其他業務               15,107.73       47.56     55,099.28       35.92
合計                545,173.55       8.18    1,087,106.99     14.05
"""


def _by_id(items: list[PdfExtract]) -> dict[str, float]:
    return {item.indicator_id: item.value for item in items}


# ── 信义 ───────────────────────────────────────────────────────────────
def test_xinyi_segment_table_margins():
    items = _by_id(hkex_pdf.extract_xinyi(XINYI_SEGMENT_TABLE))
    # 分部表：毛利/收入
    assert items["xinyi_glass_gm"] == pytest.approx(3.32, abs=0.01)
    assert items["xinyi_renewable_gm"] == pytest.approx(55.67, abs=0.01)


def test_xinyi_summary_fields():
    text = XINYI_SUMMARY + XINYI_SEGMENT_TABLE
    items = _by_id(hkex_pdf.extract_xinyi(text))
    assert items["xinyi_revenue"] == pytest.approx(8430.1)
    assert items["xinyi_attributable_profit"] == pytest.approx(39.0)


def test_xinyi_ignores_income_statement_gross_row():
    """回归坑 1：利润表的 2 列「毛利」行不得被当成分部毛利。"""
    text = XINYI_INCOME_STATEMENT + XINYI_SEGMENT_TABLE
    items = _by_id(hkex_pdf.extract_xinyi(text))
    assert items["xinyi_renewable_gm"] == pytest.approx(55.67, abs=0.01)
    assert items["xinyi_glass_gm"] == pytest.approx(3.32, abs=0.01)


def test_xinyi_without_segment_table_yields_nothing():
    """只有利润表时不应产出任何分部毛利率（宁可空，不可错）。"""
    assert hkex_pdf.extract_xinyi(XINYI_INCOME_STATEMENT) == []


def test_segment_rows_helper_returns_both_rows():
    rev, gross = hkex_pdf.segment_rows(XINYI_SEGMENT_TABLE)
    assert rev is not None and gross is not None
    assert rev.group(1) == "7,162,693"
    assert gross.group(1) == "237,832" and gross.group(2) == "673,833"


# ── 福莱特 ─────────────────────────────────────────────────────────────
def test_flat_margin_table_picked_not_revenue_table():
    """回归坑 2：必须取毛利表（6.73/8.18），不能取收益表（87.90/100.00）。"""
    text = FLAT_REVENUE_TABLE + FLAT_MARGIN_TABLE
    items = _by_id(hkex_pdf.extract_flat(text))
    assert items["flat_glass_glass_gm"] == pytest.approx(6.73)
    assert items["flat_overall_gm"] == pytest.approx(8.18)
    assert items["flat_glass_glass_gm"] != pytest.approx(87.90)
    assert items["flat_overall_gm"] != pytest.approx(100.00)


def test_flat_negative_margin_rows_supported():
    """浮法玻璃毛利率为负，窗口/行正则不能因为负号而错位。"""
    window = hkex_pdf.product_table_window(FLAT_MARGIN_TABLE)
    assert window is not None
    assert "浮法玻璃" in window  # 结构校验标记
    assert hkex_pdf._FLAT_TOTAL_ROW.search(window) is not None


def test_flat_revenue_table_alone_yields_nothing():
    """只有收益表（没有毛利表头）时不应产出毛利率。"""
    assert hkex_pdf.extract_flat(FLAT_REVENUE_TABLE) == []


def test_flat_window_requires_marker_row():
    no_marker = FLAT_MARGIN_TABLE.replace("浮法玻璃", "某产品")
    assert hkex_pdf.product_table_window(no_marker) is None


# ── 公告分类与报表筛选 ────────────────────────────────────────────────
def test_classify_half_year_results_is_interim_not_annual():
    """回归：'半年度業績' 含 '年度業績' 子串，曾被误判为年报。"""
    title = "海外監管公告 - 福萊特玻璃集團股份有限公司關於2026年半年度業績說明會情況的公告"
    assert hkex.classify(title, "") == "interim_results"
    assert hkex.classify("截至二零二六年六月三十日止六個月的中期業績公告", "") == "interim_results"
    assert hkex.classify("截至二零二五年十二月三十一日止年度的年度業績公告", "") == "annual_results"


def test_report_title_filters_exclude_notice_documents():
    assert hkex.REPORT_TITLE_RE.search("截至二零二六年六月三十日止六個月的中期業績公告")
    assert hkex.NON_REPORT_TITLE_RE.search("關於2026年半年度業績說明會情況的公告")
    assert not hkex.REPORT_TITLE_RE.search("董事會會議通知")


def test_latest_report_skips_notice_and_picks_real_report(tmp_path, monkeypatch):
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    store.init_db()
    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        store.upsert_announcements(
            conn,
            [
                {  # 更新的“说明会”，必须跳过
                    "stock_code": "06865",
                    "ann_date": "2026-09-07",
                    "title": "海外監管公告 - 關於2026年半年度業績說明會情況的公告",
                    "category": "interim_results",
                    "url": "https://example.com/notice.pdf",
                },
                {  # 真正的报表
                    "stock_code": "06865",
                    "ann_date": "2026-08-25",
                    "title": "截至二零二六年六月三十日止六個月的中期業績公告",
                    "category": "interim_results",
                    "url": "https://example.com/report.pdf",
                },
            ],
        )
        conn.commit()
        row = hkex_pdf._latest_report(conn, "06865", ("interim_results", "annual_results"))
    assert row is not None
    assert row["url"].endswith("report.pdf")


# ── 期间推断与降级 ─────────────────────────────────────────────────────
def test_period_end_from_category():
    assert hkex_pdf._period_end("2026-07-31", "interim_results") == "2026-06-30"
    assert hkex_pdf._period_end("2027-03-20", "annual_results") == "2027-12-31"


def test_missing_pdftotext_is_skipped_not_faked(tmp_path, monkeypatch):
    """没有 poppler 时必须明确 skipped；不许静默降级到 pypdf（中文会乱码）。"""
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(hkex_pdf.shutil, "which", lambda _name: None)
    store.init_db()
    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        result = hkex_pdf.collect(conn, _registry())
    assert result.status == "skipped"
    assert "poppler" in result.message


def test_pdf_to_text_raises_without_binary(monkeypatch):
    monkeypatch.setattr(hkex_pdf.shutil, "which", lambda _name: None)
    from pathlib import Path

    with pytest.raises(RuntimeError, match="poppler"):
        hkex_pdf.pdf_to_text(Path("/nonexistent.pdf"))


def _registry():
    from nous.research.pvglass.registry import load_registry

    return load_registry()
