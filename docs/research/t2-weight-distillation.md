# 开源量化模型权重体系蒸馏 — 短期反弹选股引擎（t2）

> Ticket: 开源量化模型权重体系蒸馏（https://github.com/sophiezel/nous/issues/3）
> 用途：为 nous 短期反弹选股引擎（1–20 日波段，超跌反弹 / 情绪修复场景）提供"业内专业权重体系草案"
> 产出日期：2026-08-24
> 调研方式：curl/bash 直连一手来源（GitHub 源码 / 官方 README / 本地仓库代码）

---

## 0. 结论速览（TL;DR）

1. **开源量化圈没有"统一权威权重"**，只有两类可复用的东西：(a) **因子族/因子表达式**（qlib Alpha158、Barra CNE5/CNE6 风格因子），(b) **权重学习方法**（ICIR 加权、风险平价、Lasso/ElasticNet 回归、IC 加权）。权重本身必须由模型/回测学出来，不能拍脑袋硬编码——但可以拿一个**有依据的初值**做种子。
2. 短期反弹（超跌反弹/情绪修复）场景下，开源策略给出的因子族权重有高度一致性：**超跌/企稳类因子占绝对主导（约 30–40%），量能类次之（约 20–25%），情绪/资金类再次（各 10–15%），财报/板块/宏观多为门槛或小权重（5–10%）。**
3. 最贴合本场景的一手样本是 `CANGLIN123/AlphaReversal`（KDJ 超卖+量价+反弹弹性的 A 股多因子策略），其十因子权重可直接作为初值锚点。
4. 所有"权重区间"均标注置信度（高=一手源码 / 中=开源复现或 README / 低=推断）。

---

## 1. 一手来源清单与可达性

| # | 来源 | 类型 | 可达性 | 用途 |
|---|------|------|--------|------|
| 1 | microsoft/qlib `qlib/contrib/data/loader.py` | 一手源码 | ✅ raw.githubusercontent 可达 | Alpha158/Alpha360 因子表达式全集 |
| 2 | microsoft/qlib `examples/benchmarks/README.md` | 官方 benchmark | ✅ | LightGBM/Linear 在 Alpha158/Alpha360 的 IC/ICIR/收益基准 |
| 3 | microsoft/qlib `examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml` | 官方配置 | ✅ | GBDT 超参 + TopkDropout 选股策略 |
| 4 | YTZzzzz/Barra_CNE5 `style_factor.py` | 开源复现（182⭐） | ✅ | Barra CNE5 十风格因子定义与半衰期参数 |
| 5 | rosie068/BARRA_risk | 开源复现（81⭐） | ✅ | CNE6/USE4 因子族划分 |
| 6 | fasiondog/hikyuu `readme.md` | 官方 README | ✅ | Hikyuu 模块化策略组件划分 |
| 7 | stxupengyu/multi-factor-strategy-joinquant `multi-factor.py` | 聚宽策略源码（42⭐） | ✅ | 回归法（LR/Lasso/ElasticNet）学权重 |
| 8 | lijq126/a-stock-recommender `README.md` | 开源项目文档 | ✅ | 20 因子 7 维度显式打分权重（满分 127） |
| 9 | CANGLIN123/AlphaReversal- `README.md` | 开源策略（超跌反弹） | ✅ | 十因子权重 + IC/IR（本场景最强锚点） |
| 10 | CroTuyuzhe/quant-stock-screener `README.md` + `references/weighting_methodology.md` | 开源项目 | ✅ | ICIR 加权 + 风险平价方法论、情绪因子族 |
| 11 | 本地 `src/nous/engine/ml/adaptive_weights.py` 等 | 本仓库一手代码 | ✅ | 现状因子权重（作为"差距基线"） |

> 网络说明：`github.com` 主页直连超时，但 `raw.githubusercontent.com`、`api.github.com`、`www.joinquant.com`、`www.ricequant.com` 均可达，故以上一手来源全部成功获取原文。
> 未达成的部分：券商研报（国泰君安/申万宏源/海通）的 PDF 因子合成细节需付费/登录，未取得一手 PDF，改用其方法论在开源实现中的落地版本（第 4、10 号来源）作为等价证据，并在相应处标注置信度降级。

---

## 2. 因子族划分（对标候选：超跌 / 量能 / 资金 / 板块 / 财报 / 宏观 / 情绪）

先建立三套体系与 nous 七候选族的映射关系。

### 2.1 Barra CNE5 / CNE6 风格因子（行业标准"因子族"）

Barra 是**风险模型**不是 alpha 模型，但它定义了 A 股业内最通用的风格因子族划分。CNE5 十风格因子（来自 `YTZzzzz/Barra_CNE5/style_factor.py`，函数名即因子）：

| 因子族 | 因子（CNE5 命名） | 定义要点（源码） | 映射到 nous 候选 |
|--------|------------------|------------------|------------------|
| Size 市值 | LNCAP / MARCAP / NLSIZE | 对数流通市值、非线性市值 | 财报/门槛（非加分） |
| Beta | BETA | 120 日风险 beta | 宏观/市场暴露（中性化用） |
| Momentum 动量 | RSTR | T=504 日、L=21 日滞后、半衰期 126 的对数超额收益 | 超跌（取反=反转） |
| Residual Volatility 残差波动 | DASTD / CMRA / HSIGMA | 日收益波动（半衰期 42）、累计区间、残差波动 | 超跌/风险 |
| Value 价值 | BTOP / EPFWD / CETOP / ETOP | 账面/预期收益/现金收益/滚动收益收益率 | 财报 |
| Growth 成长 | EGRLF / EGRSF | 长期/短期净利润增长率 | 财报 |
| Leverage 杠杆 | MLEV / DTOA / BLEV | 市场杠杆、资产负债率、账面杠杆 | 财报（负向过滤） |
| Liquidity 流动性 | STOM / STOQ / STOA | 1/3/12 月换手率对数 | 量能 |

来源：[`YTZzzzz/Barra_CNE5/style_factor.py`](https://github.com/YTZzzzz/Barra_CNE5/blob/master/style_factor.py)（`RSTR/DASTD/CMRA/BTOP/STOM/STOQ/STOA/EPFWD/CETOP/ETOP/MLEV/DTOA/BLEV/NLSIZE` 等函数）。置信度：**高**（一手源码，复现 MSCI CNE5 公开描述）。
> 关键参数（半衰期）本身即可借鉴：RSTR 半衰期 126、DASTD 半衰期 42、动量滞后 21 日——**短期反弹应大幅缩短这些窗口**（见 §5）。

CNE6 的因子族划分（来自 `rosie068/BARRA_risk` 文件结构：`CalBarra{Size,Value,Growth,Leverage,Liquidity,Momentum,Volatility,...}.py`）与 CNE5 一致，即 10 大风格因子族：Size、Beta、Momentum、Residual Volatility、Non-linear Size、Book-to-Price、Earnings Yield、Growth、Leverage、Liquidity。置信度：**中**（文件结构证据，非逐因子公式）。

### 2.2 qlib Alpha158 / Alpha360（因子表达式，无人工权重）

qlib 提供的是**因子库**而非权重，权重由下游模型（LightGBM/Linear 等）学习。Alpha158 的因子表达式分组（来自 `microsoft/qlib/qlib/contrib/data/loader.py`）：

| 因子组 | 表达式族（源码命名） | 映射到 nous 候选 |
|--------|----------------------|------------------|
| kbar K 线形态 | KMID/KLEN/KUP/KLOW/KSFT 等 9 个 | 超跌/企稳（单日形态） |
| price 价格 | OPEN/HIGH/LOW/VWAP 的 0–4 日滞后 | 量能/超跌 |
| volume 量 | VOLUME0–4 滞后 | 量能 |
| rolling 滚动 | ROC/MA/STD/BETA/RSQR/RESI/MAX/MIN/QTLU/QTLD/RANK/RSV/IMAX/IMIN/IMXD/CORR/CORD/CNTP/CNTN/CNTD/SUMP/SUMN/SUMD/VMA/VSTD/WVMA/VSUMP/VSUMN/VSUMD | 超跌(ROC/RSV/RANK)、量能(VMA/VSTD/VSUMP)、情绪(SUMP/SUMN≈RSI、CNTP≈涨跌家数比) |

来源：[`microsoft/qlib/qlib/contrib/data/loader.py`](https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py)（`Alpha158DL.get_feature_config`，`kbar/price/volume/rolling` 各分支）。置信度：**高**（一手源码）。
> 特别标注：`RSV%d = ($close-Min($low,d))/(Max($high,d)-Min($low,d)+1e-12)` 与 nous 现有 `K6_price_position` 同构（见 `src/nous/engine/ml/factor_compute.py`），`SUMP/SUMN` 即 RSI 的变体——这些就是"超跌"的机器可读形式。

### 2.3 情绪/资金/板块类（A 股特有，Barra 不覆盖）

Barra 风格因子**不包含** A 股短线的"情绪、资金流、板块动量"，这三类来自量化社区策略与本地代码：

| 候选族 | 代表因子（来源） | 置信度 |
|--------|------------------|--------|
| 情绪 | 连板高度 + 昨日涨停溢价率（本地 `src/nous/engine/screening/short_term.py::_get_market_sentiment`，炒股养家情绪周期）；涨停频率/换手/放量（`CroTuyuzhe/quant-stock-screener` 情绪因子族 `limit_up_freq/volume_burst/short_reversal`） | 高 |
| 资金 | 龙虎榜净买/大宗（本地 `scoring.py::get_dragon_tiger_bonus/get_block_trade_bonus`）；北向 5 日衰减净流入 `weights=[0.35,0.25,0.2,0.12,0.08]`（`scoring.py` L66/L160）；主力进场=放量异动（AlphaReversal） | 高 |
| 板块 | 行业动量（本地 `scoring.py::get_sector_momentum`，TODO 未实现）；行业中性化 z-score（AlphaReversal / quant-stock-screener）；热门赛道 +2（a-stock-recommender） | 中 |
| 宏观 | 市场状态四分类 BULL/BEAR/SIDEWAYS/VOLATILE（本地 `src/nous/engine/ml/market_regime.py`）作为择时门控；VIX/DXY/CNH（`scoring.py::_get_global_macro_factor`，港股专用） | 高 |

---

## 3. 各因子族的业内典型权重区间

以下区间来自多个开源体系的**显式或归一化**权重，按"短期反弹/波段"语境汇总。

### 3.1 显式权重样本一：`a-stock-recommender` v5.0（20 因子 7 维度，满分 127）

| 维度（≈因子族） | 分值 | 归一化权重 |
|----------------|------|-----------|
| 量价因子（量能） | 25 | 19.7% |
| 资金因子 | 20 | 15.7% |
| 估值因子（财报） | 20 | 15.7% |
| 趋势因子 | 20 | 15.7% |
| 风险因子 | 15 | 11.8% |
| 技术因子（超跌/均线/RSI） | 15 | 11.8% |
| 动量因子 | 10 | 7.9% |
| 热门赛道（板块） | +2 | 1.6% |

来源：[`lijq126/a-stock-recommender/README.md`](https://github.com/lijq126/a-stock-recommender/blob/main/README.md)（"四、评分模型详解 v5.0"）。置信度：**中**（README 自述，未见回测验证）。
> 该体系是"趋势+价值"偏中长线的均衡风格，量能+资金合计约 35%，可作为"波段引擎量能/资金权重上限"的参照。

### 3.2 显式权重样本二：`AlphaReversal`（超跌反弹，与本场景同构，十因子权重 100%）

| 因子 | 权重 | 归属候选族 |
|------|:----:|-----------|
| 超卖深度（J_60d_min） | 14% | 超跌 |
| 缩量程度（抛压衰竭） | 13% | 量能 |
| 放量异动（主力进场） | 12% | 量能/资金 |
| 行业热度+质量（PE/ROE） | 12% | 板块/财报 |
| 反转确认（J 拐头+缩量+小 K 线） | 11% | 超跌 |
| BBI 趋势（多空方向） | 10% | 超跌/趋势 |
| 反弹弹性（原创，下跌特征→反弹幅度） | 10% | 超跌 |
| 小 K 线企稳 | 8% | 超跌 |
| MA60 贴近（回调到位） | 7% | 超跌 |
| Amihud 弹性（流动性） | 3% | 量能 |

来源：[`CANGLIN123/AlphaReversal-/README.md`](https://github.com/CANGLIN123/AlphaReversal-/blob/main/README.md)（"十因子打分体系"）。置信度：**中**（README 自述权重，但配了 IC/IR 与回测：回测 2020-04~2025-12 收益 +5.94%、最大回撤 -3.43%、胜率 71.86%；Amihud IC=0.127/IR=1.996、反弹弹性 IC=0.020/IR=0.309、缩量 IC=0.006/IR=0.103）。
> **归并到候选族后**：超跌 ≈ 14+11+10+8+7 = **50%**；量能 ≈ 13+12+3 = **28%**；板块+财报 ≈ 12%；资金（放量异动）并入量能。这是"超跌反弹"语境下**超跌因子权重可高达 40–50%** 的最直接证据。

### 3.3 权重学习方法（决定"区间"而非"定值"）

| 方法 | 公式/规则 | 来源 | 置信度 |
|------|-----------|------|--------|
| ICIR 加权（大类内） | `w_i = ICIR_i / Σ ICIR_i`，剔除 `ICIR < 0.3` 的因子 | `CroTuyuzhe/quant-stock-screener/references/weighting_methodology.md` | 高 |
| 风险平价（大类间） | `w_i ∝ 1 / Var(IC_i)` 或 ERC（等边际风险贡献） | 同上 | 高 |
| 最大化 IR | `max wᵀμ / sqrt(wᵀΣw)`，s.t. Σw=1, w≥0 | 同上 + Grinold-Kahn 框架 | 中 |
| 回归学权重 | Lasso(α=0.004)、ElasticNet(α=0.01, l1_ratio=0.06)、LinearRegression 直接拟合月收益 | `stxupengyu/multi-factor-strategy-joinquant/multi-factor.py` | 高 |
| 非线性交互 | 低波×动量 +15%、质量×成长 +10%、低估值×情绪 +10% | `quant-stock-screener` README | 中 |
| 情绪门控 | 情绪 hot/warm 才允许追涨；cold/cool 才允许抄底（+10 置信度） | 本地 `short_term.py::_check_symbol` | 高 |

### 3.4 汇总：各候选族的业内典型权重区间

| nous 候选族 | 典型权重区间（短期反弹场景） | 主要证据 | 置信度 |
|-------------|------------------------------|----------|--------|
| **超跌/反转/企稳** | **30–50%** | AlphaReversal 归并≈50%；qlib 反转+RSV+RANK 为核心特征 | 高 |
| **量能（含缩量/放量/换手/流动性）** | **15–28%** | AlphaReversal 28%；a-stock-recommender 量价 19.7% | 高 |
| **情绪（涨停梯队/溢价/热度）** | **10–15%** | short_term.py 情绪周期作门控；quant-stock-screener 情绪族 | 中 |
| **资金（龙虎榜/北向/主力异动）** | **5–15%** | scoring.py 北向 0.05–0.08；a-stock-recommender 资金 15.7% | 中 |
| **板块（行业热度/轮动）** | **5–12%** | AlphaReversal 行业 12%；a-stock-recommender 赛道 1.6% | 中 |
| **财报（估值/成长/质量，多为门槛）** | **0–10%（作负向过滤/门槛）** | a-stock-recommender 估值 15.7%（长线语境）；反弹场景下多为过滤 | 中 |
| **宏观/市场状态（择时门控，非加分）** | **0–5% 直接权重，另作 gate** | market_regime.py + adaptive_weights.py 的 regime 切换 | 高 |

---

## 4. 短期反弹场景的权重组合草案（可作初值）

### 4.1 设计原则（从一手来源归纳）

1. **超跌主导**：超跌反弹的收益主要来自"跌过头后的均值回归"，超跌类必须最大权重（AlphaReversal 证据：超跌归并≈50%）。
2. **量能确认**：反弹需要"缩量衰竭 + 放量启动"两个确认，量能是第二权重（AlphaReversal 28%；本地 `方新侠` 规则要求 RSI<30 + 放量>1.5 + 涨幅>3%）。
3. **情绪/宏观是门控不是加分**：情绪周期决定"能不能做"（`short_term.py` 炒股养家），宏观状态决定仓位（`adaptive_weights.py` REGIME_CONFIGS），二者不进入线性加权总分，或仅给小额权重。
4. **财报做负向过滤**：剔除 ST/退市/财务爆雷，不参与正向打分（Barra Leverage 杠杆族本义即负向）。
5. **权重可学习**：初值只作种子，上线前用 ICIR/回测在样本内校准（§3.3 方法）。

### 4.2 推荐初值（总分归一化到 100）

| 因子族 | 权重 | 关键子因子（映射到现有代码） | 依据 |
|--------|:----:|------------------------------|------|
| 超跌/企稳 | **30** | RSI(14)<30、20 日区间位置 K6_price_position 低位、连跌天数、RSV/ROC 反转、K 线企稳（小实体阳线） | AlphaReversal 超跌≈50% 的中枢偏保守取值 |
| 量能确认 | **22** | 量比 K4_vol_ratio（缩量衰竭 <0.7 / 放量启动 >1.5）、换手 STOM、Amihud 非流动性 | AlphaReversal 量能 28% |
| 情绪 | **15** | 涨停梯队高度、昨日涨停溢价率、跌停家数（市场级门控因子） | short_term.py 情绪周期 + quant-stock-screener 情绪族 |
| 资金 | **13** | 龙虎榜净买、北向 5 日衰减净流入 `[0.35,0.25,0.2,0.12,0.08]`、大宗 | scoring.py 北向权重 + a-stock-recommender 资金 15.7% |
| 板块 | **10** | 行业热度排名、板块内个股同步走强（行业中性化后取相对强度） | AlphaReversal 行业 12% |
| 财报（门槛型，负向） | **8** | PE/PB 极端值过滤、ROE>0、剔除 ST/退市（不参与正向打分时可设 0–8） | a-stock-recommender 估值 15.7%（长线语境下调） |
| 宏观/状态 | **2** | regime 四分类（BULL/BEAR/SIDEWAYS/VOLATILE）作为仓位乘数 + 情绪 gate | adaptive_weights.py |

> **合计 = 100**。若把"情绪"和"宏观"移出线性分、改为 gate（推荐），则超跌 30 + 量能 22 + 资金 13 + 板块 10 + 财报 8 的**选股分**合计 83，另设 `情绪 gate`（cold/cool 才放行抄底、hot/warm 放行追涨）与 `regime 仓位乘数`（BULL 0.95 / SIDEWAYS 0.6 / BEAR 0.3 / VOLATILE 0.4，见 `adaptive_weights.py::REGIME_CONFIGS`）。

### 4.3 与本地现状的差距

| 维度 | 现状（本仓库） | 草案建议 | 差距 |
|------|----------------|----------|------|
| 超跌因子权重 | `adaptive_weights.py` reversal 0.5–1.5（相对量，SIDEWAYS 最高） | 归一化后 30% | 反弹场景下 reversal 应显著提升 |
| 情绪 | `short_term.py` 作 gate（cold/cool 抄底 +10） | 保留 gate，另加涨停梯队作为市场级因子 | 已基本对齐 |
| 资金 | `scoring.py` 短线权重 trend 0.30/volume 0.20/value 0.10/policy 0.15/sector 0.10/northbound 0.05 | 资金 13%（含龙虎榜/北向） | 北向仅 0.05，龙虎榜仅作 bonus，需升权 |
| 板块动量 | `scoring.py::get_sector_momentum` 为 TODO 未实现 | 10% | **需实现** |
| 宏观 | `market_regime.py` 四分类已实现 | 作仓位乘数（已实现） | 已对齐 |

---

## 5. 关键参数迁移建议（含来源）

| 参数 | 业内取值（来源） | 短期反弹建议 |
|------|------------------|--------------|
| 动量回看窗口 | Barra RSTR T=504、L=21（CNE5） | 缩短到 5/10/20 日（qlib ROC5/10/20） |
| 波动半衰期 | DASTD 半衰期 42（CNE5） | 缩短到 5–10 日滚动 std（`factor_compute.py K3_std_5d/10d` 已具备） |
| 换手率窗口 | STOM/STOQ/STOA = 1/3/12 月（CNE5） | 用 1 月 STOM 即可 |
| 因子预处理 | 缩尾 2.5% → 行业/市值中性 → z-score（quant-stock-screener） | 照搬；行业中性用本地 sector 字段 |
| ICIR 门槛 | ICIR < 0.3 剔除（quant-stock-screener） | 照搬 |
| 调仓频率 | 月频（多数开源） | 1–20 日波段 → 周频或事件触发 |
| 止损 | 方新侠：破抄底日最低价止损 / +5% 止盈（`short_term.py`） | 照搬（1–20 日场景天然匹配） |

---

## 6. 数据质量 caveats 与开放问题

1. **无回测验证的权重不能直接上线**：`a-stock-recommender`、`AlphaReversal` 的显式权重出自 README 自述，前者未见回测数据；后者有回测但收益 +5.94% 跑输沪深300 同期 +10.22%（虽最大回撤极低），需在 nous 自己的 1–20 日回测框架里复核。
2. **券商研报一手 PDF 未取得**：国泰君安/申万宏源/海通的多因子合成细节（等权/IC 加权/最大化 ICIR 的具体阈值）未能 curl 到一手 PDF，改用开源落地实现（`quant-stock-screener/weighting_methodology.md` 的 ICIR 加权 + 风险平价 + 最大化 IR）作等价证据，相关区间置信度标"中"。
3. **因子族权重与场景强耦合**：AlphaReversal 的"超跌 50%"结论来自 KDJ 超卖+缩量企稳的特定定义；换用 RSI/RSV 定义时权重会漂移，初值必须做敏感性分析。
4. **Barra 是风险模型非 alpha 模型**：其"十风格因子"提供的是**中性化与因子族划分**标准，不是可加分的权重，§3.4 中"区间"是借用其因子族口径、权重来自开源 alpha 策略，二者不可混为一谈。
5. **本地部分因子未实现**：`scoring.py` 的 `get_policy_catalyst_score`/`get_sector_momentum` 均为 TODO 返回固定 0.5，草案中"板块 10%"依赖先实现板块动量；`scoring.py::load_config()` 指向 `~/code/stock-screener/config.yaml`，权重实际存储在未迁移的外部仓库，本仓库内未见 `a_share.short_term` 的落盘配置，存在迁移断层。
6. **小市值/涨跌停约束**：短线反弹标的常接近涨跌停（`short_term.py` 已处理 10%/20%/30% 涨跌幅限制），权重体系须配合 `_get_limit_pct` 的交易所规则与 T+1 成交假设（AlphaReversal 用 T+1 开盘成交避免前视）。

---

## 7. 附：完整引用 URL

- qlib 因子表达式：https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py
- qlib benchmark 结果：https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md
- qlib LightGBM 配置：https://github.com/microsoft/qlib/blob/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
- Barra CNE5 因子：https://github.com/YTZzzzz/Barra_CNE5/blob/master/style_factor.py
- Barra CNE6/USE4：https://github.com/rosie068/BARRA_risk
- Hikyuu：https://github.com/fasiondog/hikyuu/blob/master/readme.md
- 聚宽多因子（回归学权重）：https://github.com/stxupengyu/multi-factor-strategy-joinquant/blob/master/multi-factor.py
- 20 因子 7 维度打分：https://github.com/lijq126/a-stock-recommender/blob/main/README.md
- AlphaReversal（超跌反弹十因子）：https://github.com/CANGLIN123/AlphaReversal-/blob/main/README.md
- ICIR 加权方法论：https://github.com/CroTuyuzhe/quant-stock-screener/blob/master/references/weighting_methodology.md
- 本地仓库：`src/nous/engine/ml/adaptive_weights.py`、`src/nous/trader/scoring.py`、`src/nous/engine/screening/short_term.py`、`src/nous/engine/ml/factor_compute.py`、`src/nous/engine/ml/market_regime.py`
