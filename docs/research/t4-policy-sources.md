# T4 政策/事件数据源探源 — A股反弹引擎政策因子

> 状态：已实测（curl / akshare 1.18.63 / 直接跑代码验证），非印象流。
> 验证日期：2026-08-24。对应 issue #5「政策/事件数据源探源」。
> 结论先行：**政策要闻**与**涨停原因/板块异动**均有免费、结构化、可历史回填的可用主源；唯一需要逆向的反例是**财联社电报（cls.cn）**，建议降级为备源/放弃，改用新浪政策频道 + 东财要闻替代。

---

## 0. 现有代码基线（nous 已有什么）

nous 并非零基础，已有两套政策采集器和一套情绪采集器，本报告的推荐方案应**复用而非重写**：

| 现有模块 | 文件:行 | 数据源 | 状态 |
|---|---|---|---|
| 政策雷达（四层） | `src/nous/data/collectors/fetchers/policy_radar.py:64` (SINA_FEED_CHANNELS), `:389` (fetch_sina_feed), `:213` (fetch_macro_indicators) | 新浪 Feed API + akshare 宏观 + 2025eyp | 新浪 Feed 已实测可用 |
| 宏观政策采集器（简单版） | `src/nous/data/collectors/fetchers/policy.py:26` (POLICY_SOURCES) | gov.cn / pbc / ndrc / csrc HTML 正则 | 站点均 HTTP 200 可爬 |
| 同花顺 JS challenge（hexin-v） | `src/nous/data/collectors/fetchers/fund_flow.py:22` (_gen_v_code) | 10jqka `ths.js` → `hexin-v` token | 已实测可复用 |
| 情绪采集（涨停/炸板/连板） | `src/nous/data/collectors/fetchers/sentiment.py:133` | akshare `stock_zh_a_spot` + screener.db | 无「涨停原因」字段 |
| 东财行情直连（push2 系列） | `src/nous/data/collectors/unified.py:59` (_fetch_a_spot_em) | `push2delay/push2/82.push2.eastmoney.com` | 生产在用 |

依赖：`akshare>=1.14,<2.0`（实测安装 1.18.63）、`curl-cffi`、`py-mini-racer`（fund_flow 已用）见 `pyproject.toml:24-40`。

---

## 1. 政策要闻类

### 1.1 新浪财经 Feed API（✅ 推荐主源之一）

- **接口**：`https://feed.mix.sina.com.cn/api/roll/get?pageid=155&lid=<LID>&num=<N>&page=<P>`
- **nous 已有频道映射**：`policy_radar.py:64-74` —— 宏观 lid=1686、政策 lid=1689、产业 lid=1690、证券 lid=1691、地产 lid=1692、科技 lid=1693、消费 lid=1695。
- **实测结果**（2026-08-24 curl）：返回 JSON `{"result":{"status":{"code":0},"total":100549,"data":[...]}}`；单条含 `title / url / intro / ctime(Unix秒) / media_name / keywords`。
- **翻页/历史深度**：`page` 参数有效（page=1/2/10 均返回不同 ctime 区间），`total≈10万`，可按时间倒序回溯，历史深度充足。
- **免费性**：免费、无需 key。
- **更新延迟**：分钟级（`ctime` 为实时 Unix 时间戳，实测比当下早 ~30 分钟）。
- **结构化程度**：高（JSON，字段稳定）。
- **维护成本**：低（官方 feed，无签名/无 JS 挑战）；需自行维护 lid→频道语义映射（已存在于 policy_radar.py）。
- **稳定性**：高（新浪官方 feed，已存在多年，nous 正在用）。

### 1.2 东方财富要闻（✅ 推荐主源之二）

- **接口**：`https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=350&order=1&needInteractData=0&page_index=<N>&page_size=<M>&req_trace=1`
  - `column=350` 为「财经要闻」频道（实测）；另有证券/公司/研报等 column id 可扩展。
- **实测结果**（2026-08-24 curl）：JSON `{"code":"1","data":{"list":[...]}}`；单条含 `title / summary / showTime / uniqueUrl / code / image`。page_index=10 仍返回当日数据，翻页深度可用。
- **免费性**：免费、无需 key。
- **更新延迟**：分钟级（`showTime` 精确到秒）。
- **历史深度**：可 `page_index` 翻页；单频道按时间倒序。
- **结构化程度**：高（JSON）。
- **维护成本**：低（无签名）；需带 `Referer: https://finance.eastmoney.com/`。
- **稳定性**：高（东财官方资讯接口）。

### 1.3 新闻联播文字稿（✅ 政策要闻「顶格」来源）

- **接口**：akshare `news_cctv(date="YYYYMMDD")`，底层 `https://tv.cctv.com/lm/xwlb` 逐条抓全文。
- **实测结果**（2026-08-24）：`date=20260821` 返回 13 条 `[date, title, content]`，含完整正文（政策/领导人/宏观表态）。
- **免费性**：免费。
- **更新延迟**：当日播出后晚间可用（约 19:30+）。
- **历史深度**：2016-02 起（akshare 源码注明 `20160203` 后）。
- **结构化程度**：中（标题+全文文本，非事件化字段）。
- **维护成本**：低（走 akshare）；正文需 LLM 二次抽取「利好/利空板块」。
- **稳定性**：高（央视官方）。

### 1.4 财联社电报（cls.cn）（⚠️ 实测已失效，需逆向 sign，降级）

- **接口历史**：akshare `stock_info_global_cls()` 曾用 `https://www.cls.cn/nodeapi/telegraphList`。
- **实测结果**（2026-08-24）：
  - `GET nodeapi/telegraphList` → **HTTP 404**（akshare 直接报 `APIError: 404`）。
  - `POST v1/roll/get_roll_list` → 返回 JSON 但 `{"errno":50101,"msg":"小财正在加载中..."}`（**缺 sign 签名**）。
  - 网页根路径返回 Next.js 应用壳（无数据）。
- **结论**：cls.cn 电报已改为**签名（sign）+ 前端渲染**反爬，`nodeapi/telegraphList` 端点已死；要用需逆向 sign 算法。
- **免费性**：免费但不可稳定获取。
- **维护成本**：**高**（需逆向 + 随时可能再变）。
- **建议**：不作为主源；如需「快讯」可用新浪「证券」频道（lid=1691）与东财要闻替代，或降级为人工盯盘。

### 1.5 政府官网公告（✅ 备源，nous 已实现）

- **站点与实测 HTTP 状态（2026-08-24 全 200）**：
  | 站点 | URL | 来源引用 |
  |---|---|---|
  | 国务院 | `https://www.gov.cn/lianbo/bumen/index.htm` | `policy.py:29` |
  | 央行 | `http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html` | `policy.py:34` |
  | 发改委 | `https://www.ndrc.gov.cn/xwdt/xwfb/` | `policy.py:38` |
  | 证监会 | `http://www.csrc.gov.cn/csrc/c100028/common_list.shtml` | `policy.py:42` |
  | 工信部 | `https://www.miit.gov.cn/xwdt/gxdt/sjdt/` | `policy_radar.py:146` (GOV_POLICY_URLS) |
- **免费性**：免费。
- **更新延迟**：不定（公告日更，非实时）。
- **历史深度**：深（官网归档多年）。
- **结构化程度**：**低**（纯 HTML，需正则/BeautifulSoup 解析，`policy.py:52` fetch_source 已是正则抽取 `<a>` 标题+链接）。
- **维护成本**：**中高**（各站 HTML 结构不定期改版，正则易碎）。
- **稳定性**：中（政府站偶有反爬/改版）。

---

## 2. 板块异动与涨停原因

### 2.1 同花顺涨停原因（✅ 核心主源，含 `reason_type` 涨停原因）

- **接口**：`https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool`
  - 参数：`page / limit / field / filter=HS,GEM2STAR / order_field=330324 / order_type=0 / date=YYYYMMDD`
  - 必带头：`hexin-v`（JS challenge 生成，nous 已有：`fund_flow.py:22` `_gen_v_code()` 用 `py_mini_racer` 执行 `ths.js`）。
- **实测结果**（2026-08-24，生成 hexin-v 后直连）：
  - 返回 JSON，单条关键字段：`code / name / reason_type(涨停原因) / high_days(连板，如"2天2板") / limit_up_type(换手板/一字板) / first_limit_up_time / last_limit_up_time / turnover_rate / change_rate`。
  - 样例：`002491 通鼎互联 "中报扭亏+光纤光缆+储能安防"`；`301591 肯特股份 "PCB覆铜板+PEEK材料+高性能工程塑料"`。
- **历史回填**：`date` 参数有效 —— `20260821→total=54`、`20260820→total=78`、`20260701→total=149`，可回溯补历史。
- **免费性**：免费、无需 key（但需 hexin-v）。
- **更新延迟**：收盘后可用（约 15:00+，含当日封板时间）。
- **历史深度**：深（date 可回填，至少数月）。
- **结构化程度**：**高**（JSON，`reason_type` 正是反弹引擎需要的「涨停原因」）。
- **维护成本**：中（hexin-v 每请求/每页需重算，`fund_flow.py:76` 已示范「每页重新生成」；token 会过期需重算）。
- **稳定性**：中高（同花顺 dataapi 长期稳定，但偶发风控）。

> 这是「涨停原因分类」的直接答案 —— **东方财富涨停池不含原因**（见 2.2），原因字段只有同花顺有。

### 2.2 东方财富涨停池（✅ 辅源，无原因但有连板/封板资金）

- **接口**：akshare `stock_zt_pool_em(date=...)` → `https://push2ex.eastmoney.com/getTopicZTPool`
  - 参数：`ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=10000&sort=fbt:asc&date=YYYYMMDD`。
- **实测结果**（2026-08-24）：返回 54 只涨停，字段：`代码/名称/涨跌幅/成交额/流通市值/换手率/连板数(lbc)/首次封板时间/最后封板时间/封板资金/炸板次数/所属行业(hybk)/涨停统计(zttj)`。**无 `涨停原因`**。
- **免费性**：免费、无需 key。
- **更新延迟**：实时/收盘后。
- **历史深度**：date 可回填。
- **结构化程度**：高（JSON）。
- **维护成本**：低（akshare 封装，无需签名）。
- **稳定性**：高（东财 push2ex 系列）。
- **用法建议**：与 2.1 同花顺原因**按 code+date 关联**，补连板/封板资金/所属行业。

### 2.3 东方财富板块异动（✅ 板块事件辅源）

- **接口**：akshare `stock_board_change_em()` → `https://push2ex.eastmoney.com/getAllBKChanges?ut=...&dpt=wzchanges&pageindex=0&pagesize=5000`。
- **实测结果**（2026-08-24）：1005 条，字段：`板块名称/涨跌幅/主力净流入/板块异动总次数/异动最频繁个股(代码+名称+买卖方向)/异动类型列表(ydl: t=类型码, ct=次数)`。
- **免费性**：免费；**延迟**：当日实时；**历史**：仅当日（无历史参数）；**结构化**：高；**维护**：低；**稳定**：高。

### 2.4 概念板块（✅ 已有生产链路）

- **接口**：`https://79.push2.eastmoney.com/api/qt/clist/get?fs=m:90 t:3 f:!50&fields=...`（akshare `stock_board_concept_name_em` / `stock_board_concept_spot_em`）；nous `unified.py:59` 生产已在用 `push2delay/push2/82.push2` 三主机轮换。
- **同花顺概念**（备）：`stock_board_concept_name_ths` / `stock_board_concept_cons_ths`（需 hexin-v）。
- **nous 侧**：`stock_concept_map` 表 + `src/nous/engine/signals/concept_signals.py` 已在计算概念板块涨幅/动量/主线。

---

## 3. akshare 中可用的 policy/news/event 接口清单（实测 1.18.63）

| akshare 函数 | 数据 | 实测状态 |
|---|---|---|
| `stock_info_global_cls` | 财联社电报 | ❌ 404（端点失效） |
| `stock_news_em(symbol)` | 东财个股新闻（10条，含关键词/标题/内容/时间/来源/链接） | ✅ 已验证 |
| `news_cctv(date)` | 新闻联播文字稿 | ✅ 已验证 |
| `stock_zt_pool_em(date)` | 东财涨停股池 | ✅ 已验证 |
| `stock_zt_pool_previous_em` / `strong_em` / `zbgc_em` / `dtgc_em` | 昨日涨停/强势/炸板/跌停池 | 同系列，未逐一测但同源 |
| `stock_board_change_em` | 东财板块异动 | ✅ 已验证 |
| `stock_board_concept_name_em` / `spot_em` | 东财概念板块 | ✅ 同 push2 链路 |
| `stock_board_concept_name_ths` / `cons_ths` | 同花顺概念板块 | 需 hexin-v（已有） |
| `stock_research_report_em` | 东财个股研报 | 存在，未测 |
| `stock_notice_report` / `stock_individual_notice_report` | 公告 | 存在，未测 |

> akshare **没有**「同花顺涨停原因」封装，需按 2.1 直连 `data.10jqka.com.cn/dataapi/limit_up/limit_up_pool`（本文已给出可用参数）。

---

## 4. 推荐方案

### 主源（直接落地，nous 已具备全部依赖）

| 数据 | 主源 | 接口 | 落地点 |
|---|---|---|---|
| 政策/事件要闻 | 新浪 Feed（政策 lid=1689 / 宏观 lid=1686 / 产业 lid=1690） + 东财要闻 column=350 | 1.1 + 1.2 | 复用 `policy_radar.py`，新增东财要闻 fetcher |
| 政策要闻顶格 | 新闻联播文字稿 | `news_cctv` | 新 fetcher（日更一次） |
| **涨停原因** | 同花顺 dataapi | 2.1 | **新 fetcher**（复用 `fund_flow.py:22` 的 hexin-v） |
| 涨停连板/封板 | 东财涨停池 | 2.2 | 新 fetcher（akshare 封装即可） |
| 板块异动 | 东财 getAllBKChanges | 2.3 | 新 fetcher |

### 备源（降级/交叉验证）

- 政府官网 HTML（`policy.py` 已实现）：gov.cn / pbc / ndrc / csrc / miit —— 作为政策原文回查。
- 东财个股新闻 `stock_news_em`：按标的回查「利好/利空」事件。
- 同花顺概念板块 `stock_board_concept_*_ths`：东财概念失效时替代。

### 明确放弃

- **财联社电报 cls.cn**：`nodeapi/telegraphList` 已 404，`v1/roll/get_roll_list` 需 sign 逆向（errno 50101）。用新浪证券频道 + 东财要闻覆盖快讯需求，不再投入逆向。

---

## 5. Fallback：无可用源时的人工标注输入表设计

当程序化主源/备源全部不可用（如东财/同花顺/新浪同时限流或改版）时，退回**人工标注输入表**，由 LLM/人工填好后导入反弹引擎政策因子。

建议字段（CSV/JSONL，UTF-8，一事件一行）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `date` | YYYY-MM-DD | ✅ | 事件发生/公告日（交易口径） |
| `target` | string | ✅ | 直接标的股票代码（6位，如 `600519`）；板块级事件可留空或填 `*` |
| `sector` | string | ✅ | 影响板块（建议复用 `policy_radar.py:36-60` SECTOR_KEYWORDS 的 13 板块分类） |
| `event_type` | enum | ✅ | `policy(政策) / regulation(监管) / industry(产业) / macro(宏观) / company(公司公告) / sentiment(舆情) / limit_up_reason(涨停原因)` |
| `direction` | enum | ✅ | `bullish(利好) / bearish(利空) / neutral(中性)` |
| `source` | string | ✅ | 来源（新华社/证监会/央行/财联社/东财/人工判断…） |
| `confidence` | 0.0–1.0 | ✅ | 置信度（人工=1.0，LLM 推断=0.5–0.9） |
| `title` | string | 建议 | 事件标题/一句话 |
| `url` | string | 可选 | 原文链接（可溯源） |
| `impact_level` | enum | 可选 | `high / medium / low` |
| `note` | string | 可选 | 备注（如「有效期 N 日」「已被市场计价」） |

**导入约定**：
- `target` 为个股时，按 `date` 落在该标的反弹引擎的候选池中加权；`sector` 级事件按 `stock_concept_map` 扩散到成分股（对应 `concept_signals.py` 现有表）。
- `confidence` 直接作为政策因子的权重系数，避免把人工标注与程序化信号等权混用。
- 表可落盘为 `~/wiki/finance/raw/policy/manual/YYYY-MM-DD.jsonl`，与 `policy_radar.py` 输出目录（`~/wiki/finance/raw/policy/`）并列，由统一 collector 合并。

---

## 6. 数据质量 Caveat（实测发现）

1. **东财涨停池 `date` 参数非严格**：实测 `date=20260821` 返回 `qdate=20260824`（非交易日或当天会回落到最近交易日），历史回填需用确定交易日并核对返回 `qdate`。
2. **同花顺涨停原因依赖 hexin-v**：token 会过期、需每页重算（`fund_flow.py:76`），且偶发风控返回空 `info`，需加重试与降级到东财涨停池。
3. **cls.cn 已失效**：`stock_info_global_cls` 在 akshare 1.18.63 中 404，网上流传的 `telegraphList` 端点已死，勿按旧文档实现。
4. **政府官网为 HTML 无 JSON**：结构改版即碎（`policy.py` 正则），需定期巡检，建议只用作文本回查而非实时信号。
5. **新浪 Feed 频道 lid 语义需自行维护**：`policy_radar.py:64` 的 lid 映射是历史约定，若新浪调整频道需人工核对返回 `title/media_name` 是否仍属该频道。
6. **板块异动 `getAllBKChanges` 仅当日**：无历史参数，需每日落地存库才能积累历史。
