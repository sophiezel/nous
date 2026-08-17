# Provider DAG — SLA 声明式生产链

**状态**：已批准实施实施  
**日期**：2026-07-23  
**范围**：Features → Factors → Assert → Products/Consume → Observe；早间 Assert+补产

## 问题

调度有断言无生产：因子等资产只验不产；散落 cron 无阶段序，与荐股竞态。

## 方案

在 `sla_registry` 挂 `PRODUCERS`；`pipeline_dag` 串行执行：

| 阶段 | 动作 |
|------|------|
| S1 Features | `collect_*` 全域采集 + cross/gap |
| S2 Factors | `factor_compute` 日更增量合并 → latest + dated snapshot |
| S3 Assert | `data_assert --consumer recommend`（不含荐股产物） |
| S4 Consume | theme 池（若有）+ daily-recommend + review |
| S5 Observe | health / quality-report |
| S0 Morning | assert → remediable 补产一轮 → 再 assert |

Cron：`post-close-chain`、`morning-chain`；原散落 ETL/assert/recommend/factor-freshness 并入或降为别名。

## 失败

- P0：阻断下游，写 `chain_status.json`
- P1：DEGRADED 继续
- 早间补产最多 1 轮

## 非目标

不引入 Airflow；不改 SLA 数值阈值；港股因子后挂。
