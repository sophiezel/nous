# 环节标签层聚类口径说明（L2）

> 对应票：[环节标签层落地](https://github.com/sophiezel/nous/issues/18)
> 实现：`src/nous/data/collectors/fetchers/chain_segment.py`

## 1. 股票池

从 `concept_master` + `concept_stock_map` 取 **聚焦链**（半导体, 新能源, AI, 医药）成分股并集。
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
| `_MIN_REVENUE_RATIO` | 0.05 | 主营产品收入占比 < 5% 时改用概念兜底 |
| 报告期 | 最新 | 按 `报告日期` 降序取最近一期 |
| 产品过滤 | 排除「其他」 | 优先非「其他」产品行 |

## 5. 落库 schema

`stock_chain_segment(symbol, chain_name, segment, main_product, revenue_ratio, report_date, source)`

## 6. 环节词典（按链）

```json
{
  "半导体": [
    "设计",
    "制造",
    "封测",
    "设备",
    "材料"
  ],
  "新能源": [
    "上游资源",
    "材料",
    "电池",
    "储能",
    "光伏",
    "风电",
    "整车应用",
    "电力运营"
  ],
  "AI": [
    "算力硬件",
    "光通信",
    "数据中心",
    "软件应用",
    "智能驾驶",
    "机器人",
    "显示面板"
  ],
  "医药": [
    "创新药",
    "CXO",
    "医疗器械",
    "中药",
    "生物制品",
    "医疗服务"
  ]
}
```

## 7. 质量评估

运行脚本后见 `docs/acceptance/chain_segment_quality.json`：
- `zygc_coverage`：主营构成命中率
- `other_ratio`：`其他` 环节占比（越低越好）
- `by_chain_segment`：各链环节分布
