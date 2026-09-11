# 数据鲜度断言报告 — 2026-09-09

**综合裁决：通过**  
上一交易日：`2026-09-08`  |  consumer=`recommend`  |  domain=`all`  
P0=通过  P1=通过  
DEGRADED: 无  
耗时：122.82s

| 优先级 | 资产 | 轨道 | 裁决 | 详情 |
|--------|------|------|------|------|
| P0 | A股日线 (`stock_daily_a`) | freshness | 通过 | 最新=2026-09-09, 交易日滞后=0 (阈值≤1), 覆盖=100%/80% |
| P0 | 股票基础信息 (`stock_basic`) | existence | 通过 | 行数=6641 |
| P1 | 基本面快照 (`stock_fundamental`) | freshness | 通过 | 最新=2026-09-09, 交易日滞后=0 (阈值≤2) |
| P1 | 指数日线 (`index_daily`) | freshness | 通过 | 最新=2026-09-08, 交易日滞后=0 (阈值≤1) |
| P1 | 沪深港通个股 (`hsgt_stock_daily`) | freshness | 通过 | 最新=2026-09-08, 交易日滞后=0 (阈值≤3) |
| P0 | A股因子 latest (`factors_latest`) | freshness | 通过 | latest.parquet 行数=10041313 (阈值≥500), as_of=2026-09-09, 交易日滞后=0, K列=60 |
| P0 | A股因子 dated snapshot (`factors_snapshot`) | freshness | 通过 | 2026-09-08.parquet 行数=10036002 (阈值≥500), K列=60 |
| P1 | LightGBM 模型 (`models_lgb`) | freshness | 通过 | lgb_2026-09-07.pkl 年龄=2.6d (阈值≤14d), 共4个 |
| P0 | 日线完整性抽样 (`integrity_ohlcv`) | integrity | 通过 | 2026-09-08: 行=5311, 无效OHLC=0 (0.0%) |

## 读数

- P0 失败 → 阻断荐股/筛选/交易相关消费。
- P1 失败 → 短池/ML 降级；模型缺失标记 DEGRADED。
- P2 失败 → 信号可中性降级，报告标黄。

对标：Qlib CalendarProvider + check_data_health；Lean Consumer Contract。
