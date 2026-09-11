"""主营构成环节标签层（L2）— 聚焦链股票 → 产业链环节聚类落库。

流程：concept_master 聚焦链股票池 → stock_zygc_em 主营产品 →
关键词规则聚类 → stock_chain_segment。

聚类方法：按链内置环节词典对「按产品分类」收入占比最高项做规则匹配（非 ML）。
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass

import akshare as ak
import pandas as pd

# ── DDL ───────────────────────────────────────────────────────────────────

SEGMENT_DDL = """
CREATE TABLE IF NOT EXISTS stock_chain_segment (
    symbol       TEXT NOT NULL,
    chain_name   TEXT NOT NULL,
    segment      TEXT NOT NULL,
    main_product TEXT,
    revenue_ratio REAL,
    report_date  TEXT,
    source       TEXT DEFAULT 'zygc',
    updated_at   TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (symbol, chain_name)
);
CREATE INDEX IF NOT EXISTS idx_chain_segment_chain ON stock_chain_segment(chain_name, segment);
"""

# 每条聚焦链的环节词典：(segment_name, keywords...)
CHAIN_SEGMENTS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "半导体": (
        ("设计", ("芯片设计", "IC设计", "EDA", "IP核", "FPGA", "GPU设计", "模拟芯片", "设计服务")),
        ("制造", ("晶圆", "代工", "制造", "集成电路", "半导体器件", "功率器件", "半导体显示", "显示器件", "芯片")),
        ("封测", ("封装", "封测", "测试", "先进封装", "Chiplet")),
        ("设备", ("刻蚀", "薄膜", "沉积", "光刻", "清洗", "检测设备", "半导体设备", "真空", "炉管")),
        ("材料", ("硅片", "光刻胶", "靶材", "特气", "电子气体", "CMP", "湿电子", "材料", "偏光片", "掩膜")),
    ),
    "新能源": (
        ("上游资源", ("锂矿", "钴", "镍矿", "稀土", "矿产", "资源", "开采", "盐湖")),
        ("材料", ("正极", "负极", "电解液", "隔膜", "锂电材料", "光伏材料", "硅料", "碳纳米")),
        ("电池", ("动力电池", "储能电池", "锂电池", "电池系统", "电芯", "BC电池", "钠电", "电池")),
        ("储能", ("储能系统", "储能", "逆变器")),
        ("光伏", ("光伏", "组件", "硅片", "电池片", "HJT", "TOPCon", "太阳能")),
        ("风电", ("风电", "风机", "叶片", "塔筒")),
        ("整车应用", ("整车", "新能源汽车", "充电桩", "换电", "底盘", "汽车零部件", "汽车")),
        ("电力运营", ("电力", "发电", "售电", "燃机", "火电", "水电", "绿电")),
    ),
    "AI": (
        ("算力硬件", ("服务器", "AI服务器", "算力", "GPU", "加速卡", "智算", "计算产业", "计算机", "PC")),
        ("光通信", ("光模块", "CPO", "光器件", "光纤", "光通信", "铜缆高速", "线缆", "通信设备", "通信")),
        ("数据中心", ("数据中心", "IDC", "机房", "液冷", "散热", "运营商网络", "网络设备")),
        ("软件应用", ("软件", "SaaS", "大模型", "AI应用", "语料", "云计算", "信息技术", "系统集成")),
        ("智能驾驶", ("智能驾驶", "自动驾驶", "激光雷达", "毫米波", "域控制器", "车联网", "汽车电子")),
        ("机器人", ("机器人", "人形机器人", "减速器", "伺服", "机器视觉", "自动化")),
        ("显示面板", ("显示", "显示器", "显示屏", "面板", "显示模组", "OLED", "LCD")),
    ),
    "医药": (
        ("创新药", ("创新药", "化学药", "生物药", "抗肿瘤", "抗体", "小分子", "制剂", "原料药")),
        ("CXO", ("CXO", "CRO", "CDMO", "CMO", "医药外包", "临床", "研发服务")),
        ("医疗器械", ("医疗器械", "医疗设备", "耗材", "IVD", "影像", "诊断")),
        ("中药", ("中药", "中成药", "饮片")),
        ("生物制品", ("疫苗", "血液制品", "基因", "细胞治疗", "胰岛素", "生物制品")),
        ("医疗服务", ("医院", "连锁药房", "医疗服务", "医美", "医药商业", "分销")),
    ),
}

FOCUS_CHAINS = tuple(CHAIN_SEGMENTS.keys())
_MIN_REVENUE_RATIO = 0.05  # 主营产品收入占比低于 5% 时降级用行业分类


@dataclass
class SegmentResult:
    symbol: str
    chain_name: str
    segment: str
    main_product: str
    revenue_ratio: float | None
    report_date: str
    source: str = "zygc"


def to_em_symbol(symbol: str) -> str:
    """6 位代码 → 东财 SH/SZ/BJ 前缀。"""
    s = symbol.strip()
    if s.upper().startswith(("SH", "SZ", "BJ")):
        return s.upper()
    s = s.zfill(6)[-6:]
    if s.startswith(("688", "689", "6")):
        return f"SH{s}"
    if s.startswith(("8", "4")):
        return f"BJ{s}"
    return f"SZ{s}"


def normalize_product(text: str) -> str:
    t = re.sub(r"\s+", "", str(text))
    t = re.sub(r"[（(].*?[）)]", "", t)
    return t


def classify_segment(chain_name: str, product_text: str) -> str:
    """规则匹配：主营产品文本 → 环节标签。"""
    text = normalize_product(product_text)
    rules = CHAIN_SEGMENTS.get(chain_name, ())
    for segment, keywords in rules:
        if any(kw in text for kw in keywords):
            return segment
    return "其他"


def _top_product_row(df: pd.DataFrame) -> tuple[str, float | None, str] | None:
    """取最新报告期按产品分类中收入占比最高项（排除「其他」）。"""
    if df is None or df.empty:
        return None
    prod = df[df["分类类型"] == "按产品分类"].copy()
    if prod.empty:
        prod = df[df["分类类型"] == "按行业分类"].copy()
    if prod.empty:
        return None
    prod = prod.sort_values("报告日期", ascending=False)
    latest_date = prod["报告日期"].iloc[0]
    latest = prod[prod["报告日期"] == latest_date]
    latest = latest[~latest["主营构成"].astype(str).str.contains("其他", na=False)]
    if latest.empty:
        latest = prod[prod["报告日期"] == latest_date]
    row = latest.sort_values("收入比例", ascending=False).iloc[0]
    ratio = float(row["收入比例"]) if pd.notna(row["收入比例"]) else None
    return str(row["主营构成"]), ratio, str(latest_date)


def fetch_zygc(symbol: str) -> pd.DataFrame | None:
    try:
        return ak.stock_zygc_em(symbol=to_em_symbol(symbol))
    except Exception:
        return None


def load_focus_universe(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """symbol → 所属聚焦链列表。"""
    rows = conn.execute(
        """SELECT csm.symbol, cm.chain_name
           FROM concept_stock_map csm
           JOIN concept_master cm ON cm.concept_id = csm.concept_id
           WHERE cm.chain_name IS NOT NULL
           ORDER BY csm.symbol"""
    ).fetchall()
    universe: dict[str, list[str]] = {}
    for sym, chain in rows:
        chain = str(chain).split("+")[0]  # 多链标注取主链
        if chain not in FOCUS_CHAINS:
            continue
        universe.setdefault(sym, [])
        if chain not in universe[sym]:
            universe[sym].append(chain)
    return universe


def segment_from_concepts(conn: sqlite3.Connection, symbol: str, chain_name: str) -> str:
    """zygc 不可用时的概念名兜底。"""
    rows = conn.execute(
        """SELECT cm.concept_name FROM concept_stock_map csm
           JOIN concept_master cm ON cm.concept_id = csm.concept_id
           WHERE csm.symbol = ? AND cm.chain_name LIKE ?""",
        (symbol, f"%{chain_name}%"),
    ).fetchall()
    for (name,) in rows:
        seg = classify_segment(chain_name, name)
        if seg != "其他":
            return seg
    return "其他"


def build_segment_for_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    chains: list[str],
    zygc_df: pd.DataFrame | None,
) -> list[SegmentResult]:
    product_info = _top_product_row(zygc_df) if zygc_df is not None else None
    results: list[SegmentResult] = []
    for chain in chains:
        if product_info:
            product, ratio, rpt_date = product_info
            if ratio is not None and ratio < _MIN_REVENUE_RATIO:
                segment = segment_from_concepts(conn, symbol, chain)
                source = "concept_fallback"
            else:
                segment = classify_segment(chain, product)
                source = "zygc"
            results.append(SegmentResult(
                symbol=symbol, chain_name=chain, segment=segment,
                main_product=product, revenue_ratio=ratio,
                report_date=rpt_date, source=source,
            ))
        else:
            segment = segment_from_concepts(conn, symbol, chain)
            results.append(SegmentResult(
                symbol=symbol, chain_name=chain, segment=segment,
                main_product="", revenue_ratio=None,
                report_date="", source="concept_fallback",
            ))
    return results


def ensure_segment_table(conn: sqlite3.Connection) -> None:
    conn.executescript(SEGMENT_DDL)
    conn.commit()


def upsert_segments(conn: sqlite3.Connection, rows: list[SegmentResult]) -> None:
    for r in rows:
        conn.execute(
            """INSERT INTO stock_chain_segment
               (symbol, chain_name, segment, main_product, revenue_ratio, report_date, source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
               ON CONFLICT(symbol, chain_name) DO UPDATE SET
                 segment=excluded.segment,
                 main_product=excluded.main_product,
                 revenue_ratio=excluded.revenue_ratio,
                 report_date=excluded.report_date,
                 source=excluded.source,
                 updated_at=excluded.updated_at""",
            (r.symbol, r.chain_name, r.segment, r.main_product, r.revenue_ratio, r.report_date, r.source),
        )


def build_chain_segments(
    conn: sqlite3.Connection,
    *,
    sleep_s: float = 0.15,
    limit: int | None = None,
    skip_existing: bool = True,
) -> dict:
    """主流程：聚焦链股票 → 主营构成 → 环节标签落库。"""
    ensure_segment_table(conn)
    universe = load_focus_universe(conn)
    symbols = sorted(universe.keys())
    if limit:
        symbols = symbols[:limit]

    stats = {
        "universe_symbols": len(universe),
        "processed": 0,
        "zygc_ok": 0,
        "zygc_fail": 0,
        "skipped": 0,
        "rows_written": 0,
    }

    for i, sym in enumerate(symbols):
        chains = universe[sym]
        if skip_existing:
            existing = conn.execute(
                "SELECT COUNT(*) FROM stock_chain_segment WHERE symbol = ?", (sym,)
            ).fetchone()[0]
            if existing >= len(chains):
                stats["skipped"] += 1
                continue

        df = fetch_zygc(sym)
        if df is not None and not df.empty:
            stats["zygc_ok"] += 1
        else:
            stats["zygc_fail"] += 1

        rows = build_segment_for_symbol(conn, sym, chains, df)
        upsert_segments(conn, rows)
        stats["processed"] += 1
        stats["rows_written"] += len(rows)

        if (i + 1) % 100 == 0:
            conn.commit()
        time.sleep(sleep_s)

    conn.commit()

    stats["segment_rows"] = conn.execute("SELECT COUNT(*) FROM stock_chain_segment").fetchone()[0]
    stats["segment_distribution"] = {
        r[0]: r[1] for r in conn.execute(
            "SELECT chain_name || '/' || segment, COUNT(*) FROM stock_chain_segment GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    }
    stats["source_distribution"] = {
        r[0]: r[1] for r in conn.execute(
            "SELECT source, COUNT(*) FROM stock_chain_segment GROUP BY source"
        ).fetchall()
    }
    return stats


def export_quality_report(conn: sqlite3.Connection) -> dict:
    """环节聚类质量评估摘要。"""
    total = conn.execute("SELECT COUNT(*) FROM stock_chain_segment").fetchone()[0]
    other = conn.execute(
        "SELECT COUNT(*) FROM stock_chain_segment WHERE segment='其他'"
    ).fetchone()[0]
    zygc = conn.execute(
        "SELECT COUNT(*) FROM stock_chain_segment WHERE source='zygc'"
    ).fetchone()[0]
    by_chain = conn.execute(
        """SELECT chain_name, segment, COUNT(*) cnt
           FROM stock_chain_segment GROUP BY chain_name, segment ORDER BY chain_name, cnt DESC"""
    ).fetchall()
    return {
        "total_rows": total,
        "other_ratio": round(other / total, 4) if total else 0,
        "zygc_coverage": round(zygc / total, 4) if total else 0,
        "by_chain_segment": [dict(r) for r in by_chain],
    }
