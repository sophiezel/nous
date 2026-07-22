# 数据鲜度断言报告 — 2026-07-17

**综合裁决：通过**  
上一交易日：`2026-07-16`  |  consumer=`all`  |  domain=`all`  
P0=通过  P1=通过  
DEGRADED: 无  
耗时：0.59s

| 优先级 | 资产 | 轨道 | 裁决 | 详情 |
|--------|------|------|------|------|
| P0 | A股日线 (`stock_daily_a`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤1), 覆盖=100%/80% |
| P0 | 股票基础信息 (`stock_basic`) | existence | 通过 | 行数=6256 |
| P1 | 基本面快照 (`stock_fundamental`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤2) |
| P1 | 指数日线 (`index_daily`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤1) |
| P2 | 全球指数 (`index_global_daily`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤2) |
| P2 | 期货日线 (`futures_daily`) | freshness | 通过 | 最新=2026-07-15, 交易日滞后=1 (阈值≤1) |
| P2 | 期指基差 (`futures_basis`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤1) |
| P2 | 市场情绪 (`sentiment_cache`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤1) |
| P1 | 沪深港通市场 (`hsgt_market_daily`) | freshness | 通过 | 最新=2026-07-15, 交易日滞后=1 (阈值≤1) |
| P1 | 沪深港通个股 (`hsgt_stock_daily`) | freshness | 通过 | 最新=2026-07-15, 交易日滞后=1 (阈值≤3) |
| P2 | 个股资金流向 (`fund_flow_stock`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤2) |
| P2 | 融资融券 (`margin_daily`) | freshness | 通过 | 最新=2026-07-15, 交易日滞后=1 (阈值≤2) |
| P2 | ETF资金流 (`etf_flow_daily`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤2) |
| P2 | 大宗交易 (`block_trades`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤2) |
| P2 | 龙虎榜 (`lhb_daily`) | freshness | 通过 | 最新=2026-07-15, 交易日滞后=1 (阈值≤2) |
| P0 | A股因子 latest (`factors_latest`) | freshness | 通过 | latest.parquet 行数=829476 (阈值≥500), as_of=2026-07-16, 交易日滞后=0, K列=60 |
| P0 | A股因子 dated snapshot (`factors_snapshot`) | freshness | 通过 | 2026-07-16.parquet 行数=829476 (阈值≥500), K列=60 |
| P1 | LightGBM 模型 (`models_lgb`) | freshness | 通过 | lgb_2026-07-17.pkl 年龄=0.0d (阈值≤14d), 共1个 |
| P0 | 筛选结果 (`screen_results`) | freshness | 通过 | 最新=2026-07-16, 交易日滞后=0 (阈值≤1) |
| P0 | 龙脉主题池 (`theme_auto_pools`) | freshness | 通过 | 最新=2026-07-17, 交易日滞后=0 (阈值≤1) |
| P0 | 日线完整性抽样 (`integrity_ohlcv`) | integrity | 通过 | 2026-07-16: 行=5201, 无效OHLC=0 (0.0%) |

## 读数

- P0 失败 → 阻断荐股/筛选/交易相关消费。
- P1 失败 → 短池/ML 降级；模型缺失标记 DEGRADED。
- P2 失败 → 信号可中性降级，报告标黄。

对标：Qlib CalendarProvider + check_data_health；Lean Consumer Contract。
