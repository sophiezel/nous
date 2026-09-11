---
name: 供应链位置得分 — 因子定义与验证协议
overview: 决议来源 ticket #20（grilling Round 1，2026-08-26）。在 A2 超跌族模型上叠加供应链位置加分项，OOS 双窗口验证，未过即拒。
todos:
  - id: implement
    content: 按本规格在 #21 实现因子 + run_acceptance_rebound A/B
    status: pending
---

# 供应链位置得分 — 因子定义与验证协议（决议规格）

> 决议来源：[供应链位置得分设计](https://github.com/sophiezel/nous/issues/20)，grilling Round 1 确认（Q1:A, Q2:推荐+下游更高, Q3:B, Q4:接受）。
> 数据依赖：`concept_master` / `concept_stock_map`（L1）、`stock_chain_segment`（L2）、`supply_chain_edge`（L3）。

## 1. 因子形态（Q4 / 地图 Q4）

**供应链位置得分** = 链强度门控 × 节点位置分 → 转为 **加分项** 叠加到超跌族 `base_score`（不改现有 100 分权重结构）。

```
final_score = base_score + bonus
bonus = clip(chain_strength_norm × position_score × 10, 0, 10)   # 仅当 chain_strength > 0
```

- `base_score`：现有 `oversold` 族加权分（0–100）
- `bonus`：0–10 分；透明可解释，写入 `score_detail`

## 2. 链强度（Q1:A）

**定义**：候选股所属聚焦链内，全部成分股（`concept_stock_map` ∩ `chain_name`）的 **5 日收益中位数**（PIT 日线，信号日 T 收盘后可见）。

```python
chain_strength(chain, date) = median(ret_5d(s) for s in chain_members(chain, date))
```

**门控**（Q1 推荐）：

- `chain_strength <= 0` → `bonus = 0`（链内整体走弱，不做供应链加分）
- `chain_strength > 0` → 计算 `chain_strength_norm = clip(chain_strength / 0.05, 0, 1)`（链内中位涨 5% 视为满强度）

**多链归属**：取 **环节类概念数最多** 的链；并列时优先级 AI > 半导体 > 新能源 > 医药。

## 3. 节点位置分（Q2: 推荐 + 下游更高）

```
position_score = 0.5 × P1 + 0.3 × P2 + 0.2 × P3
```

| 分量 | 来源 | 计算 |
|------|------|------|
| **P1** 环节 ordinal | `stock_chain_segment.segment` | 链内预设表（§3.1）；「其他」= 0.5 |
| **P2** 概念成员度 | `concept_stock_map` 环节类概念数 | min-max 归一化到 [0,1]（链内截面） |
| **P3** 边中心度 | `supply_chain_edge` | 有边：`(out_degree - in_degree + max_deg) / (2×max_deg)`；无边：0.5 |

**下游更高**：ordinal 越大表示越靠近链的下游/应用端（反弹辐射受益假设）。

### 3.1 环节 ordinal 表（0=上游, 1=下游）

**半导体**

| segment | ordinal |
|---------|---------|
| 材料 | 0.20 |
| 设备 | 0.35 |
| 制造 | 0.60 |
| 封测 | 0.80 |
| 设计 | 0.90 |
| 其他 | 0.50 |

**新能源**

| segment | ordinal |
|---------|---------|
| 上游资源 | 0.10 |
| 材料 | 0.30 |
| 电池 / 光伏 / 风电 | 0.50 |
| 储能 | 0.70 |
| 电力运营 | 0.75 |
| 整车应用 | 0.90 |
| 其他 | 0.50 |

**AI**

| segment | ordinal |
|---------|---------|
| 算力硬件 | 0.40 |
| 光通信 | 0.50 |
| 数据中心 | 0.60 |
| 显示面板 | 0.75 |
| 机器人 / 智能驾驶 | 0.80 |
| 软件应用 | 0.90 |
| 其他 | 0.50 |

**医药**

| segment | ordinal |
|---------|---------|
| CXO | 0.30 |
| 中药 | 0.55 |
| 创新药 / 生物制品 | 0.70 |
| 医疗器械 | 0.80 |
| 医疗服务 | 0.90 |
| 其他 | 0.50 |

## 4. 融合方式（Q3:B）

- **加分项**，不修改 `config/rebound_weights.yaml` 现有因子权重
- 仅作用于 **超跌族**（`oversold`）；反包族保持关闭（A2 配置）
- `bonus` 在 `_family_scores` 之后、`min_score` 门槛之前叠加

## 5. 验证协议（Q4: 接受）

沿用 `docs/superpowers/specs/2026-08-24-rebound-backtest-acceptance-design.md` 全部硬门槛（A1–A6）。

| 项 | 规格 |
|----|------|
| 窗口 | 校准 2020–2023 / OOS 2024–2026 |
| 对照 | **A2 基线** vs **A2 + supply_chain bonus** |
| 通过 | OOS 双窗口 Sharpe、PF、WR **均不低于** A2 基线（或差异不显著劣化）；期望 > 0 |
| 拒绝 | 任一双窗口子期崩溃 / 任一硬门槛 FAIL → 拒绝并入引擎 |
| 前视 | 边表为当前快照近似（`integrity_flags.APPROX_EDGE` 警告，不硬 FAIL）；概念/环节/日线 PIT |
| 报告 | `docs/acceptance/supply_chain_position_oos_YYYYMMDD.md` |

## 6. 数据 Caveat（实现 #21 须记录）

1. brainmap 边覆盖聚焦链仅 **9.5%** → P3 对大部分股票为 0.5 中性
2. 环节标签 66% 为「其他」→ P1 大量中性；因子边际可能来自 P2 概念成员度 + 链强度门控
3. 边表无历史 PIT → 回测用当前边近似，报告须披露

## 7. #21 实现清单

- [ ] `engine/signals/supply_chain_position.py`（链强度 + 位置分 + bonus）
- [ ] 接入 `rebound.py` oversold 路径
- [ ] `run_acceptance_rebound.py` 增加 A/B 模式
- [ ] OOS 报告 + 通过/拒绝结论
