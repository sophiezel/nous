# 数据与因子可用性盘点 — 短期反弹选股模型（rebound 引擎）

**Ticket:** 数据与因子可用性盘点 (https://github.com/sophiezel/nous/issues/2)
**Wayfinder effort:** 短期反弹选股模型 — 在 nous 落地 rebound 引擎 (issue #1)
**调研日期:** 2026-08-24
**方法:** `sqlite3 ~/nous-data/screener.db` 实测（`.schema` / `MIN/MAX/COUNT` / 空值率 / 逐分区盘点），并核对因子计算源码 `src/nous/engine/ml/factor_compute.py` 与因子快照 parquet 的实际列。

> 本报告只陈述"库里有/没有什么、能用什么口径算"，不下"该买什么"的结论。所有日期范围均为本次实测值，来源逐条标注。

---

## 0. 结论速览

| 因子族 | 可得性 | 一句话结论 |
|--------|--------|-----------|
| 超跌（反转/超卖） | ✅ 可直接算 | 依赖 `stock_daily_all`（K1/K2/K6/K10），但**未复权**、历史存在幸存者偏差 |
| 量能（成交量/换手） | ✅ 可直接算 | `stock_daily.volume/amount` 覆盖 2009+，同超跌的复权/幸存者坑 |
| 资金流向（个股主力） | 🟡 部分可算 | `fund_flow_stock` 仅 ~10 个月；`main_pct` 96% 空；板块资金流表为空 |
| 融资融券 | 🟡 仅全市场级 | `margin_daily/margin_short_daily` 是**全市场汇总**（无 symbol 列）；个股两融需新数据 |
| 沪深港通 | 🟡 部分可算 | 南向个股 2024-05+ 可用；**北向个股为估算值**（真实数据 2024-08 已停披露） |
| 龙虎榜 | ✅ 可直接算 | `lhb_daily` 2015+，~70 行/日；`reason` 口径 100+ 种需归一化 |
| 行业板块 | 🟡 部分可算 | 申万/概念映射为**当前快照**（无 point-in-time）；板块资金流表为空；行业收益可自行聚合 |
| 财报 | 🟡 仅当前快照 | `stock_fundamental` 是滚动快照，`roe` 89% 空、`dividend_yield` 93% 空；无历史/point-in-time |
| 宏观 | 🟡 部分可算 | 利率/流动性可算；**CPI/PPI/GDP 已停更 ~1 年**（最新 2025-08） |
| 政策 | ❌ 需人工 | 库中无政策/新闻表，只能走 LLM/人工 |
| 情绪 | ✅ 市场级可算 | `sentiment_cache` 2020+；但涨停梯队/溢价表（`zt_daily_pool`/`limit_up_premium`）**停在 2026-05-22** |

---

## 1. 数据源总览（实测）

数据库位置：`~/nous-data/screener.db`（符号链接 → `~/code/stock-screener/data/screener.db`），共 **121 张表**。日线按年分区 + 热表（hot），历史权威视图为 `stock_daily_all`。

### 1.1 日线：分区结构（关键，直接决定因子窗口可算到多深）

| 对象 | 类型 | 覆盖范围 | 行数 | distinct symbol |
|------|------|----------|------|-----------------|
| `stock_daily`（热表） | 表 | 2025-08-25 ~ 2026-08-21 | 1,018,118 | 6,369 |
| `stock_daily_2026` | 分区表 | 2026-01-02 ~ **2026-07-16** | 729,606 | 6,245 |
| `stock_daily_2025` | 分区表 | 2025-01-02 ~ 2025-12-31 | 858,512 | 6,082 |
| `stock_daily_2024` | 分区表 | 2024-01-02 ~ 2024-12-31 | 1,279,991 | 5,346 |
| `stock_daily_2020` | 分区表 | 2020-01-02 ~ 2020-12-31 | 907,889 | 4,010 |
| `stock_daily_2015` | 分区表 | 2015-01-05 ~ 2015-12-31 | 418,313 | 2,071 |
| `stock_daily_2014` | 分区表 | 2014-01-02 ~ 2014-12-31 | 101,929 | 417 |
| `stock_daily_2010` | 分区表 | 2010-01-04 ~ 2010-12-31 | 414 | **2** |
| `stock_daily_2009` | 分区表 | 2009-11-16 ~ 2009-12-31 | 33 | **1** |
| `stock_daily_all` | **视图** | 2009-11-16 ~ 2026-08-21 | 10,228,607 | 6,376 |
| `stock_daily_20260520` | 空表 | — | 0 | 0 |

**路由规则**（`src/nous/data/storage/daily_bars.py`）：
- 读历史用 `stock_daily_YYYY` 分区；写热表永远落 `stock_daily`。
- `stock_daily_all` = 2009..2026 全分区 `UNION ALL` + 热表中 `trade_date > MAX(stock_daily_2026)`（即 > 2026-07-16）的尾部。
- 因子引擎 `daily_relation_sql(start, end)` 按窗口选分区，避免扫全 UNION；语义与 `stock_daily_all` 一致。

**⚠️ 分区断裂（已由视图兜底）：** `stock_daily_2026` 分区最后一次更新停在 **2026-07-16**；2026-07-17 之后的数据只存在于热表 `stock_daily`。`stock_daily_all` 视图 + `daily_relation_sql` 已正确拼接（不会重复、不会断档），但任何**绕过视图直接 `FROM stock_daily_2026`** 的代码会拿到 2026-07-16 之后缺口的错误数据。

### 1.2 股票基础与行业

| 表 | 覆盖 | 说明 |
|----|------|------|
| `stock_basic` | 6,640（a=5,896, hk=744） | 仅 symbol/name/market；**无上市日期、无退市日期、无 ST 标志**（ST 只能靠 `name LIKE '%ST%'` 猜，命中 478 只，含港股） |
| `stock_industry_multilevel` | 5,203 symbol / 131 industry_code | 申万行业，`source='cninfo_sw'`，但 `start_date` **全空** → 只有"当前归属"，无 point-in-time |
| `stock_industry`（legacy） | 5,519 | 旧单层行业名 |
| `stock_industry_map` | **0 行（空表）** | 不可用 |
| `industry_tree` | 498 | 申万行业树（code/name/level/parent） |
| `stock_concept_map` | 4,454 symbol / 70 concept | 东方财富概念，当前快照 |

---

## 2. 因子族 × 数据源 × 可得性 矩阵

图例：✅ 可直接算（库内即可）｜🟡 需新数据/部分可算｜❌ 需人工/外部。

| 因子族 | 数据源（表.字段） | 历史深度 | 更新频率 | 可得性 | 主要坑 |
|--------|-------------------|----------|----------|--------|--------|
| 超跌/反转 | `stock_daily_all.close`（K1_ret_*、K2_reverse_*、K6_ma_gap_*、K6_price_position、K10_bias_*、K10_roc_*） | 2015+（<2014 几乎无） | 日更（T+0） | ✅ | 未复权；幸存者偏差 |
| 量能 | `stock_daily_all.volume/amount`（K4_*、K10_vol/amt、K10_pvt、K9_wq007） | 2015+ | 日更 | ✅ | 未复权不影响量；金额含除权效应 |
| 资金流向 | `fund_flow_stock.main_net/super_large_net/...` | 2025-10-29 ~ 2026-08-21（~10 月） | 日更 | 🟡 | 仅 10 月；`main_pct` 96% 空；板块级 `sector_fund_flow_daily` 空表 |
| 融资融券 | `margin_daily.margin_balance/margin_buy`、`margin_short_daily.short_balance/...` | 2010-03 ~ / 2015-07 ~ | 日更 | 🟡 | **全市场级（无 symbol）**；重复行；个股两融缺失 |
| 沪深港通 | `hsgt_stock_daily`（南向个股）、`hsgt_daily`、`hsgt_market_daily`、`hsgt_sector_daily` | 南向 2024-05+；北向个股 2026-01+（估算） | 日更（T+1） | 🟡 | 北向个股=估算值；`hsgt_board_daily` 仅 1 天；`hsgt_quarterly_holding` 死表 |
| 龙虎榜 | `lhb_daily.l_buy/l_sell/net_amount/reason` | 2015-01 ~ 2026-08-20 | 日更 | ✅ | `reason` 口径 100+ 种；含可转债/北交所/ST 混入 |
| 行业板块 | `stock_industry_multilevel` + `stock_daily_all` 聚合、`index_daily`（10 指数） | 行业映射仅当前；指数 1990+ | 行业映射低频/指数日更 | 🟡 | 无 point-in-time 行业归属；板块资金流表空 |
| 财报 | `stock_fundamental.pe/pb/total_mv`（roe/dividend 空） | 仅当前快照（snapshot_date 集中在 2026-08-21） | 日更快照 | 🟡 | roe 89% 空、dividend_yield 93% 空；无历史 |
| 宏观 | `macro_lpr`、`macro_shibor`、`macro_m2`、`macro_pmi`、`macro_cpi`、`macro_ppi`、`macro_gdp` | 利率/流动性到 2026-05；通胀/增长**停更于 2025-08/07** | 月/日（各表不一） | 🟡 | CPI/PPI/GDP 停更 ~1 年 |
| 政策 | —（无表） | — | — | ❌ | 需 LLM/人工/外部新闻源 |
| 情绪 | `sentiment_cache.score/limit_up_count/details`、`limit_up_sentiment` | 2020+ / 2026-04+ | 日更 | ✅（市场级） | 涨停梯队/溢价表停更于 2026-05-22 |
| 盘中（分钟） | `intraday_minute.price/volume/amount` | 2026-05-17 ~ 2026-08-21（~3 月，仅 421 只） | 盘中 | 🟡 | 仅池内 421 只；历史浅 |

---

## 3. 各因子族详细盘点与建议计算口径

### 3.1 超跌（反转 / 超卖）

- **支撑表/字段：** `stock_daily_all`（或因子快照 `factors/latest.parquet` 中现成的 `K1_ret_1d/5d/10d/20d/60d`、`K2_reverse_1d/5d`、`K6_ma_gap_20/60`、`K6_price_position`、`K10_bias_20`、`K10_roc_24`）。
- **历史深度：** 因子快照实测为 **2015-01-05 ~ 2026-08-21**、10,002,070 行、5,631 只（`factor_compute` 默认 `--start 2015-01-01`，见 `scripts/overnight_chain.sh:70`）。2014 及以前分区宇宙极小（1~417 只），不可用于回测。
- **更新频率：** 日更（Provider DAG S2 增量合并进 latest.parquet）。
- **建议口径：** 反弹模型的核心"超跌"用**短窗口**收益，规避复权失真：
  - `ret_5d = close / close[-5] - 1`（K1_ret_5d）
  - `ma_gap_20 = (close - MA20) / MA20`（K6_ma_gap_20）
  - `price_position_20 = (close - low_20d) / (high_20d - low_20d)`（K6_price_position，衡量"离 20 日低点有多近"）
- **数据质量坑：**
  1. **未复权（除权除息未处理）**：全库无任何复权/除权因子表（实测 `sqlite_master` 无 `adj/hfq/复权` 相关列或表）。`close.pct_change(n)` 在除息日会产生**假的下跌**。短窗口（≤10d）影响小；**60d 收益、MA 缺口、price_position 会失真**。若回测含除权密集的 5-6 月，需谨慎，或补后复权数据（新数据）。
  2. **幸存者偏差**：历史分区宇宙逐年递减（2015 2,071 只 → 2020 4,010 只 → 2026 6,245 只，实测），说明历史是"当前存活股"回填，退市股缺失。回测 2015-2020 段存在幸存者偏差。
  3. 因子快照 `K7_*`（基本面）是**用"当前最新"快照合并**到所有历史行（`factor_compute.py` 中 `drop_duplicates("symbol", keep="last")`），存在前视偏差——但 K1/K2/K6 无此问题。

### 3.2 量能

- **支撑表/字段：** `stock_daily_all.volume / amount`；快照中 `K4_vol_ratio`（量/MA5）、`K4_vol_chg_5d`、`K4_vwap`、`K10_vol_ma60_ratio`、`K10_amt_ratio`、`K10_pvt_chg/pvt_ma10`、`K9_wq007`（放量下跌信号）。
- **建议口径：**
  - 反弹常伴"缩量止跌后放量"：`K4_vol_ratio = volume / vol_ma5`；`K10_amt_ratio = amt_ma5 / amt_ma20`。
  - 换手率无直接字段，可用 `volume / 流通股本` 近似，但库内**无流通股本**（`stock_fundamental` 只有 `total_mv`），需外部股本数据。
- **坑：** 量/额数据完整（`stock_daily` 全量 `volume/amount` 空值=0）；但 `amount` 含除权除息导致的市值变化，做比值时可接受。

### 3.3 资金流向

- **支撑表/字段：** `fund_flow_stock(trade_date, symbol, main_net, super_large_net, large_net, medium_net, small_net, main_pct, total_amount)`。
  - 覆盖 2025-10-29 ~ 2026-08-21，5,223 只，近端每日 ~5,204 只。`main_net` 无空值；**`main_pct` 322,063/334,090 = 96% 空**。
- **历史深度：** 仅 ~10 个月。`stock_fund_flow`（旧表）更小且更旧（2025-10-29 ~ 2026-05-15，仅 4,560 行），是子集/早期采集。
- **建议口径：** 只用 `main_net`（主力净流入额）及其 3/5 日累计/占比（用 `total_amount` 归一化），不要用 `main_pct`。反弹模型用"近 5 日主力净流入转正/加速"。
- **坑：**
  1. 历史太短（10 个月），无法做长回测 → **需新数据**补齐更长主力资金流（如 2020+）。
  2. 板块级 `sector_fund_flow_daily` 是**空表（0 行）**，行业资金流因子不可算。

### 3.4 融资融券

- **支撑表/字段：** `margin_daily(trade_date, margin_balance, margin_buy, short_sell_volume, short_balance, short_value, total_balance)`、`margin_short_daily(..., short_balance, short_volume, short_sell, source)`。
- **关键事实：两表都无 `symbol` 列 → 是"全市场汇总"，不是个股级。**
  - `margin_daily`：2010-03-31 ~ 2026-08-20，4,809 行（但同一 trade_date 有**重复行**，如 2026-01-05 有 4 行，且存在全 NULL 行）。
  - `margin_short_daily`：2015-07-09 ~ 2026-08-24，146,076 行，`source='combined'` 唯一值；**每日 ~73 行重复**（需按 `trade_date` 去重/聚合后才能用）。
- **建议口径（仅全市场情绪代理）：**
  - 融资情绪：`margin_balance` 的 5 日变化率；两融余额占成交额比。
  - 融券情绪：`short_balance` 或 `short_sell` 变化。
- **坑：**
  1. **个股级两融余额/买入额缺失** → 若要"该股融资盘强平→超跌反弹"这类因子，需新数据（交易所两融标的每日明细）。
  2. `margin_daily`/`margin_short_daily` 有重复行 + 空行，必须先按日聚合、丢弃 NULL。

### 3.5 沪深港通

- **支撑表/字段：**
  - `hsgt_stock_daily(trade_date, symbol, direction, net_inflow, holding_market_cap, holding_pct, hold_shares, hold_value, change_1d/5d/10d, estimated_net_buy, ...)`：
    - `direction='南向'`（港股通）：2024-05-22 ~ 2026-08-20，295,668 行，每日 ~617 只（近端）。
    - `direction='北向'`（外资买 A 股）：2026-01-05 ~ 2026-08-20，仅 8,256 行、537 只，且 `estimated_net_buy` 98% 空、`change_1d/5d/10d` 87% 空。
  - `hsgt_daily(trade_date, direction, net_buy, ...)`：`north` 仅 51 行（2026-05-15~）、`south` 2,674 行（2014-11-17~）。
  - `hsgt_market_daily`（2026-01-05~，北/南各 ~160 行）、`hsgt_sector_daily`（2026-01-05~，1,941 行）。
  - `hsgt_board_daily`：仅 2026-05-15 一天 25 行（不可用）。
  - `hsgt_quarterly_holding`：仅 3 只 symbol（2017-2024），**死表**。
- **建议口径：**
  - 反弹模型若覆盖 A 股，港通因子的可用部分是**南向港股通个股**（`hsgt_stock_daily` 南向的 `net_inflow` / `holding_pct` 变化）。
  - 北向个股真实数据**自 2024-08 起交易所停止披露**；库内"北向"是 `northbound_estimator.py` 用 TOP50×K 系数（K=2.0 默认，未校准）推算的**估算值**（见 `src/nous/engine/pipelines/northbound_estimator.py` 头注释），不可当真实资金用，最多当弱代理。
- **坑：** 北向数据本质是估算 + 极短历史；`estimated_net_buy`/`change_1d/5d/10d` 基本为空。南向数据可用但只能用于港股池。

### 3.6 龙虎榜

- **支撑表/字段：** `lhb_daily(trade_date, symbol, name, close, pct_change, turnover_rate, l_buy, l_sell, net_amount, amount, reason)`。
  - 2015-01-05 ~ 2026-08-20，70,467 行，近端每日 ~68-74 行。
- **建议口径：**
  - 上榜信号：`net_amount = l_buy - l_sell`（龙虎榜净买额）；`turnover_rate`（换手率）；上榜日 `pct_change`。
  - 反弹因子：近 N 日是否上龙虎榜、净买额方向、游资/机构席位（需从 `reason` 或另行解析营业部，库内**无席位明细**）。
- **坑：**
  1. **`reason` 口径漂移严重**：实测 100+ 种 distinct 值（从"日涨幅偏离值达到7%的前五只证券"到"有价格涨跌幅限制的日收盘价格涨幅偏离值达到7%的前三只证券"再到"当日换手率达到20%的前5只股票"），跨年度比较需先归一化（映射成"涨停类/跌停类/换手类/振幅类/ST类/退市类/新股类/可转债类"）。
  2. 混入了**可转债、北交所、ST/退市**标的，需用 `stock_basic`（+ `name LIKE '%ST%'`）和 market 过滤。
  3. 无席位/游资明细，只能做"是否上榜 + 净额方向"，做不了游资风格因子。

### 3.7 行业板块

- **支撑表/字段：** `stock_industry_multilevel(symbol, industry_l1/l2/l3, industry_code, is_current)`（申万，5,203 只 / 131 代码）+ `industry_tree` + `stock_concept_map(symbol, concept_name)`（概念，4,454 只 / 70 概念）+ `index_daily`（10 个指数：上证/沪深300/中证500/1000/创业板/科创50/恒指等，1990-12-19~）。
- **建议口径：**
  - 行业收益/动量：`stock_daily_all JOIN stock_industry_multilevel` 按 `industry_l2`（或 code）聚合等权/市值加权日收益，可算行业强弱、行业超跌反弹。
  - 概念轮动：`stock_concept_map` 聚合。
  - 指数作为市场 beta/风格基准：`index_daily`（`IDX_000852` 中证1000 适合小盘反弹基准）。
- **坑：**
  1. **行业/概念映射是"当前快照"**（`stock_industry_multilevel.start_date` 全空，`is_current=1`），无 point-in-time 历史归属 → 回测中"过去某日某股属于什么行业"不可知，只能用当前归属近似（有前视风险）。
  2. 板块资金流 `sector_fund_flow_daily` 空表 → 行业资金流向因子不可算。
  3. `stock_industry_map` 空表、`stock_industry`（legacy 5,519 只）与 multilevel（5,203 只）覆盖不一致，建议统一用 `stock_industry_multilevel`。

### 3.8 财报 / 基本面

- **支撑表/字段：** `stock_fundamental(symbol, pe, pb, roe, dividend_yield, debt_ratio, total_mv, pe_dynamic, pe_static, snapshot_date)`（6,627 行）+ `stock_fundamental_snapshots`（历史快照，仅 2026-05-15 一天、2,135 只）。
- **空值率实测（当前快照 6,627 行）：** `pe` 7.7% 空、`pb` 5.3% 空、`total_mv` 5.3% 空、**`roe` 88.7% 空、`dividend_yield` 93.3% 空**、`debt_ratio` 几乎全空。
- **建议口径：** 只用 `pe / pb / total_mv`（覆盖好）；`K7_pb` 是现有模型 top-10 因子（`ic_analysis/ic_2026-08-24.json`）。
- **坑：**
  1. **无历史财报、无 point-in-time**：`stock_fundamental` 是滚动当前快照（snapshot_date 集中在 2026-08-21，6,428 行）；`stock_fundamental_snapshots` 只有 1 天。做历史回测时 PE/PB 只能用当前值 → 前视偏差。**需新数据**（历史财报/业绩快报）。
  2. 因子快照 `K7_*` 是用当前值合并到全部历史行，属前视；回测应把 K7 当"截面常量"或重新补历史财报。

### 3.9 宏观

| 表 | 字段 | 实测范围 | 状态 |
|----|------|----------|------|
| `macro_lpr` | LPR1Y/LPR5Y/RATE_1/RATE_2 | 1991-04-21 ~ 2026-05-20（1,572 行，日） | ✅ 可用 |
| `macro_shibor` | O/N~1Y 定价/涨跌幅 | 2015-05-08 ~ 2026-05-19（9,129 行，日） | ✅ 可用 |
| `macro_m2` | M2/M1/M0 数量+同比+环比 | 2008-01 ~ 2026-04（220 行，月） | ✅ 可用 |
| `macro_pmi` | 制造业/非制造业 指数+同比 | 2008-01 ~ 2026-04（220 行，月） | ✅ 可用 |
| `macro_cpi` | cpi_yoy | 1986-02 ~ **2025-08-09**（475 行） | ⛔ 停更 ~1 年 |
| `macro_ppi` | ppi_yoy | 1995-08 ~ **2025-08-09**（361 行） | ⛔ 停更 ~1 年 |
| `macro_gdp` | 今值/预测值/前值 | 2011-01-20 ~ **2025-07-15**（61 行） | ⛔ 停更 ~1 年 |

- **建议口径：** 利率/流动性（LPR/Shibor/M2）可作市场 beta/风格状态；PMI 作经济景气。反弹模型短期可主要用 LPR/Shibor（流动性宽松利好反弹）。
- **坑：** `macro_cpi/ppi/gdp` 三个通胀/增长指标**最新只到 2025-08/07**，近一年缺失 → 通胀因子需新数据；`macro_gdp`/`macro_m2`/`macro_pmi` 用中文列名，代码里需转义引用。

### 3.10 政策

- **库内无政策/新闻表**（`messages` 是微信推送记录，非政策数据）。政策因子（如"降准降息/行业利好"）**需人工 + LLM/外部新闻源**落地，建议落成结构化事件表（date × 政策类型 × 影响板块）。

### 3.11 情绪

- **支撑表/字段：**
  - `sentiment_cache(date, score, limit_up_count, limit_up_rate, details)`：2020-01-02 ~ 2026-08-21，1,526 行。`details` JSON 含 `limit_up/limit_down/up/down/total`（涨跌家数）。
  - `limit_up_sentiment(trade_date, limit_up_count, limit_down_count, board_break_count, board_break_rate, max_board_height, first/second/high_board_count, strong_pool_count)`：2026-04-17 ~ 2026-08-21，73 行（**注意 `trade_date` 格式不统一：`20260821` 无横杠**）。
  - `zt_daily_pool`（涨停池，连板天数/封单/开板次数）：2026-01-06 ~ **2026-05-22 停更**，8,845 行。
  - `limit_up_premium`（涨停溢价 open/close premium）：2026-01-06 ~ **2026-05-22 停更**，5,152 行。
  - `theme_auto_pools`（主题池 161 主题）、`theme_scores`、`leader_history`（龙头晋级）2026-05+。
- **建议口径：**
  - 市场情绪：`sentiment_cache.limit_up_rate`、涨跌家数比（`details.up/down`）、`limit_up_sentiment.max_board_height / board_break_rate`（连板高度、炸板率——反弹赚钱效应核心）。
- **坑：**
  1. `zt_daily_pool`（涨停梯队/连板）、`limit_up_premium`（涨停溢价）**停更于 2026-05-22** → 若要"涨停溢价"或"连板高度"因子，近 3 个月数据缺失，需新数据。
  2. `limit_up_sentiment` 的 `trade_date` 用 `YYYYMMDD`（无横杠），与其它表 `YYYY-MM-DD` 不一致，JOIN 前需统一。
  3. `sentiment_cache.score` 口径需读采集代码确认（`src/nous/data/collectors/fetchers/sentiment.py`）。

### 3.12 盘中分钟（附）

- `intraday_minute(symbol, datetime, price, volume, amount, pct_change)`：2026-05-17 ~ 2026-08-21，485,324 行，**仅 421 只**（池内标的），~3 个月。
- 用于盘中反弹信号（如日内 V 型反转、尾盘放量）可行，但**历史浅 + 仅池内**，无法做长回测。`realtime_pool` 表定义当前池（symbol + pool_source + strategy_type）。

---

## 4. 跨因子数据质量坑清单（实现时必须处理）

1. **未复权（除权除息）**：全库无复权因子表。所有 `close` 为不复权价。→ 短窗口（≤10d）可用；长窗口因子失真。建议补后复权数据或限制使用 ≤20d 窗口。
2. **涨跌停无标志字段**：`stock_daily` 无 `is_limit_up/down`、无涨跌幅限制字段。需用 `close/pct_change` 相对前收 + 板块规则（主板 10% / 创业板·科创 20% / 北交所 30% / ST 5%）自行推断；ST 只能靠 `stock_basic.name LIKE '%ST%'`。**涨停（尤其一字板）当日不可买入，回测必须剔除或按涨停价成交规则处理。**
3. **幸存者偏差 + 历史宇宙递减**：日线分区 2009 年仅 1 只、2014 年 417 只、2015 年 2,071 只、2020 年 4,010 只。**可回测起点建议 ≥2015，且早期存在幸存者偏差**；`stock_basic` 是当前存活名单（6,640），而历史日线含 4 只已退市、且 265 只当前 A 股在日线中无数据。
4. **分区断裂（已兜底）**：`stock_daily_2026` 停在 2026-07-16；后续数据在热表。必须走 `stock_daily_all` 视图或 `daily_relation_sql`，不要直接查年分区。
5. **基本面前视偏差**：因子快照 K7 用"当前最新"基本面合并到全历史；`stock_fundamental` 无 point-in-time 历史。回测需注意。
6. **空表/死表**：`sector_fund_flow_daily`(0)、`block_trade_daily`(0)、`stock_industry_map`(0)、`stock_daily_20260520`(0)、`hsgt_board_daily`(仅 1 天)、`hsgt_quarterly_holding`(仅 3 只)。选表时避开；大宗交易用 `block_trades`（2026-01+，~10-34 行/日）。
7. **重复/空行**：`margin_daily`、`margin_short_daily` 有同日多行 + 全 NULL 行，需按日去重聚合。
8. **停更表**：`macro_cpi/ppi/gdp`（停更 ~1 年）、`zt_daily_pool`/`limit_up_premium`（停更于 2026-05-22）。
9. **日期格式不一致**：`limit_up_sentiment.trade_date` 为 `20260821`（无横杠）。
10. **北向个股为估算值**：真实北向个股数据 2024-08 起停披露，库内为 TOP50×K 系数推算（K=2.0 未校准），勿当真实资金。

---

## 5. 现有因子资产（可直接复用）

- **因子快照**：`~/nous-data/factors/latest.parquet`（2015-01-05 ~ 2026-08-21，10,002,070 行，5,631 只，60 个 K 因子）+ `snapshots/{date}.parquet` 版本化。列清单见第 3.1 节，源码 `src/nous/engine/ml/factor_compute.py`（K1~K7、K9 WQ101 子集、K10 Alpha158 子集）。
- **已覆盖的反弹相关因子**：超跌（K1/K2/K6/K10）、量能（K4/K10/K9_wq007）、量价相关（K5）、波动率（K3）。**未覆盖**：个股资金流、个股两融、行业资金流、龙虎榜席位、历史财报、政策。
- **另类数据因子**（`src/nous/engine/ml/alt_data_factors.py`）：已有 K8_northbound（全市场）、K8_margin（全市场）、K8_sentiment（全市场）骨架，但未进入 parquet 快照（60 列中无 K8）。
- **IC 监控**：`~/nous-data/ic_analysis/` 周报显示模型 IC=0.0414，top 因子 `K9_wq007 / K6_ma60 / K3_std_60d / K1_ret_60d / K7_pb`。

---

## 6. 开放问题 / 待补数据

1. **个股融资融券明细**（融资余额/买入额按股票）—— 库内缺失，两融因子只能全市场级。是否需接入交易所每日两融标的明细？
2. **主力资金流历史** —— 目前仅 10 个月；若要回测 2015+ 的"资金流向"因子，需补历史数据源。
3. **后复权价 / 复权因子** —— 当前全库不复权，60d 超跌/MA 缺口类因子会失真，是否补复权因子表？
4. **涨停/跌停标志 + ST 标志 + 上市/退市日期** —— 回测需要这些做可交易性约束，当前只能间接推断。
5. **涨停梯队/溢价停更**（zt_daily_pool/limit_up_premium 停于 2026-05-22）—— 情绪细分因子近 3 月缺失，是否恢复采集？
6. **CPI/PPI/GDP 停更** —— 通胀/增长宏观因子近一年缺失，是否补数？
7. **行业 point-in-time 归属** —— 当前只有当前快照，历史行业归属缺失。
8. **政策因子** —— 无结构化数据源，需确定人工/LLM 事件表方案。
