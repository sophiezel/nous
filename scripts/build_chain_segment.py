#!/usr/bin/env python3
"""build_chain_segment.py — 环节标签层（L2）落库

用法:
  .venv/bin/python scripts/build_chain_segment.py
  .venv/bin/python scripts/build_chain_segment.py --limit 20     # 冒烟
  .venv/bin/python scripts/build_chain_segment.py --no-skip    # 全量重跑

产出:
  - stock_chain_segment 表
  - docs/acceptance/chain_segment_methodology.md（口径说明）
  - docs/acceptance/chain_segment_quality.json（质量评估）
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

from nous.data.collectors.fetchers.chain_segment import (
    CHAIN_SEGMENTS,
    FOCUS_CHAINS,
    _MIN_REVENUE_RATIO,
    build_chain_segments,
    export_quality_report,
)
from nous.data.storage import get_db

METHODOLOGY = f"""# 环节标签层聚类口径说明（L2）

> 对应票：[环节标签层落地](https://github.com/sophiezel/nous/issues/18)
> 实现：`src/nous/data/collectors/fetchers/chain_segment.py`

## 1. 股票池

从 `concept_master` + `concept_stock_map` 取 **聚焦链**（{', '.join(FOCUS_CHAINS)}）成分股并集。
多链归属时按 `chain_name` 主链（`+` 分隔取首项）分别打标。

## 2. 数据源

- **主源**：东财 F10 `stock_zygc_em`（`按产品分类`，取最新报告期收入占比最高项）
- **兜底**：同链概念名关键词匹配（`source=concept_fallback`）

## 3. 聚类方法

**规则词典匹配**（非 ML 聚类）：

对每条聚焦链预定义环节 → 关键词表（见 `CHAIN_SEGMENTS`）。
主营产品文本归一化（去空格、去括号补充说明）后，**首个命中关键词**的环节即为标签；
未命中 → `其他`。

## 4. 阈值

| 参数 | 值 | 说明 |
|------|-----|------|
| `_MIN_REVENUE_RATIO` | {_MIN_REVENUE_RATIO} | 主营产品收入占比 < 5% 时改用概念兜底 |
| 报告期 | 最新 | 按 `报告日期` 降序取最近一期 |
| 产品过滤 | 排除「其他」 | 优先非「其他」产品行 |

## 5. 落库 schema

`stock_chain_segment(symbol, chain_name, segment, main_product, revenue_ratio, report_date, source)`

## 6. 环节词典（按链）

```json
{json.dumps({k: [s[0] for s in v] for k, v in CHAIN_SEGMENTS.items()}, ensure_ascii=False, indent=2)}
```

## 7. 质量评估

运行脚本后见 `docs/acceptance/chain_segment_quality.json`：
- `zygc_coverage`：主营构成命中率
- `other_ratio`：`其他` 环节占比（越低越好）
- `by_chain_segment`：各链环节分布
"""


def main() -> None:
    p = argparse.ArgumentParser(description="环节标签层落库（主营构成聚类）")
    p.add_argument("--limit", type=int, default=None, help="仅处理前 N 只股票（测试）")
    p.add_argument("--sleep", type=float, default=0.15, help="请求间隔秒")
    p.add_argument("--no-skip", action="store_true", help="不跳过已落库股票")
    args = p.parse_args()

    out_dir = PROJECT_ROOT / "docs" / "acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = get_db(write=True)
    try:
        stats = build_chain_segments(
            conn,
            sleep_s=args.sleep,
            limit=args.limit,
            skip_existing=not args.no_skip,
        )
        quality = export_quality_report(conn)

        (out_dir / "chain_segment_methodology.md").write_text(METHODOLOGY, encoding="utf-8")
        (out_dir / "chain_segment_quality.json").write_text(
            json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print("=== stock_chain_segment 落库完成 ===")
        for k, v in stats.items():
            if k not in ("segment_distribution", "source_distribution"):
                print(f"  {k}: {v}")
        print(f"  zygc_coverage: {quality['zygc_coverage']}")
        print(f"  other_ratio: {quality['other_ratio']}")
        print(f"  口径说明: {out_dir / 'chain_segment_methodology.md'}")
        print(f"  质量报告: {out_dir / 'chain_segment_quality.json'}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
