"""同花顺概念板块采集 — 概念节点层（L1）落库。

拉取 THS 375 概念 → 环节/公司/题材/地域/财务 分类 → 成分股抓取 →
与 stock_concept_map 合并 → 落 concept_master + concept_stock_map。

本机限制：THS 成分分页 ajax 常被 403，默认仅抓详情页首页（~10 只/概念）；
生产环境可传 fetch_all_pages=True 尝试全量分页。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Iterable

import akshare as ak
import requests
from bs4 import BeautifulSoup
from py_mini_racer import MiniRacer

from nous.data.collectors.fetchers.base import clean_session

# ── 表 DDL ──────────────────────────────────────────────────────────────

CONCEPT_DDL = """
CREATE TABLE IF NOT EXISTS concept_master (
    concept_id   TEXT PRIMARY KEY,
    concept_name TEXT NOT NULL,
    category     TEXT NOT NULL CHECK(category IN ('环节','公司','题材','地域','财务','其他')),
    chain_name   TEXT,
    stock_count  INTEGER DEFAULT 0,
    source       TEXT DEFAULT 'ths',
    updated_at   TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_concept_master_name ON concept_master(concept_name);

CREATE TABLE IF NOT EXISTS concept_stock_map (
    concept_id TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    source     TEXT DEFAULT 'ths',
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (concept_id, symbol),
    FOREIGN KEY (concept_id) REFERENCES concept_master(concept_id)
);
CREATE INDEX IF NOT EXISTS idx_concept_stock_symbol ON concept_stock_map(symbol);
"""

# ── 分类规则（t6 §1.3 + 实测 375 概念语义）──────────────────────────────

_COMPANY_MARKERS = (
    "比亚迪", "宁德时代", "阿里巴巴", "腾讯", "百度", "华为", "苹果", "特斯拉",
    "小米", "京东", "美团", "字节", "英伟达", "微软", "谷歌", "长安", "上汽",
    "一汽", "广汽", "吉利", "长城", "蔚来", "小鹏", "理想", "国家大基金持股",
)
_REGION_MARKERS = (
    "长三角", "西部大开发", "京津冀", "粤港澳", "海南", "自贸区", "一带一路",
    "雄安", "成渝", "东北振兴", "上海", "深圳", "北京", "广东", "浙江", "江苏",
)
_FINANCE_MARKERS = (
    "预增", "预减", "扭亏", "分红", "高送转", "回购", "减持", "增持", "破净",
    "ST", "摘帽", "业绩", "年报", "中报", "季报",
)
_THEME_MARKERS = (
    "创投", "参股", "国企改革", "央企", "混改", "借壳", "重组", "并购",
    "低空经济", "元宇宙", "网红", "电竞", "体育", "旅游", "养老",
)
_CHAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI": (
        "AI", "人工智能", "算力", "光模块", "CPO", "服务器", "数据中心",
        "语料", "大模型", "DeepSeek", "AIGC", "机器人", "人形机器人",
        "智能驾驶", "自动驾驶", "无人驾驶", "机器视觉", "多模态",
    ),
    "半导体": (
        "半导体", "芯片", "存储", "HBM", "封测", "晶圆", "EDA", "光刻",
        "集成电路", "MCU", "GPU", "CPU", "FPGA", "先进封装", "碳化硅",
        "氮化镓", "IGBT", "功率半导体",
    ),
    "新能源": (
        "锂", "电池", "储能", "光伏", "风电", "充电桩", "新能源", "锂电",
        "正极", "负极", "电解液", "隔膜", "氢能", "燃料电池", "BC电池",
        "固态电池", "钠离子",
    ),
    "医药": (
        "医药", "创新药", "CXO", "医疗器械", "中药", "生物", "疫苗",
        "基因", "阿尔茨海默", "减肥药", "GLP", "医美", "眼科", "牙科",
    ),
}


@dataclass
class ConceptRecord:
    concept_id: str
    concept_name: str
    category: str
    chain_name: str | None
    source: str = "ths"


@lru_cache(maxsize=1)
def _ths_v_code() -> str:
    js = MiniRacer()
    js.eval(ak.stock_feature.stock_fund_flow._get_file_content_ths("ths.js"))
    return js.call("v")


def _ths_headers() -> dict[str, str]:
    v = _ths_v_code()
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Cookie": f"v={v}",
    }


def classify_concept(name: str) -> str:
    """将概念名分为 环节/公司/题材/地域/财务/其他。"""
    if any(m in name for m in _COMPANY_MARKERS):
        return "公司"
    if any(m in name for m in _REGION_MARKERS):
        return "地域"
    if any(m in name for m in _FINANCE_MARKERS):
        return "财务"
    if any(m in name for m in _THEME_MARKERS):
        return "题材"
    return "环节"


def tag_chain(name: str) -> str | None:
    """聚焦链归属：AI / 半导体 / 新能源 / 医药。"""
    hits = [chain for chain, kws in _CHAIN_KEYWORDS.items() if any(kw in name for kw in kws)]
    if not hits:
        return None
    return hits[0] if len(hits) == 1 else "+".join(hits)


def fetch_ths_concept_list() -> list[ConceptRecord]:
    """拉取同花顺全量概念列表（~375）。"""
    df = ak.stock_board_concept_name_ths()
    records: list[ConceptRecord] = []
    for _, row in df.iterrows():
        name = str(row["name"]).strip()
        code = str(row["code"]).strip()
        records.append(ConceptRecord(
            concept_id=code,
            concept_name=name,
            category=classify_concept(name),
            chain_name=tag_chain(name),
            source="ths",
        ))
    return records


def _parse_stock_table(html: str) -> list[str]:
    """从 THS 概念详情/分页 HTML 解析成分股代码。"""
    soup = BeautifulSoup(html, "lxml")
    symbols: list[str] = []
    for tr in soup.select("table.m-table tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        sym = tds[1].get_text(strip=True)
        if re.fullmatch(r"\d{6}", sym):
            symbols.append(sym)
    return symbols


def _page_count(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    span = soup.find("span", class_="page_info")
    if not span or "/" not in span.text:
        return 1
    try:
        return int(span.text.split("/")[1])
    except (ValueError, IndexError):
        return 1


def fetch_ths_constituents(
    concept_code: str,
    *,
    fetch_all_pages: bool = False,
    sleep_s: float = 0.25,
) -> list[str]:
    """抓取概念成分股。默认仅首页；fetch_all_pages 尝试 ajax 分页（生产可用）。"""
    session = clean_session()
    headers = _ths_headers()
    detail_url = f"http://q.10jqka.com.cn/gn/detail/code/{concept_code}/"
    r = session.get(detail_url, headers=headers, timeout=20)
    r.encoding = "gbk"
    symbols = _parse_stock_table(r.text)
    if not fetch_all_pages:
        return symbols

    total_pages = _page_count(r.text)
    for page in range(2, total_pages + 1):
        time.sleep(sleep_s)
        v = _ths_v_code()
        ajax_headers = {
            **headers,
            "Cookie": f"v={v}",
            "hexin-v": v,
            "Referer": detail_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
        }
        ajax_url = (
            f"http://q.10jqka.com.cn/gn/detail/field/stockcode/order/desc/"
            f"page/{page}/ajax/1/code/{concept_code}"
        )
        try:
            ar = session.get(ajax_url, headers=ajax_headers, timeout=20)
            if ar.status_code != 200:
                break
            page_syms = _parse_stock_table(ar.text)
            if not page_syms:
                break
            symbols.extend(page_syms)
        except Exception:
            break
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def load_em_concepts(conn: sqlite3.Connection) -> list[tuple[str, str, list[str]]]:
    """从 stock_concept_map 读取现有 EM/手工概念 → (concept_id, name, symbols)。"""
    rows = conn.execute(
        "SELECT concept_code, concept_name, symbol FROM stock_concept_map ORDER BY concept_name, symbol"
    ).fetchall()
    buckets: dict[str, dict] = {}
    for code, name, sym in rows:
        key = name
        if key not in buckets:
            buckets[key] = {"code": code or f"em:{name}", "symbols": []}
        buckets[key]["symbols"].append(sym)
    return [(b["code"], name, b["symbols"]) for name, b in buckets.items()]


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(CONCEPT_DDL)
    conn.commit()


def upsert_concept(
    conn: sqlite3.Connection,
    rec: ConceptRecord,
    symbols: Iterable[str],
    stock_source: str,
) -> int:
    """写入 concept_master + concept_stock_map，返回写入成分股数。"""
    sym_list = list(dict.fromkeys(symbols))

    # 同名概念可能已有不同 concept_id（EM 手工 vs THS），按名称合并
    existing = conn.execute(
        "SELECT concept_id FROM concept_master WHERE concept_name = ?", (rec.concept_name,)
    ).fetchone()
    concept_id = existing[0] if existing else rec.concept_id

    conn.execute(
        """INSERT INTO concept_master (concept_id, concept_name, category, chain_name, stock_count, source, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))
           ON CONFLICT(concept_id) DO UPDATE SET
             concept_name=excluded.concept_name,
             category=excluded.category,
             chain_name=COALESCE(excluded.chain_name, concept_master.chain_name),
             stock_count=excluded.stock_count,
             source=excluded.source,
             updated_at=excluded.updated_at""",
        (concept_id, rec.concept_name, rec.category, rec.chain_name, len(sym_list), rec.source),
    )
    for sym in sym_list:
        conn.execute(
            """INSERT OR IGNORE INTO concept_stock_map (concept_id, symbol, source, updated_at)
               VALUES (?, ?, ?, datetime('now','localtime'))""",
            (concept_id, sym, stock_source),
        )
    # 刷新 stock_count
    actual = conn.execute(
        "SELECT COUNT(*) FROM concept_stock_map WHERE concept_id = ?", (concept_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE concept_master SET stock_count = ? WHERE concept_id = ?",
        (actual, concept_id),
    )
    return actual


def build_concept_master(
    conn: sqlite3.Connection,
    *,
    fetch_constituents: bool = True,
    fetch_all_pages: bool = False,
    sleep_s: float = 0.3,
    limit: int | None = None,
) -> dict:
    """主流程：THS 概念 + EM 合并落库。返回统计摘要。"""
    ensure_tables(conn)
    ths_records = fetch_ths_concept_list()
    if limit:
        ths_records = ths_records[:limit]

    stats = {
        "ths_total": len(ths_records),
        "segment_count": 0,
        "with_chain": 0,
        "stocks_ths": 0,
        "stocks_em": 0,
        "merged_names": 0,
    }

    # 名称 → EM 成分股（用于合并）
    em_by_name: dict[str, tuple[str, list[str]]] = {}
    for cid, name, syms in load_em_concepts(conn):
        em_by_name[name] = (cid, syms)

    # THS 概念落库
    for i, rec in enumerate(ths_records):
        if rec.category == "环节":
            stats["segment_count"] += 1
        if rec.chain_name:
            stats["with_chain"] += 1

        symbols: list[str] = []
        if fetch_constituents:
            try:
                symbols = fetch_ths_constituents(
                    rec.concept_id, fetch_all_pages=fetch_all_pages, sleep_s=sleep_s,
                )
                stats["stocks_ths"] += len(symbols)
            except Exception:
                pass
            time.sleep(sleep_s)

        # 与 EM 同名概念合并成分股
        if rec.concept_name in em_by_name:
            stats["merged_names"] += 1
            _, em_syms = em_by_name[rec.concept_name]
            symbols = list(dict.fromkeys(symbols + em_syms))
            rec = ConceptRecord(
                concept_id=rec.concept_id,
                concept_name=rec.concept_name,
                category=rec.category,
                chain_name=rec.chain_name,
                source="merged",
            )

        upsert_concept(conn, rec, symbols, "ths" if rec.source == "ths" else "merged")
        if (i + 1) % 50 == 0:
            conn.commit()

    # EM-only 概念（THS 列表中无同名）补录
    ths_names = {r.concept_name for r in ths_records}
    for name, (cid, syms) in em_by_name.items():
        if name in ths_names:
            continue
        rec = ConceptRecord(
            concept_id=cid if not str(cid).startswith("MANUAL_") else f"em:{name}",
            concept_name=name,
            category=classify_concept(name),
            chain_name=tag_chain(name),
            source="em",
        )
        stats["stocks_em"] += len(syms)
        upsert_concept(conn, rec, syms, "em")

    conn.commit()

    # 汇总行数
    stats["master_rows"] = conn.execute("SELECT COUNT(*) FROM concept_master").fetchone()[0]
    stats["stock_map_rows"] = conn.execute("SELECT COUNT(*) FROM concept_stock_map").fetchone()[0]
    stats["segment_in_db"] = conn.execute(
        "SELECT COUNT(*) FROM concept_master WHERE category='环节'"
    ).fetchone()[0]
    stats["chain_tagged"] = conn.execute(
        "SELECT COUNT(*) FROM concept_master WHERE chain_name IS NOT NULL"
    ).fetchone()[0]
    return stats


def export_segment_report(conn: sqlite3.Connection) -> list[dict]:
    """导出环节类概念清单（含链归属）。"""
    rows = conn.execute(
        """SELECT concept_id, concept_name, category, chain_name, stock_count, source
           FROM concept_master WHERE category='环节' ORDER BY chain_name, concept_name"""
    ).fetchall()
    return [dict(r) for r in rows]
