# 光伏玻璃同业（福莱特 601865/06865、信义能源 03868）数据源实测报告

日期：2026-09-16 · 目标：把 `nous pv` 跟踪面从信义光能 00968.HK 扩展到同级跟踪 福莱特 + 信义能源

> **工具链限制（必读）**：本次运行**没有 bash/curl**，全部 HTTP 通过 `fetch_content`（**仅 GET、不能自定义 header/cookie、不能 POST**）完成。
> 因此：① cninfo `hisAnnouncement/query`（POST-only）**未能执行**，只给出源码级契约，标记为「未实测」；
> ② `.venv/bin/python` **无法运行**，akshare 函数用「已安装包源码 + dist-info/RECORD」验证存在性与返回列，而非执行验证。
> 所有写成「已实测」的条目，都是本客户端真实返回过 200/JSON/PDF 的。

---

## 1. 港交所披露易 stockId 反查（已实测）

`GET https://www1.hkexnews.hk/search/prefix.do?callback=cb&lang=ZH&type=A&name=<代码>&market=SEHK`

| 代码 | 名称 | **stockId** | 原文（截取） |
| --- | --- | --- | --- |
| 06865 | 福萊特玻璃 | **133883** | `callback({"more":"1","stockInfo":[{"stockId":133883,"code":"06865","name":"福萊特玻璃"},...]})` |
| 03868 | 信義能源 | **209025** | `callback({"more":"1","stockInfo":[{"stockId":209025,"code":"03868","name":"信義能源"}]})` |
| 00968 | 信義光能（回归校验） | **97365** | `callback({"more":"1","stockInfo":[{"stockId":97365,"code":"00968","name":"信義光能"},...]})` |

- 返回体是 **JSONP**（`callback(...)`），需剥壳后 `json.loads`。`stockInfo[0]` 即目标，后面会跟一堆同前缀权证/牛熊证，**不能取数组首元素就完事**——必须按 `code` 精确匹配。
- **cookie 步骤实测结论**：本客户端先 `GET titlesearch.xhtml` 再调 `prefix.do`，但 `prefix.do` / `titleSearchServlet.do` 在**不带 cookie** 的情况下同样返回 200 + 完整 JSON。无法排除服务端对匿名请求限流，**生产建议保留现有「先取 cookie」实现**，但不要把它当作硬依赖。
- 公告列表接口实测可用（GET 即可，无需 cookie）：
  `GET https://www1.hkexnews.hk/search/titleSearchServlet.do?sortDir=0&sortByOptions=DateTime&category=0&market=SEHK&stockId=209025&documentType=-1&fromDate=20260601&toDate=20260916&title=&searchType=1&t1code=-2&t2Gcode=-2&t2code=-2&rowRange=100&lang=ZH`
  返回 `{"result":"[{\"FILE_LINK\":\"/listedco/listconews/sehk/2026/0731/2026073100623_c.pdf\", \"DATE_TIME\":\"31/07/2026 16:53\", \"TITLE\":\"...中期業績公告\", ...}]"}`（`result` 是**被转义的 JSON 字符串**，需二次 `json.loads`）。完整 PDF 路径 = `https://www1.hkexnews.hk` + `FILE_LINK`；`_c` = 繁体中文，`_e` = 英文。`stockId=133883` 实测返回 43 条（2026-07-01~09-16），`stockId=209025` 返回 23 条。

---

## 2. 福莱特 A 股公告接口

### 2.1 cninfo `hisAnnouncement/query` —— GET 不可用（已实测）；POST 契约（未实测，源码级）

| 事实 | 证据 |
| --- | --- |
| `GET http://www.cninfo.com.cn/new/hisAnnouncement/query`（裸路径） | 返回 **HTTP 200 + cninfo 404 页面**（"很抱歉，您访问的页面不存在"） |
| `GET ...?stock=601865,gssz&tabName=fulltext&pageSize=30&pageNum=1&column=sse&plate=sh&seDate=` | 同样返回 **404 页面** → 该路由**只接受 POST**，GET 无论如何拼参都不通 |
| **任务假设的 `stock=601865,gssz` 是错的** | akshare 1.18.91 源码里 `stock` 字段是 `f"{symbol},{orgId}"`，`orgId` 来自 `http://www.cninfo.com.cn/new/data/szse_stock.json`。**实测该 JSON 中 601865 → `orgId=9900031502`、`zwjc=福莱特`**（答案模式引用，code 精确匹配）。`gssz` 是 `column=szse` 的旧写法混用，邮不到正确记录。 |

**未实测的 curl（POST 契约来自已安装的 akshare 1.18.91 源码 `akshare/stock_feature/stock_disclosure_cninfo.py`）**

```bash
curl -sS -X POST 'http://www.cninfo.com.cn/new/hisAnnouncement/query' \
  -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \
  -H 'User-Agent: Mozilla/5.0' \
  -H 'Referer: http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search' \
  -H 'X-Requested-With: XMLHttpRequest' \
  --data-urlencode 'pageNum=1' \
  --data-urlencode 'pageSize=30' \
  --data-urlencode 'column=szse' \
  --data-urlencode 'tabName=fulltext' \
  --data-urlencode 'plate=' \
  --data-urlencode 'stock=601865,9900031502' \
  --data-urlencode 'searchkey=' \
  --data-urlencode 'secid=' \
  --data-urlencode 'category=' \
  --data-urlencode 'trade=' \
  --data-urlencode 'seDate=2026-08-01~2026-09-16' \
  --data-urlencode 'sortName=' \
  --data-urlencode 'sortType=' \
  --data-urlencode 'isHLtitle=true'
```

- 期望 JSON 顶层键：`totalAnnouncement`、`announcements[]`（每项含 `secCode/secName/announcementTitle/announcementTime(ms)/announcementId/orgId`，分页 `pageSize=30`）。
- akshare 源码**未设置任何 header**（裸 `requests.post(url, data=payload)`），说明服务端对 header 不敏感；但正文 PDF 链接需自行拼 `http://www.cninfo.com.cn/new/disclosure/detail?stockCode=&announcementId=&orgId=&announcementTime=`。
- **上线前必须在有 bash 的环境跑一次上面的 curl 确认 200+JSON**（本次未能执行）。

### 2.2 已实测可用的替代 A（推荐先落地）：东财公告 API（纯 GET）

```
GET https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=20&page_index=1&ann_type=A&client_source=web&stock_list=601865&f_node=0&s_node=0
```

- **实测返回 200 + JSON**：`{"data":{"list":[...],"page_index":1,"page_size":20,"total_hits":1710},"success":1}`（每项含 `art_code`、`title`、`notice_date`、`display_time`、`columns[].column_name`、`codes[].stock_code`，且**半年度报告条目带 `financial_report` 摘要字段**）。
- 实测抓到的关键条目：`AN202608251828419140` = 《2026年半年度报告》、`AN202608251828419162` = 《2026年半年度报告摘要》、`AN202608251828413879` = 《福莱特H股公告(截至二零二六年六月三十日止六个月的中期业绩公告)》。
- PDF 直链规律实测有效：`https://pdf.dfcfw.com/pdf/H2_<art_code>_1.pdf`（返回 `application/pdf`，非 404；`fetch_content` 的 raw 模式拒收 PDF，需用 readable/answer）。
- 分类过滤：`f_node` ∈ {0 全部,1 财务报告,2 融资公告,3 风险提示,4 信息变更,5 重大事项,6 资产重组,7 持股变动}（来自 akshare `stock_fundamental/stock_notice.py`）。

### 2.3 已实测可用的替代 B（本次发现的更优解）：港交所「海外監管公告」镜像

`titleSearchServlet.do` 中 `stockId=133883`（福莱特）**把 A 股公告原文整包镜像过去了**，`LONG_TEXT` 标注为 `公告及通告 - [海外監管公告-…]`，文件名即 A 股公告标题，简体中文。实测可直接抓：

| 内容 | URL（实测 200，可抓） |
| --- | --- |
| 2026 半年度报告（全文，220 页 / 5MB） | `https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082501121_c.pdf` |
| 2026 半年度报告摘要 | `https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082501137_c.pdf` |
| 2026 上半年业绩预亏公告 | `https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0714/2026071400395_c.pdf` |
| 2026 半年度业绩说明会情况公告 | `https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0907/2026090700881_c.pdf` |
| 中期业绩公告（H 股口径，含分部毛利表） | `https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0825/2026082500671_c.pdf` |

> **工程建议**：福莱特完全走「HKEX 一个采集器、两个 stockId（97365 / 133883 / 209025）」即可覆盖三家公司，**绕开 cninfo POST 与反爬**；cninfo/东财只作补充（A 股公告更及时，H 股镜像约滞后数小时到 1 天）。

### 2.4 akshare 1.18.91 可用函数（源码级验证，非执行验证）

`.venv` 为 **Python 3.13.1**；`akshare-1.18.91.dist-info/METADATA` 确认版本 `1.18.91`（`Requires-Python >=3.11`）。

| 函数 | 模块（`.venv/lib/python3.13/site-packages/akshare/…`） | 接口 / 方法 | 返回列 | 用途判断 |
| --- | --- | --- | --- | --- |
| `stock_zh_a_disclosure_report_cninfo` | `stock_feature/stock_disclosure_cninfo.py` | **POST** cninfo `hisAnnouncement/query`，`column=szse`，`stock="{symbol},{orgId}"`，`seDate` 为 `YYYY-MM-DD~YYYY-MM-DD` | `代码, 简称, 公告标题, 公告时间, 公告链接` | ✅ 最贴合「巨潮公告查询」；但默认 `pageSize=30` 且**逐页 POST**，仍受 POST-only 限制 |
| `stock_zh_a_disclosure_relation_cninfo` | 同上 | POST，`tabName="relation"` | 同上 | 预约披露调研 |
| `stock_notice_report` / `stock_individual_notice_report` | `stock_fundamental/stock_notice.py` | **GET** `np-anotice-stock.eastmoney.com`（`page_size=100`，逐页 GET） | `代码, 名称, 公告标题, 公告类型, 公告日期, 网址` | ✅ **本次实测该 GET 接口可用**，是绕过 cninfo POST 的首选 |
| `stock_yjyg_cninfo` | `stock_feature/stock_yjyg_cninfo.py` | cninfo 业绩预告 | — | 补「盈警/业绩预告」事件 |
| `stock_profile_cninfo` / `stock_industry_cninfo` / `stock_irm_cninfo` / `stock_dividend_cninfo` / `stock_allotment_cninfo` / `stock_hold_num_cninfo` / `stock_hold_control_cninfo` / `stock_share_changes_cninfo` / `stock_industry_pe_cninfo` / `stock_ipo_summary_cninfo` | `stock/*.py` | cninfo 系 | — | 备用 |
| `stock_finance_hk_em` | `stock_fundamental/stock_finance_hk_em.py` | 东财港股财报 | — | 补 03868/06865 港股财报 |
| `stock_profit_forecast_hk_etnet` | `stock_fundamental/stock_profit_forecast_hk_etnet.py` | etnet 盈利预测 | — | 与现有 `etnet` 采集器同源，可直接替换自研解析 |

> **注**：本次**未执行任何 Python**，上表「返回列」取自源码中的 `rename`/列选择语句，属源码事实；`stock_notice_report` 的底层 URL 则由我实测 200+JSON 佐证。`stock_zh_a_disclosure_report_cninfo` 的 **POST 行为未实测**。

---

## 3. 福莱特 2026H1 关键经营数据

来源（全部已实测抓取）：`…/2026/0825/2026082500671_c.pdf`（中期業績公告）、`…/2026/0825/2026082501137_c.pdf`（A 股半年报摘要镜像）、`…/2026/0714/2026071400395_c.pdf`（预亏公告）、`…/2026/0907/2026090700881_c.pdf`（业绩说明会）。

| 指标 | 2026H1 | 2025H1 | 变动 | 证据强度 |
| --- | --- | --- | --- | --- |
| 营业收入 | **66.68 亿元**（6,668,210,124.99 元） | 77.37 亿元 | **-13.81%** | 高（利润表 + 摘要 + 新闻三重一致） |
| 营业成本 | **61.23 亿元**（6,123,036,574.97 元） | 66.50 亿元 | **-7.92%** | 高 |
| 毛利 | **5.45 亿元**（545,173,550 元） | 10.87 亿元 | -49.85% | 高 |
| **整体毛利率** | **8.18%** | 14.05% | -5.87pct | 高（3 处独立一致：利润表倒算、分部表合计、新闻「营业成本降低 7.92%」交叉验证） |
| **光伏玻璃分部收入** | **58.62 亿元**（5,861,594.65 千元），占比 **87.90%** | 69.45 亿元，89.76% | **-15.60%** | 高（分部表；占比与半年报正文「87.90%」一致） |
| **光伏玻璃分部毛利率** | **6.73%**（分部毛利 3.94 亿元） | 12.31%（8.55 亿元） | -5.58pct | 高（分部表 8 行合计精确等于 5.45 亿总毛利，构造性验证） |
| 利润总额 | -4.28 亿元（-427,726,884.82） | 2.75 亿元 | — | 高 |
| **归母净利润** | **-3.63 亿元**（-362,771,115.32） | +2.66 亿元 | **-236.4%**（新闻口径 -239.04%） | 高（摘要 -362.8、利润表、EPS-0.16 元、多家媒体一致） |
| 基本 EPS | **-0.16 元** | 0.11 元 | — | 高 |
| 扣非归母 | 见「矛盾」章节（-4.38 亿 vs -3.76 亿） | +2.27 亿元 | — | **低（未解决）** |
| 2026Q2 单季毛利率 | **0.49%**（同比 -16.16pct、环比 -13.95pct） | — | — | 中（OFweek 转述，与 H1 8.18% 自洽） |
| 总资产 / 总负债 | 415.34 亿 / 197.71 亿元 | — | — | 中（媒体转述 + PDF 数字可对） |

**在产日熔量 / 产能**

| 数据点 | 值 | 来源 | 可信度 |
| --- | --- | --- | --- |
| 越南基地产能 | **2,000 吨/天**（2026H1 当地成本上升、盈利收窄） | 业绩说明会公告（HKEX，2026-09-07）**已实测抓取**，原文「目前越南产能规模为 2,000 吨/天」 | **高（公司自述）** |
| 2026 年 9 月初 | 公司**主动冷修**部分产能，未给具体吨数 | 同上，原文「我司近期的产能冷修，主要是基于公司对行业供需走势及自身产能盈利能力的独立判断而做出的主动决策」 | **高（公司自述，但无数字）** |
| 累计在产总产能 **19,100 吨/日**（含越南 2,000） | 时点存疑 | OFweek 文章（2026-08，已实测抓取）：「今年一季度…合计新增产能 2700 吨/天；截至 3 月底…19,100 吨/日；南通和安徽项目将根据市场供需择机点火投产」 | **低（"今年"=2026Q1 还是 2025Q1 无法判定，疑为 2025 年报「产能布局」段落）** |
| 16,400 吨/天 | 2025-09 末 | 东北证券研报（慧博摘要，2025） | 中（但已过期） |
| 2025 年冷修 3 座窑炉合计 3,000 吨/日 | 2025 全年 | OFweek（转述） | 中 |

> **⚠️ 未取到**：**2026H1 时点公司自报的在产日熔量**。`2026082500671_c.pdf`（220 页）与 A 股半年报 PDF 抽取时**中文被 CID 字体吞掉、只留数字**，无法定位到产能段落文字。建议：① 用 pdfplumber/`pdftotext -layout` 在本地重抽 `2026082501121_c.pdf` 第 9–17 页（对应「管理层讨论与分析」）；② 或从中信/国泰海通《福莱特2026年半年报点评》取数（本次 `fxbaogao.com` 详情页抓取失败）。

---

## 4. 信义能源 03868 2026H1 关键数据

主来源（已实测抓取）：`https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0731/2026073100623_c.pdf`（截至 2026-06-30 六个月中期业绩公告，52 页，2026-07-31 16:53 发布）；辅证：证券之星 MD&A 转载、老虎证券/智通财经快讯。

| 指标 | 2026H1 | 2025H1 | 变动 |
| --- | --- | --- | --- |
| 收益 | **10.52 亿元**（1,051.7 百万 / 1,051,693 千元） | 12.10 亿元（1,210.2 百万） | **-13.1%** |
| 毛利 | 5.44 亿元（544,106 千元） | 7.47 亿元（747,421 千元） | -27.2%（毛利率 51.7% vs 61.8%） |
| 经营溢利 | 5.04 亿元 | 7.31 亿元 | -31.0% |
| 除税前溢利 | 4.17 亿元（417,108 千元） | 5.85 亿元 | -28.7% |
| 所得税 | -0.97 亿元 | -1.35 亿元 | -28.1% |
| **权益持有人应占溢利** | **3.20 亿元**（319.788 百万元） | 4.50 亿元（449.842 百万） | **-28.9%** |
| EPS | 3.76 分（人民币） | 5.37 分 | -30.0% |
| 中期股息 | 2.1 港仙 | 2.9 港仙 | -27.6% |
| 可再生能源业务收益 | 10.06 亿元（占 95.7%） | — | -16.8% |
| ⤷ 电力销售 / 电价调整 | 6.63 亿 / 3.35 亿元 | — | -10.4% / -28.1% |
| 总资产 / 总负债 / 股东权益 | 220.74 / 87.83 / 132.88 亿元 | — | 资产负债率 39.8%，净负债率 44.9%（上年同期 47.9%） |
| 资本开支 | 1.54 亿元 | — | 用于既有项目结算 + 收购新西兰附属公司 |
| 总发电量 | 同比 **-10.7%** | — | 限电损失↑、市场化电价↓、天气，及 2025 年出售信义光能（天津）51% 权益后不再并表 |

**装机容量 —— 口径提醒（决策相关）**

原文（MD&A，证券之星转载，已实测检索命中；同一组数字在 PDF 第 36/38 页数字块中以 `4,630.5 / 1,624 / 3,006.5 / 174` 出现，且 780+540+340+660+520+636.5+860+294 = 4,630.5 可复算）：

> 「截至二零二六年六月三十日，本集团**合共运营并持有核准发电容量达 4,630.5 兆瓦**的大型可再生能源业务项目，其中 **1,624 兆瓦属补贴政策**及 **3,006.5 兆瓦属平价上网政策**；另持有**一项以权益法入账、容量达 174 兆瓦且属补贴政策**的可再生能源项目。」

| 项目 | 值 |
| --- | --- |
| 运营并持有的**核准发电容量** | **4,630.5 MW**（补贴 1,624 MW + 平价 3,006.5 MW） |
| 以**权益法入账**的容量 | 174 MW（补贴政策，单列） |
| 2026H1 新增大项目 | **无**（未向信义光能集团或第三方收购大型项目） |
| 公司官网口径（2026-06-30） | 运维管理规模 >6.5 GW、持有 49 个项目、累计核准容量 4,631 MW |

> **⚠️ 术语坑**：**公司并不披露「权益装机容量」**，只披露「运营并持有之核准发电容量（4,630.5MW）」与「权益法入账容量（174MW）」。若新增「权益装机容量」指标，**必须先定义口径**（=4,630.5MW 全口径？还是 4,630.5+174？各项目实际股比未逐项披露，无法算真权益口径），否则会与信义光能 00968 的「权益装机」写法混淆。

---

## 5. 免费「光伏玻璃在产日熔量 / 冷修 / 行业库存天数」数据源逐个实测

| 来源 | URL | 实测状态 | 可直接抓？ | 抽取建议 |
| --- | --- | --- | --- | --- |
| 卓创资讯 · 光伏玻璃数据统计 | `https://nonmetal.sci99.com/mobile/list/1_22891_2.html` | **HTTP 403 Forbidden** | ❌ | 明确拒绝本客户端；该页字段（产量/开工率/表观消费量/库存/进出口/成本利润）属付费数据产品 |
| 卓创资讯 · 光伏产业链资讯 | `https://ne.sci99.com/chain/pv` | **HTTP 403 Forbidden** | ❌ | 403。搜索引擎缓存可见标题（如「光伏玻璃窑炉冷修逐渐落地，涨价支撑稍增 08-26」「国内光伏玻璃行业月度风险评估指标对比分析--2026年9月 08-31」）→ **只能做低频人工事件线索，不能自动抽数** |
| 卓创资讯 · 首页 | `https://www.sci99.com/` | 200 | ⚠️ 极少量 | 首页仅有免费资讯，光伏玻璃数值无；页面唯一「库存天数」是**废钢**的 9 天（口径陷阱，勿误抽） |
| 卓创 · 数据/数智服务 | `https://digital.sci99.com/` | — | ❌ | 数据超市/数据终端/产业数据包，付费 |
| 百川盈孚 · 光伏玻璃价格 | `https://www.baiinfo.com/bolichanye/guangfuboli` | **抽取失败**（"Extracted content appears incomplete"） | ❌ | 搜索引擎摘要证明该页**有日度数据**（「8月25日，近期 2.0mm 光伏玻璃均价为 9.5 元/平方米」「7月31日…8.3 元/平方米」），但正文抓不到 → 需浏览器渲染/登录，判为不可用 |
| 百川盈孚 · 首页 | `https://www.baiinfo.com/`、`http://www.baiinfo.cn/` | **抽取失败** | ❌ | 同上；官网自述数据品类（产能/产量/开工率/消费/库存/进出口/毛利率）为商用服务 |
| 隆众资讯 · 首页 | `https://www.oilchem.net/` | 200 | ⚠️ 无 | 数值全部为前端模板占位 `{{item.dataValue}}` / `{{hbitem1.priceValue}}` / `{{priceListtime}}`，服务端不吐数字 → **结构上不可抓** |
| 隆众资讯 · 光伏玻璃周度库存文章 | `https://www.oilchem.net/26-0622-13-9a478f4f133c4bed.html` | **抽取失败** | ❌ | 标题为「[库存]:中国光伏玻璃样本生产厂库库存量周数据统计（20260618）」（发布 2026-06-22 13:55）→ **说明隆众确有周度库存数据，但正文抓不到**，需渲染/会员 |
| SMM · 光伏价格频道 | `https://hq.smm.cn/photovoltaic` | 200 | ❌ 登录墙 | **价格单元格直接显示「未登录」+ 锁图标**（3.2mm/2.0mm 单双镀、2.0mm 超高透/背面玻璃，日期 09-15）→ 确认登录墙；是否再加付费会员未标注。页面上没有库存/日熔量 |
| SMM · 光伏产业文章 | `https://hq.smm.cn/photovoltaic/content/103270717` | 200 | ✅ 部分 | 免费正文含可抽数字：「3月国内光伏玻璃产量环比 2 月增长 15.18%，月度产量较 2 月增加 22.73 万吨」「3月国内共有 4800 吨/天窑炉新增点火，此外有近 5000 吨/天窑炉堵口恢复」。**只有增量事件、没有总量/库存绝对值** → 适合做「点火/堵口/冷修」事件抽取，不适合 `glass_inventory_days` |
| Mysteel list1 · 光伏玻璃频道 | `https://list1.mysteel.com/dh/269/1.html` | **200** | ✅ **是** | 免费可见价格表：**2.0mm 单层镀膜 = 10.5 元/㎡、3.2mm 单层镀膜 = 18 元/㎡**（无日期、无日熔量/库存/冷修）。**与字典口径「2.0mm 单镀面板·含税送到」一致，可直接当 `glass_price_2_0_single` 的报价源** |
| Mysteel list1 · 首页 | `https://list1.mysteel.com/` | 200 | ⚠️ 部分 | 有价格行情滚动数字（如「废铝 20800」），光伏玻璃仅频道链接；无日熔量/库存 |
| 券商周报（免费可见正文片段） | 例：`https://fxbaogao.com/detail/5069334`、`https://www.microbell.com/repinfodetail_2430292.html`、`https://www.sgpjbg.com.cn/bgdown/927867.html` | 200（摘要页） | ⚠️ 半自动 | **目前最接近免费的日熔量/库存天数来源**：周报正文常直接写「本周光伏玻璃日熔量 88,750 吨/日（持平）」「卓创口径 8 月底天然气制光伏玻璃理论利润 -118.13 元/吨」「行业库存由降转增」「冷修 N 条」。可对这一小段做正则抽取 + 人工留证；缺点是需按报告期滚动跟、PDF 下载多需登录 |

**结论（针对第 5 问）**：**没有找到任何可免费、结构化、直接抓取的官方「在产日熔量 / 冷修产能 / 行业库存天数」周度或月度数据源。** 四个主流口径源均已封：

| 数据商 | 状态 | 性质 |
| --- | --- | --- |
| 卓创 sci99 | 403 + 付费 | 硬付费墙 + 反爬 |
| 百川盈孚 baiinfo | 抓取失败 | 商用数据服务 |
| 隆众 oilchem | 前端模板渲染 + 文章抽取失败 | 数据在会员端 |
| SMM | 明确「未登录」锁 | 登录墙（+可能付费） |

**可行的降级组合（建议写进采集器设计）**：

1. **价格**：Mysteel list1 光伏玻璃频道（免费、口径匹配）做 `glass_price_2_0_single` 日/周报价；SMM/隆众文章做交叉验证（注意字典已记录的「同一天 SMM 17.5–18.5 vs 隆众 10.5」口径陷阱）。
2. **事件**：SMM 免费文章 + Mysteel 频道页做「点火/堵口/冷修/减产」关键词正则（有 t/d 数字，可累加成 `glass_capacity_cold_repair_ytd`）。
3. **总量（日熔量/库存天数）**：券商周报摘要页（东证/国联/天风/申万）人工或半自动录入 `nous pv set`，**标记 `source=manual`**，不要假装成自动源。
4. **公司口径**：直接走 HKEX + 业绩说明会公告（本报告 §2.3、§3），这是唯一「免费 + 权威 + 可自动抓」的一环。

### 附：行业级交叉参考数字（二手，仅作 sanity check，勿作自动源）

| 数字 | 出处 | 说明 |
| --- | --- | --- |
| 2026 年初行业在产日熔量 **86,210 吨/天**，6 月降至 **78,685 吨/天** | 华夏时报/新浪财经 2026-08-27（已实测抓取） | 未标注数据商 |
| 8 月底联合会议后，预计超 **6,000 吨/天** 窑炉产能陆续减产 | 同上，引 SMM 光伏产业分析师郑天鸿 | 事件 |
| 2026H1 2.0mm 均价 **9.65 元/㎡**（同比 -25%）；5 月行业库存天数一度**突破 50 天**（历史新高）；6–7 月行业冷修产线 **7,750 吨/日**；8/20 库存天数回落至 **45.65 天**、2.0mm 价格回升至 **9.5 元/㎡** | OFweek 2026-08（已实测抓取），转述「权威机构研报」 | 无原始署名 |
| 本周光伏玻璃日熔量 **88,750 吨/日**（卓创口径） | 国联期货周报摘要（`fxbaogao.com/detail/5069334`） | 与上行 78,685 冲突，疑口径差 |
| 2025-09 末光伏玻璃日熔量约 **8.87 万吨/天**、库存天数约 **24 天** | 东北证券研报摘要 | 已过期 |

---

## 6. 矛盾与未解决项（已记录，未擅自取舍）

1. **福莱特 扣非归母净利润：-4.38 亿 vs -3.76 亿**
   - 中期業績公告 PDF 数字串给出 `-438,208,787.16`（同年同期值 `227,241,368.45` **与预亏公告披露的上年同期扣非 2.27 亿元完全吻合**，说明该列就是扣非）→ -4.38 亿。
   - OFweek 文章（2026-08，已实测抓取）写「扣非归母净利润为 -3.76 亿元，同比下降 265.52%」；-3.76 亿对 2.27 亿算出的变动恰为 -265.6%，其内部自洽。
   - 但**预亏公告给出的扣非预测区间是 -3.20 ~ -4.20 亿元**，-4.38 亿略微超出区间上界。
   - → **结论：-3.63 亿归母（三重验证）可用；扣非待用 A 股半年报 PDF 原文人工核对，勿自动入库。**
2. **福莱特光伏玻璃收入占比：87.90%（公司半年报原文）vs 89.85%（OFweek）**。89.85% 实为 **2025 年全年**占比（年报原文「2023/2024/2025 分别为 91.42%/90.01%/89.85%」）→ **媒体串年**。以 **87.90%** 为准（分部表 5,861,594.65 / 6,668,210.14 = 87.90% 亦可复算）。
3. **行业在产日熔量口径冲突**：2026-08 报道「年初 86,210 → 6 月 78,685 吨/天」（新浪/华夏时报，未标数据商）vs 同期券商周报「本周 88,750 吨/日」（国联期货，卓创口径）。差约 1 万吨/天，疑为「在产（含堵窑）」vs「剔除堵窑/冷修」口径差。**入库必须带口径标签**，否则污染 `glass_inventory_days` 类信号。
4. **`prefix.do` 是否需要 cookie**：任务描述要求「先取 cookie 再调」；实测无 cookie 亦 200 + 完整 JSON。两者不矛盾（服务端可能都接受），但说明 cookie 步骤不是硬依赖——已在 §1 记录。
5. **信义能源「权益装机容量」在原始披露中不存在**（见 §4 术语坑）。

---

## 7. 验证手段的局限（披露）

- `source_check` 对两条核心断言（福莱特 H1 营收/归母/分部占比；信义能源 4,630.5MW/收益/归母）均返回 **`missing-evidence`（confidence 0.20，"No passages available"）**，即**未能提取到可引用段落**。这两条因此**未通过 `source_check` 机器校验**，可信度来自我**直接抓取的一手 PDF + 多处交叉比对**（§3/§4 已逐条标注证据强度）。
- 无法执行 `bash/curl/python`：cninfo POST 契约与 akshare 函数返回列均为**源码级证据，未执行验证**（§2.1/§2.4 已标注）。
- HKEX 大 PDF（>200 页）中文被 CID 字体吞掉：`fetch_content` 会把抽取结果落到本地临时 md（如 `/var/folders/.../pi-web-pdf/2026082500671-c.md`），本次靠 `read` 该文件拿到 §3 的分部毛利表数字，**中文段落缺失**（如「库存天数」等标签）。部分结论因此依赖**数字构造性验证**（分部各行毛利额精确合计等于总毛利）而非文字标签——这是本次最关键的一处方法学妥协，已通过「整体毛利率 8.18%」被利润表、分部表、媒体「营业成本 -7.92%」三路独立确认而降级为可接受风险。
- `http://www.cninfo.com.cn/**finalpage**/...PDF` 与 `static.sse.com.cn` A 股半年报 PDF 均**抓取失败**（404 / Invalid PDF structure），A 股半年报原文改用 HKEX 镜像（§2.3）。

---

## 8. 建议的落地清单

1. `config/pvglass_indicators.yaml → sources.hkex` 增加 `stock_ids: {00968: 97365, 06865: 133883, 03868: 209025}`；用 `titleSearchServlet.do`（GET）拉列表。**本次性价比最高的改动。**
2. 新增 `sources/cninfo.py`：**主路径用东财 GET（`np-anotice-stock`）**，cninfo POST 作 fallback，并在有 bash 的环境先跑通 §2.1 的 curl。
3. `peers` 分组新增：`flat_glass_gm`（福莱特整体/光伏玻璃分部毛利率，自动源 = 中期業績公告 PDF 分部表）、`flat_glass_capacity_tpd`（在产日熔量，**先标 manual**，待 §3 的 19,100 争议解决）、`xinyi_energy_capacity_mw`（4,630.5MW，**必须加「核准容量（非权益）」口径说明**）。
4. `supply` 分组中 `glass_inventory_days` / `glass_capacity_cold_repair_ytd` **保持 manual/seed**，不要因为「SMM/卓创看着有」就改成 auto —— 本次实测四家全在墙后。
5. 给新源加离线测试：用 §3/§4 的真实数字片段断言（延续 `tests/research/test_pvglass_parsers.py` 的做法）。
