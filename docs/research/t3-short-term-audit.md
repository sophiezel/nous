# 短期反弹选股模型 — short_term.py 能力盘点（ticket #4）

> 目标：盘点 `src/nous/engine/screening/short_term.py`（813 行）的能力边界，为"改造它 vs 并行新反弹引擎"提供事实基础。
> 方法：逐函数读源码 + 全仓库 grep 引用 + 对照表结构。所有结论附文件:行号证据。

---

## 0. 结论先行（TL;DR）

**`short_term.py` 是一个完全孤立的死模块**：全仓库没有任何 `import` 它、没有任何 CLI 命令调用它、没有回测/推荐管道接入、没有测试覆盖。它是一套"4 条游资规则 + 1 个情绪闸门"的**原型脚本**，且其数据访问层存在一个致命 bug（取"最早 N 天"而非"最近 N 天"），导致全部规则在真实数据上错位。

**改造 vs 并行的事实结论：**

- **不改造、直接复用其核心规则逻辑**的选项不成立——因为它从未跑通过验证，且数据层 bug 使其规则从未对正确数据生效过。
- **并行新建反弹引擎**是正确路径，但可**移植**其中 4 条规则的"触发条件 + 止损/止盈语义"作为可解释因子/信号规格（纯函数、无副作用、阈值清晰）。
- **不建议**把 `short_term.py` 作为新引擎的地基（数据访问、情绪闸门、退出接口三处都需要重写，重写成本≈重写全部）。

---

## 1. 文件定位与孤立现状

| 事实 | 证据 |
|---|---|
| 模块路径 | `src/nous/engine/screening/short_term.py`（813 行） |
| 声称用途 | "5 种游资操盘方法论编码为可执行规则"（模块 docstring，L1-24） |
| 声称集成点 | `theme_recommend.py`、`trader/executor.py`、`trader_poll.py`（docstring L15-17）——**均不成立**，见 §5 |
| 全仓库 import | **0 处**。`grep -rn "from nous.engine.screening\|short_term_engine\|ShortTermEngine"` 无任何命中（除自身） |
| CLI 命令 | **无**。`src/nous/cli.py` 的 `screen` 命令走 `coarse_filter_a_long` + `run_trl_track`，不碰本模块（cli.py:72-160） |
| 测试覆盖 | **0**。`tests/` 下 grep 徐翔/赵老哥/游资/ShortTermEngine/screening 无命中；`tests/engine/` 仅 backtest + ml |
| 运行入口 | 仅 `python -m ... short_term` 的 `main()`（L739-813），需要 `src.short_term_engine` 模块名（与真实包路径 `nous.engine.screening.short_term` 不符，无法按 docstring 的方式启动） |

---

## 2. 数据结构

- `EntrySignal`（L36-45）：`symbol, name, rule, confidence(0-100), position_pct(15-30%), trigger_price, stop_loss, detail`。
- `ExitSignal`（L49-56）：`symbol, reason, urgency(immediate/today/close), action(sell_all/sell_half), detail`。
- `ShortTermEngine`（L639）：`sentiment`（带缓存 property）、`scan_all`、`entry_signals`、`exit_signals`、`check_symbol`、`_check_symbol`。

---

## 3. 四套游资规则 — EntrySignal 触发条件（逐阈值）

> 通用前置：`_get_daily_list` 取日线后，调用方给每行打 `r["_symbol"]=symbol` 标签，供 `_is_limit_up` 按代码前缀判涨跌幅限制（`_get_limit_pct` L67-74：30/68 开头 20%，8/4 开头 30%，其余 10%）。
> 涨停判定 `_is_limit_up`（L98-113）：`close_today >= prev_close * (1+limit_pct) * 0.995`（约 0.5% 容差，主板等价于涨幅 ≥9.45%）。

### Rule1 徐翔涨停板追涨 `_rule_xu_xiang`（L296-368）
| 条件 | 阈值 | 证据 |
|---|---|---|
| 昨日涨停 | `yesterday_close >= day_before_close*(1+limit)*0.995` | L321-324 |
| 今日高开 | `today_open > yesterday_close * 1.03`（≤3% 则 return None） | L333-334 |
| "封单量/流通市值" | 代码实为**当日成交额 `amount` / `total_mv`（stock_fundamental）> 0.5%** | L338-350 |
| 仓位 | `position_pct=30` | L356 |
| 触发价 / 止损 | `trigger=今日开盘价`；`stop_loss=昨日收盘*0.98` | L352-353 |
| 置信度 | `min(85, 50 + ratio*10)` | L355 |

> ⚠️ docstring 称"封单量/流通市值"，代码用当日成交额近似（无封单数据源）。

### Rule2 赵老哥二板定龙头 `_rule_zhao_laoge`（L370-434）
| 条件 | 阈值 | 证据 |
|---|---|---|
| 首板涨停 | `board1(daily[-3]).close >= daily[-4].close*(1+limit)*0.995` | L389-392 |
| 二板涨停 | `board2(daily[-2]).close >= daily[-3].close*(1+limit)*0.995` | L395-398 |
| 首板放量 | 首板量 / 前 5 日均量 ≥ 2.0（<2 则 return） | L401-407 |
| 二板缩量加速 | 二板量/首板量 < 0.8（≥0.8 则 return） | L410-412 |
| 仓位 | `position_pct=25` | L418 |
| 触发价 / 止损 | `trigger=二板收盘*1.01`；`stop=首板收盘*0.95` | L414-415 |
| 置信度 | `min(90, 60+(1-ratio)*50)` | L417 |

### Rule4 作手新一逻辑驱动 `_rule_zuoshou_xinyi`（L436-495）
| 条件 | 阈值 | 证据 |
|---|---|---|
| 今日大涨 | `daily_return > 5%` | L456-457 |
| 放量 | `_compute_volume_ratio`（今量/近5日均量）> 2.0 | L457-459 |
| 站上均线 | `today_close > MA20` | L461-463 |
| "非一日游" | docstring 声称"过去20天涨幅>5%天数"，代码算 `recent_bursts` 后**从未使用**（死代码） | L465-471 |
| 模拟机构净买 | `est_buy = amount * min(daily_return/10, 0.3)`（无龙虎榜表，近似） | L473-474 |
| 仓位 | `position_pct=20` | L483 |
| 触发价 / 止损 | `trigger=今日收盘`；`stop=min(MA20, 今日收盘*0.95)` | L482 |
| 置信度 | `min(80, 40 + est_buy_yi*5)` | L476 |

> ⚠️ docstring 称"龙虎榜机构净买>5000万"，实际用涨幅+量比近似。仓库**已有** `lhb_daily` 表（storage/__init__.py:70-80，含 `buy_amount/sell_amount/net_amount`）却未接入。

### Rule5 方新侠拐点博弈（超跌反弹）`_rule_fang_xinxia`（L497-559）
| 条件 | 阈值 | 证据 |
|---|---|---|
| 连续下跌 3 天 | 近 3 日（daily[-3..-1]）日收益均 <0 | L505-511 |
| 超卖 | `RSI14 < 30`（≥30 则 return） | L514-515 |
| 阳线 | `today_close > today_open` | L522-523 |
| 放量 | 量比 > 1.5 | L526-527 |
| 反弹涨幅 | `> 3%` | L531-533 |
| 仓位 | `position_pct=15` | L540 |
| 触发价 / 止损 | `trigger=今日收盘`；`stop=今日最低价` | L537-538 |
| 置信度 | `min(85, 40 + (30-rsi)*2 + vol_ratio*5)` | L539 |

---

## 4. 情绪闸门 `_get_market_sentiment`（L175-294）

**用到的表**：`stock_daily`（trade_date/open/close/volume）+ `stock_basic`（market='a' 过滤）。
**指标**：① 涨停梯队最高连板数 `max_boards`；② 昨日涨停股今日溢价率 `premium_pct`。

| status | 判定阈值 | 证据 |
|---|---|---|
| hot | `max_boards>=7 and premium_pct>5` | L274-275 |
| warm | `max_boards>=5 and premium_pct>2` | L276-277 |
| cool | `max_boards>=3 and premium_pct>=0` | L278-279 |
| cold | 其余（含无数据/无涨停） | L280-281 |

**在 `_check_symbol`（L688-737）中的闸门作用**：
- Rule1/Rule2 仅在 `hot/warm` 时执行（L691, L699）；
- Rule4 无条件执行，`hot/warm` 时置信度 +5（L706-714）；
- Rule5 无条件执行，`cool/cold` 时置信度 +10（逆周期）（L717-725）。

---

## 5. 退出逻辑 `exit_signals`（L561-636）

`exit_signals(position, market_data)` 按持仓的 `rule` 分派。**关键：它不自己查库，完全依赖外部传入 `market_data[symbol]` 提供现价/涨停/持仓天数等字段**。

| 规则 | 退出条件 | urgency/action | 依赖的外部字段 |
|---|---|---|---|
| 徐翔 | 炸板 `kaiban=True`；低开 `open_change_pct < -2` | immediate / sell_all | `kaiban`, `open_change_pct` |
| 赵老哥 | 断板 `not_limit_up=True` | close / sell_all | `not_limit_up` |
| 作手新一 | 持仓≥3天且 `total_return < 2`；反手 `net_sell_over_30m=True` | today / immediate / sell_all | `days_held`, `total_return`, `net_sell_over_30m` |
| 方新侠 | `total_return >= 5` 止盈；`current_low <= entry_low` 破新低止损 | today / immediate / sell_all | `total_return`, `current_low`, 持仓的 `entry_low` |

> ⚠️ `kaiban`/`not_limit_up`/`net_sell_over_30m` 等字段的**生产者在本模块内不存在**，需要外部系统喂入——但没有任何调用方，因此退出逻辑从未真正运行。

---

## 6. 数据依赖清单

| 表 | 字段 | 用途 | 证据 |
|---|---|---|---|
| `stock_daily` | trade_date, open, high, low, close, volume, amount | 全部规则 + 情绪闸门 | `_get_daily_list` L76-84 |
| `stock_basic` | symbol, name, market | 股票池（market='a'）、名称 | `_get_stock_info` L88-96, `scan_all` L653-658 |
| `stock_fundamental` | total_mv | 仅徐翔规则"成交额/总市值" | L338-344 |

**未使用但仓库已有**：`lhb_daily`（龙虎榜，本可服务作手新一）、`screen_results`、`hsgt_*`/北向/融资融券/ETF 资金流等大量资金面表。

---

## 7. 接入现状（CLI / 回测 / 推荐管道 / 交易）

| 系统 | 是否接入 | 证据 |
|---|---|---|
| CLI（`src/nous/cli.py`） | ❌ 无命令 | `screen` 用 coarse_filter+TRL（cli.py:72-160），无 short_term |
| 回测引擎（`engine/backtest/`） | ❌ 无 | `strategies.py` 的 `STRATEGIES` 注册表（L117-243）只含 海鹰F3/龙脉TRL/鳄鱼派/市场中性/指数增强/多因子综合，无游资规则；`engine.py` 只 import `strategies`/`data_handler`/`metrics`（engine.py:23-28）；`signal_engine.py` 是另一套独立信号（MA金叉+量比+RSI，L30） |
| 推荐管道（`daily_recommendation_pipeline.py`） | ❌ 无 | 走 coarse_filter 四池 + soul L2 + 风控（头部 docstring L1-10），无 short_term |
| trader（executor/order/scoring） | ❌ 仅同名字符串 | `strategy="short_term"` 是 trader 自己的策略枚举（order.py:64, executor.py:133 等），与本模块无关 |
| 调度（scheduler/jobs） | ❌ 无 | `trader_poll.py`/`trader_open_buy.py` 均不 import 本模块 |
| docstring 声称的 `theme_recommend.py` | ❌ 文件不存在 | `grep -rn "theme_recommend"` 仅命中本模块自己的 docstring L15 |

**唯一"疑似接口"痕迹**：`ShortTermEngine.entry_signals(self, db, market_data)`（L672-674）与 `exit_signals(self, position, market_data)`（L676-678）签名形似"供 trader 调用的信号接口"，但 `db`/`market_data` 参数**被完全忽略**（entry 直接 `return self.scan_all()`），说明是留了接口却没接上。

---

## 8. 结构缺口清单

| 缺口 | 现状 | 证据 |
|---|---|---|
| **权重体系** | 无。各规则 confidence 是各自硬编码公式（§3），无统一归一化/加权；`scan_all` 仅按 confidence 降序排序 | L668 |
| **资金面因子** | 仅"当日成交额/总市值"（徐翔）+ 量比。无北向、主力净流入、融资融券、ETF flow、龙虎榜真实净买 | §3、§6 |
| **宏观/大盘因子** | 无。情绪闸门是唯一的"市场级"因子，且仅作开关/置信度加成，非独立因子 | L691-725 |
| **止损/止盈参数化** | 全硬编码在规则内（徐翔 -2% / 赵 -5% / 作手 -5% / 方新侠 +5% & 破低），无外部配置 | §3、§5 |
| **大盘闸门之外的因子** | 无市场宽度、无波动率(ATR)、无板块/行业、无拥挤度 | 全文件 |
| **持仓管理/组合级风控** | `position_pct` 为固定建议值，无组合层权重/回撤控制 | EntrySignal L42 |
| **回测/样本外验证** | 无任何胜率/收益/换手统计；无 Walk-Forward/PIT 接入 | §7 |
| **参数化/配置注入** | 规则阈值全部字面量，无 config 注入 | 全文件 |

---

## 9. 数据质量 caveats / bug 清单

1. **🔴 致命：`_get_daily_list` 取错数据窗口**（L76-84）。SQL 为 `ORDER BY trade_date ASC LIMIT ?`，返回的是**全表最早的 N 条**，而非"最近 N 天"（docstring 声称"最近日线按日期升序"）。正确写法应 `ORDER BY trade_date DESC LIMIT N` 后反转。后果：所有规则把 `daily[-1]` 当"今天"，实际是"第 N 老的一天"，量比/RSI/MA/涨停/连板全部错位，**该引擎从未在正确数据上运行过**。
2. **死参数**：`_get_market_sentiment(db_path)`（L175）与 `ShortTermEngine.__init__(db_path)`（L642）的 `db_path` 被忽略——`_get_db()`（L60-64）始终读模块级 `DB_PATH`，传入路径不生效。
3. **死代码**：作手新一的"非一日游" `recent_bursts` 计算后从未使用（L465-471），docstring 声称的"非一日游"过滤实际未生效。
4. **近似实现 vs docstring**：徐翔"封单量/流通市值"实为"成交额/总市值"（L338-350）；作手新一"龙虎榜净买>5000万"实为涨幅+量比近似 + 线性估算（L473-474），而仓库已有 `lhb_daily` 表可真实支撑却未接。
5. **性能**：`_get_market_sentiment` 对每只涨停股各查一次 `_get_daily_list`（N+1），溢价循环内每只再开新连接（conn4，L246-251）；`max_boards` 仅统计前 50 只涨停股（L220）。
6. **涨停容差**：`_is_limit_up` 用 `*0.995`，主板等价于涨幅 ≥9.45% 即判涨停，可能把未封板纳入（L98-113）。
7. **无测试**：`tests/` 下零覆盖，无法回归验证上述行为（§1）。

---

## 10. 可复用组件清单（带函数名/行号）

| 组件 | 函数/位置 | 可复用性 | 说明 |
|---|---|---|---|
| 涨停判定（含涨跌幅限制分派） | `_get_limit_pct` L67-74, `_is_limit_up` L98-113 | ✅ 直接复用（修窗口后） | 纯函数、逻辑清晰 |
| 连板数统计 | `_get_consecutive_limit_up_count` L160-172 | ✅ 直接复用 | 依赖 `_is_limit_up` |
| RSI/MA/量比计算 | `_compute_rsi` L129-149, `_compute_ma` L151-158, `_compute_volume_ratio` L116-127 | ⚠️ 逻辑可复用，输入需接"最近 N 天"数据 | 纯 Python、无 pandas 依赖 |
| 四规则触发语义 | `_rule_*` L296/370/436/497 | ⚠️ 逻辑可移植为"可解释信号规格" | 阈值清晰但需参数化 + 修窗口 + 补数据源 |
| 退出规则语义 | `exit_signals` L561-636 | ⚠️ 语义可移植，接口需重设计 | 当前依赖外部喂字段，生产者缺失 |
| 情绪闸门语义 | `_get_market_sentiment` L175-294 | ❌ 建议重写 | N+1 查询 + 死参数 + 前50截断，指标本身（连板高度+溢价）可保留 |
| 数据访问层 | `_get_daily_list` L76-84 | ❌ 必须重写 | 致命窗口 bug，见 §9.1 |

---

## 11. 事实结论：改造 vs 并行

1. **并行新建反弹引擎是明确选择**。`short_term.py` 是孤立原型：零接入、零测试、数据访问层致命 bug，不存在"改造出可上线引擎"的捷径。
2. **可移植的是规则语义，不是代码地基**。4 条规则的触发阈值 + 止损/止盈语义（§3、§5）可整理成"可解释信号规格"（类似 `engine/backtest/strategies.py` 的 `Strategy` 注册项），纳入新引擎作为信号源或基准策略。
3. **接入回测的正确姿势**：新引擎应注册进 `STRATEGIES`（strategies.py:117）或走 `signal_engine.py` 那类独立信号评估器，而非复用 `short_term.py` 的私有 sqlite 帮助函数。
4. **数据层必须重写**：先修 `_get_daily_list` 的窗口 bug（§9.1），或直接改用仓库现成的 `nous.data.storage.get_db` / `query_engine.get_multi_daily_df`（screener.py:8 已示范）。
5. **缺口必须在设计中补**：权重体系、止损止盈参数化、资金面（北向/龙虎榜 `lhb_daily`/主力净流入）、大盘闸门之外的市场宽度/波动率因子、回测验证——现有模块六项全缺（§8）。
