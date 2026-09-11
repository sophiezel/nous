#!/usr/bin/env python3
"""build_concept_master.py — 概念节点层（L1）落库

用法:
  .venv/bin/python scripts/build_concept_master.py
  .venv/bin/python scripts/build_concept_master.py --no-constituents   # 仅元数据
  .venv/bin/python scripts/build_concept_master.py --all-pages         # 尝试 THS 全分页（生产）
  .venv/bin/python scripts/build_concept_master.py --limit 10          # 冒烟测试

产出:
  - concept_master 表（375 THS + 70 EM 合并）
  - concept_stock_map 表（成分股映射）
  - docs/acceptance/concept_master_segment_list.json（环节类清单）
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

from nous.data.collectors.fetchers.concept_ths import build_concept_master, export_segment_report
from nous.data.storage import get_db


def main() -> None:
    p = argparse.ArgumentParser(description="概念节点层落库（同花顺375 + EM合并）")
    p.add_argument("--no-constituents", action="store_true", help="跳过成分股抓取（仅元数据）")
    p.add_argument("--all-pages", action="store_true", help="尝试 THS ajax 全分页（生产环境）")
    p.add_argument("--limit", type=int, default=None, help="仅处理前 N 个 THS 概念（测试）")
    p.add_argument("--sleep", type=float, default=0.3, help="请求间隔秒")
    args = p.parse_args()

    conn = get_db(write=True)
    try:
        stats = build_concept_master(
            conn,
            fetch_constituents=not args.no_constituents,
            fetch_all_pages=args.all_pages,
            sleep_s=args.sleep,
            limit=args.limit,
        )
        segments = export_segment_report(conn)

        out_dir = PROJECT_ROOT / "docs" / "acceptance"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "concept_master_segment_list.json"
        out_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")

        print("=== concept_master 落库完成 ===")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print(f"  环节类清单: {out_path} ({len(segments)} 条)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
