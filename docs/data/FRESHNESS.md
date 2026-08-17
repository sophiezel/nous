# 数据鲜度契约（FRESHNESS）

对标：Qlib CalendarProvider / check_data_health、Zipline bundle、Lean Consumer Contract。  
方法移植，非整库依赖。

设计存档：
- [`docs/superpowers/specs/2026-07-17-data-freshness-design.md`](../superpowers/specs/2026-07-17-data-freshness-design.md)
- [`docs/superpowers/specs/2026-07-23-provider-dag-design.md`](../superpowers/specs/2026-07-23-provider-dag-design.md) — **Provider DAG（现行）**

## 命令

```bash
nous data freshness              # 全表扫描着色
nous data health                 # 对齐 SLA registry
nous data assert                 # Freshness+Integrity；P0 失败 exit 1
nous data assert --domain capital
nous data assert --consumer recommend|trl|review|backtest

# Provider DAG（生产主路径）
nous data chain --chain status
nous data chain --chain post-close   # S1→S5 收盘全链路
nous data chain --chain morning      # 早间断言 + remediable 补产
nous data chain --chain S2           # 单跑因子日更
```

报告目录：`docs/data/freshness/<日期>/`（含 `FRESHNESS_REPORT.md`、`chain_status.json`）  
运行时状态：`~/nous-data/logs/chain_status.json`

## 开源对标矩阵

| 来源 | 蒸馏点 |
|------|--------|
| Qlib | 交易日 lag；健康=缺值/大跳变/必备列 |
| Zipline | 因子版本化 `snapshots/{date}.parquet` + `latest.meta.json` |
| Lean | ConsumerContract 按消费者声明依赖 |
| Alphalens | 因子 as_of 与价格日历对齐 |
| López de Prado | 未收盘特征不得进训练/荐股（与 embargo 一致） |

## Provider 分层

Calendar → Instruments(`stock_basic`) → Features(日线/资金/宏观) → Factors(parquet) → Products(荐股)

每个 `AssetSLA` 声明 `produce_stage` + `remediable`；生产函数在 `nous.data.quality.producers`。

## Consumer 依赖

| Consumer | 硬依赖（摘要） |
|----------|----------------|
| recommend | stock_daily / fundamental / index / factors |
| trl | stock_daily / theme_auto_pools |
| review | index / stock_daily；（margin/hsgt/basis 可选） |
| backtest | stock_daily / stock_basic；（factors 可选，缺失须标 FALLBACK_MOMENTUM） |

## 生产节奏（Provider DAG）

| 时间 | Job | 阶段 |
|------|-----|------|
| 16:40 | **post-close-chain** | S1 Features → S2 Factors(日更增量) → S3 Assert(recommend) → S4 Consume → S5 Observe |
| 08:30 | **morning-chain** | Assert → remediable 补产一轮 → 再 Assert |
| 09:30 | preflight | 开盘门禁 |
| 周日 02:00 / 02:30 | weekly-train / factor-full-recompute | 模型 + 因子全量 |

P0 失败阻断 S4；P1 标 DEGRADED；早间补产最多 1 轮。

## 补数优先级

见同目录 `MISSING_BACKLOG.md`（由 `nous data assert` / chain 报告后整理）。
