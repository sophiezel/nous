#!/usr/bin/env python3
"""build_supply_chain_edge.py — 边表种子层（L3）落库

用法:
  .venv/bin/python scripts/build_supply_chain_edge.py
  .venv/bin/python scripts/build_supply_chain_edge.py --add-concept-proxy

产出:
  - supply_chain_edge 表
  - docs/acceptance/supply_chain_edge_coverage.json（覆盖率/质量评估）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ["NO_PROXY"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nous.data.collectors.fetchers.supply_chain_edge import build_supply_chain_edges
from nous.data.storage import get_db


def main() -> None:
    p = argparse.ArgumentParser(description="边表种子层落库（brainmap-pandora）")
    p.add_argument(
        "--add-concept-proxy",
        action="store_true",
        help="补充概念共享度弱边代理（默认仅评估，不落库）",
    )
    p.add_argument("--proxy-limit", type=int, default=5000, help="概念代理边上限")
    args = p.parse_args()

    out_dir = PROJECT_ROOT / "docs" / "acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = get_db(write=True)
    try:
        stats = build_supply_chain_edges(
            conn,
            add_concept_proxy=args.add_concept_proxy,
            concept_proxy_limit=args.proxy_limit,
        )
        out_path = out_dir / "supply_chain_edge_coverage.json"
        out_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

        print("=== supply_chain_edge 落库完成 ===")
        for k in (
            "raw_edges", "clean_edges_brainmap", "concept_proxy_edges",
            "edge_rows", "symbols_in_edges", "focus_pool_size",
            "focus_symbols_covered", "focus_coverage_ratio", "focus_internal_edges",
        ):
            print(f"  {k}: {stats.get(k)}")
        print(f"  relation: {stats.get('relation_distribution')}")
        print(f"  评估报告: {out_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
