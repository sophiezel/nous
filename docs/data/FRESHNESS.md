# 数据鲜度契约（FRESHNESS）

对标：Qlib CalendarProvider / check_data_health、Zipline bundle、Lean Consumer Contract。  
方法移植，非整库依赖。设计存档：[`docs/superpowers/specs/2026-07-17-data-freshness-design.md`](../superpowers/specs/2026-07-17-data-freshness-design.md)

## 命令

```bash
nous data freshness              # 全表扫描着色
nous data health                 # 对齐 SLA registry
nous data assert                 # Freshness+Integrity；P0 失败 exit 1
nous data assert --domain capital
nous data assert --consumer recommend|trl|review|backtest
```

报告目录：`docs/data/freshness/<日期>/FRESHNESS_REPORT.md`

## 开源对标矩阵

| 来源 | 蒸馏点 |
|------|--------|
| Qlib | 交易日 lag；健康=缺值/大跳变/必备列 |
| Zipline | 因子版本化 `snapshots/{date}.parquet` + `latest.meta.json` |
| Lean | ConsumerContract 按消费者声明依赖 |
| Alphalens | 因子 as_of 与价格日历对齐 |
| López de Prado | 未收盘特征不得进训练/荐股（与 embargo 一致） |

## Provider 分层

Calendar → Instruments(`stock_basic`) → Features(日线/资金/宏观) → Factors(parquet)

## Consumer 依赖

| Consumer | 硬依赖（摘要） |
|----------|----------------|
| recommend | stock_daily / fundamental / index / factors |
| trl | stock_daily / theme_auto_pools |
| review | index / stock_daily；（margin/hsgt/basis 可选） |
| backtest | stock_daily / stock_basic；（factors 可选，缺失须标 FALLBACK_MOMENTUM） |

## 生产节奏（Update → Assert → Consume）

| 时间 | Job |
|------|-----|
| 16:30–16:45 | ETL |
| 16:44 | cross-validate |
| 16:50 | gap-detector + **data-assert** |
| 16:55 | post-update-verify(afternoon) + **daily-recommend** |
| 17:05–17:10 | health-dashboard / quality-report / factor-freshness |
| 次日 08:30 | data-assert-am |
| 09:30 | preflight |

## 补数优先级（首轮 assert 后）

见同目录 `MISSING_BACKLOG.md`（由 `nous data assert` 生成报告后人工整理）。
