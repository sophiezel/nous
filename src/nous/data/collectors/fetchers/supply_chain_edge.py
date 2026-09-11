"""brainmap-pandora 边表种子层（L3）— 股票级上下游有向边落库。

下载 GitHub HTML → 抽取 window.D JSON → 清洗（归一关系/去反向重复/代码对齐）→
落库 supply_chain_edge + 覆盖率评估。
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

from nous.data.collectors.fetchers.base import clean_session

BRAINMAP_URLS = (
    "https://cdn.jsdelivr.net/gh/ciuyity-lgtm/brainmap-pandora@main/"
    "P01-brainmap-pandora/brainmap_pandora_v5.html",
    "https://raw.githubusercontent.com/ciuyity-lgtm/brainmap-pandora/main/"
    "P01-brainmap-pandora/brainmap_pandora_v5.html",
)
CACHE_PATH = Path.home() / "nous-data" / "cache" / "brainmap_pandora_v5.html"
SNAPSHOT_DATE = "2026-06-15"

EDGE_DDL = """
CREATE TABLE IF NOT EXISTS supply_chain_edge (
    src_symbol  TEXT NOT NULL,
    dst_symbol  TEXT NOT NULL,
    relation    TEXT NOT NULL CHECK(relation IN ('supplier','customer','competitor')),
    weight      REAL DEFAULT 1.0,
    source      TEXT DEFAULT 'brainmap',
    updated_at  TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (src_symbol, dst_symbol, relation)
);
CREATE INDEX IF NOT EXISTS idx_sce_src ON supply_chain_edge(src_symbol);
CREATE INDEX IF NOT EXISTS idx_sce_dst ON supply_chain_edge(dst_symbol);
"""

PRIMARY_RELATIONS = frozenset({"supplier", "customer", "competitor"})


@dataclass
class RawEdge:
    src: str
    dst: str
    relation: str


@dataclass
class CleanEdge:
    src_symbol: str
    dst_symbol: str
    relation: str
    weight: float = 1.0
    source: str = "brainmap"


def normalize_symbol(code: str) -> str | None:
    s = re.sub(r"\D", "", str(code))[-6:].zfill(6)
    if not re.fullmatch(r"\d{6}", s):
        return None
    return s


def download_brainmap_html(cache_path: Path = CACHE_PATH) -> str:
    """下载 brainmap HTML（jsdelivr 优先，落本地缓存）。"""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and cache_path.stat().st_size > 100_000:
        return cache_path.read_text(encoding="utf-8", errors="replace")

    session = clean_session()
    last_err: Exception | None = None
    for url in BRAINMAP_URLS:
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            cache_path.write_text(r.text, encoding="utf-8")
            return r.text
        except Exception as e:
            last_err = e
    raise RuntimeError(f"brainmap 下载失败: {last_err}")


def extract_brainmap_json(html: str) -> dict:
    """从 HTML 抽取 window.D JSON（平衡括号）。"""
    marker = "window.D"
    idx = html.find(marker)
    if idx < 0:
        raise ValueError("window.D not found in brainmap HTML")
    start = html.find("{", idx)
    depth = 0
    end = start
    for i, ch in enumerate(html[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return json.loads(html[start:end])


def expand_relations(relation: str) -> list[str]:
    """组合 relation 拆分为 supplier/customer/competitor。"""
    parts = [p.strip() for p in str(relation).split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        if p in PRIMARY_RELATIONS:
            out.append(p)
        elif p == "upstream":
            out.append("supplier")
        elif p == "downstream":
            out.append("customer")
    return out or ["competitor"]


def parse_raw_edges(data: dict) -> list[RawEdge]:
    edges: list[RawEdge] = []
    for e in data.get("edges", []):
        src = normalize_symbol(e.get("source", ""))
        dst = normalize_symbol(e.get("target", ""))
        if not src or not dst or src == dst:
            continue
        for rel in expand_relations(e.get("relation", "competitor")):
            edges.append(RawEdge(src=src, dst=dst, relation=rel))
    return edges


def _supplies_pair(src: str, dst: str, relation: str) -> tuple[str, str] | None:
    """统一为 (supplier, customer) 供货方向；competitor 返回 None。"""
    if relation == "supplier":
        # target 是 source 的供应商 → supplier=dst, customer=src
        return dst, src
    if relation == "customer":
        # target 是 source 的客户 → supplier=src, customer=dst
        return src, dst
    return None


def dedupe_edges(raw_edges: Iterable[RawEdge]) -> list[CleanEdge]:
    """去反向重复：A--supplier-->B 与 B--customer-->A 保留一条。"""
    supplies_seen: set[tuple[str, str]] = set()
    competitor_seen: set[tuple[str, str]] = set()
    clean: list[CleanEdge] = []

    for e in raw_edges:
        if e.relation == "competitor":
            key = tuple(sorted((e.src, e.dst)))
            if key in competitor_seen:
                continue
            competitor_seen.add(key)
            clean.append(CleanEdge(e.src, e.dst, "competitor"))
            continue

        pair = _supplies_pair(e.src, e.dst, e.relation)
        if pair is None:
            continue
        if pair in supplies_seen:
            continue
        supplies_seen.add(pair)
        supplier, customer = pair
        # 落库保留 brainmap 原始语义：supplier 边表示 target 是 source 的供应商
        clean.append(CleanEdge(customer, supplier, "supplier"))
        # 不再写入反向 customer 边

    return clean


def remove_isolated(edges: list[CleanEdge]) -> list[CleanEdge]:
    """剔除孤立节点相关边：仅保留连通节点（度≥1）上的边。"""
    degree: Counter[str] = Counter()
    for e in edges:
        degree[e.src_symbol] += 1
        degree[e.dst_symbol] += 1
    return [e for e in edges if degree[e.src_symbol] > 0 and degree[e.dst_symbol] > 0]


def load_focus_symbols(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """SELECT DISTINCT csm.symbol
           FROM concept_stock_map csm
           JOIN concept_master cm ON cm.concept_id = csm.concept_id
           WHERE cm.chain_name IS NOT NULL"""
    ).fetchall()
    return {r[0] for r in rows}


def concept_sharedness_proxy(
    conn: sqlite3.Connection,
    existing_pairs: set[tuple[str, str]],
    *,
    min_shared: int = 2,
    max_pairs: int = 5000,
) -> list[CleanEdge]:
    """概念共享度弱边代理：同概念≥min_shared 且无 brainmap 边的股票对。"""
    rows = conn.execute(
        """SELECT csm.symbol, cm.concept_id
           FROM concept_stock_map csm
           JOIN concept_master cm ON cm.concept_id = csm.concept_id
           WHERE cm.category = '环节'"""
    ).fetchall()
    concept_to_symbols: dict[str, list[str]] = defaultdict(list)
    for sym, cid in rows:
        concept_to_symbols[cid].append(sym)

    proxy: list[CleanEdge] = []
    for symbols in concept_to_symbols.values():
        if len(symbols) < min_shared:
            continue
        uniq = sorted(set(symbols))
        for i, a in enumerate(uniq):
            for b in uniq[i + 1 :]:
                key = tuple(sorted((a, b)))
                if key in existing_pairs:
                    continue
                proxy.append(CleanEdge(a, b, "competitor", weight=0.3, source="concept_proxy"))
                existing_pairs.add(key)
                if len(proxy) >= max_pairs:
                    return proxy
    return proxy


def ensure_edge_table(conn: sqlite3.Connection) -> None:
    conn.executescript(EDGE_DDL)
    conn.commit()


def upsert_edges(conn: sqlite3.Connection, edges: Iterable[CleanEdge]) -> int:
    n = 0
    for e in edges:
        conn.execute(
            """INSERT INTO supply_chain_edge (src_symbol, dst_symbol, relation, weight, source, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
               ON CONFLICT(src_symbol, dst_symbol, relation) DO UPDATE SET
                 weight=excluded.weight,
                 source=excluded.source,
                 updated_at=excluded.updated_at""",
            (e.src_symbol, e.dst_symbol, e.relation, e.weight, e.source),
        )
        n += 1
    return n


def build_supply_chain_edges(
    conn: sqlite3.Connection,
    *,
    add_concept_proxy: bool = False,
    concept_proxy_limit: int = 5000,
) -> dict:
    ensure_edge_table(conn)
    html = download_brainmap_html()
    data = extract_brainmap_json(html)
    raw = parse_raw_edges(data)
    clean = dedupe_edges(raw)
    clean = remove_isolated(clean)

    n_brainmap = upsert_edges(conn, clean)

    proxy_count = 0
    if add_concept_proxy:
        existing = {tuple(sorted((e.src_symbol, e.dst_symbol))) for e in clean if e.relation == "competitor"}
        existing.update(
            tuple(sorted((e.src_symbol, e.dst_symbol)))
            for e in clean
            if e.relation in ("supplier", "customer")
        )
        proxy = concept_sharedness_proxy(conn, existing, max_pairs=concept_proxy_limit)
        proxy_count = upsert_edges(conn, proxy)

    conn.commit()
    stats = evaluate_coverage(conn, data)
    stats.update({
        "raw_edges": len(raw),
        "clean_edges_brainmap": n_brainmap,
        "concept_proxy_edges": proxy_count,
        "snapshot_date": SNAPSHOT_DATE,
        "nodes_in_source": len(data.get("nodes", [])),
    })
    return stats


def evaluate_coverage(conn: sqlite3.Connection, data: dict) -> dict:
    """边覆盖率与质量评估。"""
    focus = load_focus_symbols(conn)
    edge_rows = conn.execute(
        "SELECT src_symbol, dst_symbol, relation, source FROM supply_chain_edge"
    ).fetchall()

    symbols_in_edges: set[str] = set()
    focus_edges = 0
    rel_counter: Counter[str] = Counter()
    src_counter: Counter[str] = Counter()
    for src, dst, rel, source in edge_rows:
        symbols_in_edges.update((src, dst))
        rel_counter[rel] += 1
        src_counter[source] += 1
        if src in focus and dst in focus:
            focus_edges += 1

    nodes = data.get("nodes", [])
    industries = Counter(n.get("industry", "") for n in nodes)
    unlabeled = sum(1 for n in nodes if n.get("industry") in ("待分类", "待分析", "", None))

    focus_covered = len(focus & symbols_in_edges)
    brainmap_symbols = {normalize_symbol(n.get("id", "")) for n in nodes}
    brainmap_symbols.discard(None)

    return {
        "edge_rows": len(edge_rows),
        "symbols_in_edges": len(symbols_in_edges),
        "focus_pool_size": len(focus),
        "focus_symbols_covered": focus_covered,
        "focus_coverage_ratio": round(focus_covered / len(focus), 4) if focus else 0,
        "focus_internal_edges": focus_edges,
        "relation_distribution": dict(rel_counter),
        "source_distribution": dict(src_counter),
        "brainmap_nodes": len(nodes),
        "brainmap_unlabeled_industry": unlabeled,
        "brainmap_unlabeled_ratio": round(unlabeled / len(nodes), 4) if nodes else 0,
        "isolated_nodes_removed": len(brainmap_symbols) - len(symbols_in_edges & brainmap_symbols),
        "quality_risks": {
            "copyright": "README 声明数据自有版权，与 MIT 冲突；仅作研究种子，商用需谨慎",
            "snapshot": f"单快照 {SNAPSHOT_DATE}，无历史/PIT",
            "coverage": "~10% A股，偏科技/新能源链",
            "noise": "存在可疑 competitor 边，需因子验证阶段过滤",
        },
        "concept_proxy_recommendation": (
            "概念共享度可作弱边代理（competitor, weight=0.3），"
            "补充 brainmap 未覆盖的聚焦链股票对；默认不落库，--add-concept-proxy 启用"
        ),
    }
