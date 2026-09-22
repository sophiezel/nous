# 光伏玻璃产业链 — 需求侧数据源调研

> 请复制到：`docs/research/pvglass-demand-sources.md`
> 调研日期：2026-09-16 · 对应字典 `config/pvglass_indicators.yaml` → `group: demand`
> 目标指标：`pv_install_cn_monthly` / `module_schedule_cn` / `module_production_cn` / `tender_volume_pv`

## 取证方法（先读）

- 下方**每个 URL 都已用 `fetch_content`（raw 或 readable）实际抓取成功**并读到正文/原文片段，无凭印象条目。
- 抓取工具**不暴露原始 HTTP 状态码**。故状态写作 `2xx(推定)`，判据 = raw 返回完整 HTTP 正文 / readable 成功抽出正文，并附**实测正文长度**。抓取失败或只拿到壳页者一律标 `不可自动抓取` 并写明原因。
- 「原文片段」为逐字摘录（保留 `&nbsp;`）；「建议正则」基于已见原文，标 `[建议]` 者尚未在真实管道跑过。
- 时间基准：本地日期 2026-09-16。

---

## 0. 结论速览

| 指标 id | 推荐主源 | 状态 | SSR | 最新一期 | 期间 | 发布日 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `pv_install_cn_monthly` | 光动百科 pvmeng.com（NEA 一览表 HTML 转载） | `2xx(推定)` 171,371 字符 | ✅ | 累计新增 **86.15 GW**；单月 **14.08 GW**(差分) | 2026 1–7 月 / 7 月 | 2026-08-25 | **可自动抓取**（月值须差分） |
| `module_production_cn` | 国家统计局月度《规模以上工业增加值》附表 | `2xx(推定)` 396,602 字符 | ✅ | **6702 万千瓦 = 67.02 GW**（−12.9%） | 2026 年 8 月 | 2026-09-15 | **可自动抓取**，但**口径≠组件产量**，须改名/加注 |
| `module_schedule_cn` | 数字新能源 DataBM《DBM 周评》 | `2xx(推定)` 52,935 字符 | ✅ | **9 月排产 46–50 GW**（较 8 月 +1~2） | 2026 年 9 月 | 2026-09-15 | **可自动抓取**，但只有区间、是预测值 |
| `tender_volume_pv` | 集邦光储观察（energytrend.cn）月度招投标 | `2xx(推定)` 全文抽出 | ✅ | **招标 7.72 GW**（中标含入围 14.11） | 2026 年 8 月 | 2026-09-11 | **可自动抓取**，最佳单源 |

一句话：4 个指标里 3 个（装机、组件产量、招标）有稳定 SSR 文本源可直接自动化；`module_schedule_cn`（排产）无官方源，行业机构口径互相矛盾且为预测值，建议保持 `seed`/`manual` 而非 `auto`。

---

## 1. `pv_install_cn_monthly` — 国内光伏月度新增装机（GW）

### (a) 可抓取 URL

**主源（推荐）— 光动百科 PVMeng 转载 NEA《全国电力统计数据一览表》（HTML 表格）**

- 示例：`https://www.pvmeng.com/2026/08/25/77041/`
- 发现入口（实测 SSR）：`https://www.pvmeng.com/?s=%E5%85%A8%E5%9B%BD%E7%94%B5%E5%8A%9B%E7%BB%9F%E8%AE%A1%E6%95%B0%E6%8D%AE`
- 模板：`https://www.pvmeng.com/{YYYY}/{MM}/{DD}/{post_id}/` —— `post_id` **不可预测**（本次 `77041`），必须从搜索页/频道页解析。
- 频道页：`https://www.pvmeng.com/category/%e6%96%b0%e8%83%bd%e6%ba%90%e4%bf%a1%e6%81%af/`

**官方原文（仅交叉校验，不能单独出月值）**

- `https://www.nea.gov.cn/20260825/1430d07985474c5b8110552785f1c74b/c.html`（2026 1–7 月）
- 模板 `https://www.nea.gov.cn/{YYYYMMDD}/{32位hash}/c.html`，hash 随机须从列表页解析
- ⚠️ 正文只有「太阳能发电装机容量 **12.9 亿千瓦**」（1 位小数，±5 GW），不足做月度差分（月增约 14 GW）

**官方口径精确「新增」文本（散落型）**

- `https://www.nea.gov.cn/20260828/2f7ed7787e1e4e048bf29867e97d5ee0/c.html` → 1–7 月**太阳能发电新增 8615 万千瓦**
- `https://www.nea.gov.cn/20260730/bb571bc20d7445e5ae7d9dc51c3f700d/c.html` → 上半年**太阳能发电新增并网 7207 万千瓦**

### (b) 渲染方式

全部服务器端渲染，curl 可直接拿数字。**关键陷阱**：NEA 月度页正文里的一览表**是图片不是 HTML 表格**（2025-11 起改版）：

- 2026-07 页：`<img src="202607223b678308556b4df0a574537b59a28731_...png" width="800" height="1092">`
- 2026-08 页：`<img id="dDNtSo9AMTzYBlpiqbFI" src="...png">`
- 2025-12 页（1–11 月）：`.jpg` 图片；2025-11 页（1–10 月）已是图片；**2025-10 页（1–9 月）仍是 HTML `<table>`**
→ NEA 月度页模板在 2025-10→11 之间改版，**文本抽取不稳定**。pvmeng 的价值就是把同一张表还原成 HTML。

### (c) 抽取建议

原文片段 #1（摘要行，实测逐字命中，最稳）：

```
<li><mark style="background-color:rgba(0, 0, 0, 0)" class="has-inline-color has-luminous-vivid-orange-color">太阳能发电今年新增装机：8615万千瓦</mark></li>
<li><mark style="background-color:rgba(0, 0, 0, 0)" class="has-inline-color has-luminous-vivid-orange-color">太阳能发电累计装机容量：128804万千瓦（31.58<strong>%</strong>）</mark></li>
```

```python
import re
m = re.search(r'太阳能发电今年新增装机[：:]\s*([\d,]+)\s*万千瓦', html)   # -> 8615  (万千瓦)
ytd_new_wan_kw = int(m.group(1).replace(',', ''))                       # 86.15 GW
m2 = re.search(r'太阳能发电累计装机容量[：:]\s*([\d,]+)\s*万千瓦', html)  # -> 128804
monthly_gw = (ytd_new_wan_kw - prev_ytd_new_wan_kw) / 100.0             # 差分得单月
```

原文片段 #2（同页 `<figure class="wp-block-table">`，实测存在）：

```
<figure class="wp-block-table"><table class="has-fixed-layout"><tbody><tr><td ... colspan="4"><strong>全国电力统计数据一览表(截至2026年7月)</strong></td></tr>...
<tr><td ...>新增发电装机容量</td><td ...>万千瓦</td><td ...>19397&nbsp;</td><td ...>-13851*</td></tr>
<tr><td ...>其中：水电</td><td ...>万千瓦</td><td ...>684&nbsp;…
```

```python
# [建议] 剥标签 -> 压空白 -> 按行正则；"太阳能发电"行需实跑一次确认
text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', table_html)).replace('&nbsp;', ' ')
m = re.search(r'新增发电装机容量\s*万千瓦\s*([\d]+)\s*(-?[\d\.]+)', text)
```

**采集器注意事项**

1. 涨跌幅列有脚注星号（`-13851*`），正则须允许尾部 `*`。
2. **1–2 月合发一期**（"1-2月累计"），做 `mom` 需处理合并期。
3. 月值必须自己差分（NEA 从不给单月新增）。
4. NEA hash 不可预测 → 「NEA 列表页取 URL」+「pvmeng 搜索页取数值」两条腿互校发布期。

### (d) 最新一期数值

| 项目 | 数值 | 期间 | 发布日 | 证据 |
| --- | --- | --- | --- | --- |
| 太阳能发电**累计新增装机** | **8615 万千瓦 = 86.15 GW**（同比 −61.41%） | 2026 1–7 月 | 2026-08-25 | pvmeng 摘要行 + NEA 经济参考报转载页（互印） |
| **累计装机容量** | 128804 万千瓦 = **1288.04 GW**（占全国 31.58%） | 截至 2026-07 底 | 2026-08-25 | pvmeng 摘要行 |
| **7 月单月新增** | **14.08 GW**（同比 +27.54%） | 2026 年 7 月 | 由 86.15 − 72.07 差分 | 1–6 月 72.07 GW 来自 NEA 发布会稿 |
| 参照：上半年 | 72.07 GW（同比约 −66%） | 2026 H1 | 2026-07-22 / 07-30 | NEA 两条稿一致 |

### (e) 备选源

1. NEA 官网月度页（`{YYYYMMDD}/{hash}/c.html`）——官方 SSR，但表是图片、粒度 0.1 亿千瓦，仅作发布监控与校验。
2. 中电联季度《全国电力供需形势分析预测报告》`https://www.chinapower.org.cn/index.php/detail/460836.html`（实测可抓）——给"上半年新增发电装机 1.6 亿千瓦、风光合计 1.1 亿千瓦"，季度+粒度粗。
3. 我的钢铁网月度装机快讯 `https://xny.m.mysteel.com/a/26082511/F666EBA9EE122EFF_abc.html`——现成单月值（14.08 GW），二手。

---

## 2. `module_production_cn` — 国内组件月度产量（GW）

### (a) 可抓取 URL ⭐ 官方源

- 示例（实测成功）：`https://www.stats.gov.cn/sj/zxfbhjd/202609/t20260915_1965308.html`
- 列表入口（实测 SSR）：`https://www.stats.gov.cn/sj/zxfbhjd/`
  列表项：`<a class="fl pc_1600" href="./202609/t20260915_1965308.html" title='2026年8月份规模以上工业增加值增长5.2%'>` + `<span> 2026-09-15 </span>`
  用 title 正则 `\d{4}年\d{1,2}月份规模以上工业增加值增长` 定位
- 模板：`https://www.stats.gov.cn/sj/zxfbhjd/{YYYYMM}/{tYYYYMMDD}_{id}.html`，`id` 不可预测（本次 `1965308`）
- ⚠️ 同批的《2026 年 8 月份能源生产情况》`https://www.stats.gov.cn/sj/zxfb/202609/t20260915_1965312.html` **不含该行**（实测无 `太阳能电池`/`光伏电池` 命中），别抓错页。

### (b) 渲染方式

**服务器端渲染 ✅**，数字在标准 `<td>` 内，外层套 `<span style="font-family:'Times New Roman'">`。

### (c) 抽取建议

原文片段（实测逐字，附表「主要工业产品产量」区）：

```
<td ...><p ...><span style="font-family:宋体"> 太阳能电池（光伏电池）（万千瓦）</span></p></td>
<td ...><p ...><span style="font-family:'Times New Roman'">6702</span></p></td>
（同行后续三个单元格依次） -12.9 , 50590 , -14.6
```

剥标签后的等价文本（实测）：

```
发电机组（发电设备）（万千瓦） 2806 -10.2 23696 1.0 太阳能电池（光伏电池）（万千瓦） 6702 -12.9 50590 -14.6 微型计算机设备（万台） 2075 -25.6 ...
```

```python
import re
text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html)).replace('\u00a0', ' ')
m = re.search(r'太阳能电池（光伏电池）（万千瓦）\s*(-?[\d\.]+)\s*(-?[\d\.]+)\s*(-?[\d\.]+)\s*(-?[\d\.]+)', text)
month_wan_kw, yoy_m, ytd_wan_kw, yoy_ytd = m.groups()   # ('6702','-12.9','50590','-14.6')
production_gw = float(month_wan_kw) / 100.0             # 67.02 GW
```

### (d) 最新一期数值

| 项目 | 数值 | 期间 | 发布日 |
| --- | --- | --- | --- |
| 太阳能电池（光伏电池）产量 | **6702 万千瓦 = 67.02 GW**，同比 **−12.9%** | 2026 年 8 月 | 2026-09-15 |
| 累计产量 | **50590 万千瓦 = 505.90 GW**，同比 **−14.6%** | 2026 1–8 月 | 2026-09-15 |
| 上期参照（二手） | 6579.3 万千瓦 = 65.79 GW（−9.4%）；1–7 月累计 44607.9 | 2026 年 7 月 | 2026-08-19 |

### ⚠️ 口径警告（建议改字典）

1. 该行是**「太阳能电池（光伏电池）」**，含电池片且为规上工业口径，**显著高于 CPIA 组件产量**：统计局 2026 1–8 月 505.90 GW vs CPIA 2026H1 组件产量 201.3 GW。
   → 建议：要么把指标改名为 `cell_production_cn`（note 写"统计局规上口径，含电池片与出口，≠组件产量"）；要么保留 `module_production_cn` 为 `manual`，另建 `solar_cell_output_statsbureau` 作高频代理。
2. **不要用累计差分推单月**：实测 44607.9(1–7 月) + 6702(8 月) = 51309.9 ≠ 50590(1–8 月)，差 719.9 万千瓦。单月与累计基期不同（规上范围年度调整+修订），差分会有 ~7 GW 级误差。
3. 每月 15–19 日随"规模以上工业增加值"同日发布，**日期可预测**，适合定时任务。

### (e) 备选源

1. 我的钢铁网 `https://news.mysteel.com/a/26091510/2140E68A33E95411.html`（一句话口径；**HTML 未实测**，落地前须验证）
2. 世纪新能源网 `https://www.ne21.com/news/show-230810.html`（同上，非官方）
3. CPIA `https://www.chinapv.org.cn/`（实测可抓但**无月度数据**；2026H1 组件产量 201.3 GW −35.1%，只能半年度人工补）

---

## 3. `module_schedule_cn` — 组件排产（GW）

### (a) 可抓取 URL

**主源：数字新能源 DataBM.com《DBM 周评》**

- 示例（实测成功，52,935 字符 raw）：`https://www.databm.com/news/58265186333796115.html`
  标题《DBM周评：放假、减产、降价！光伏组件"旺季"遭遇"寒潮"》；`article:modified_time` = **2026-09-15T21:08:57+08:00**
- 频道列表（实测 SSR）：`https://www.databm.com/photovoltaic/`
- 要闻列表（频道页内实测出现）：`https://www.databm.com/news/list/1-1007.html`
- 搜索：`https://www.databm.com/news/search/list-1.html?keyword=排产`
- 模板 `https://www.databm.com/news/{id}.html`，id 不可预测；用标题 `DBM周评` 过滤（同页可见 `DBM月评`）

### (b) 渲染方式

**服务器端渲染 ✅**，正文 `<p>` 直出，数字外套 `<strong>`。无登录墙、无 JS 挑战。

### (c) 抽取建议

原文片段（实测逐字）：

```
<strong>供应方面</strong>，本周出现<strong>头部厂商排产分化</strong>现象，TOP5厂商中大部分仍<strong>保持提产预期</strong>，行业整体排产预期仍较8月提高1-2GW左右。据数字新能源DataBM.com调研，9月组件排产集中于<strong>46-50GW</strong>之间。
```

同页另有（可做 `tender_volume_pv` 的周频版）：

```
<strong>集中式需求方面</strong>，本周回落至低位，<strong>招标需求</strong>约为<strong>201MW</strong>左右…<strong>定标规模</strong>约为<strong>169MW</strong>…
```

```python
import re
text = re.sub(r'[\u00a0\s]+', '', re.sub(r'<[^>]+>', '', html))
m  = re.search(r'(\d{1,2})月组件排产集中于(\d{1,2})[-~—至](\d{1,2})GW', text)   # ('9','46','50')
m2 = re.search(r'排产预期仍较(\d{1,2})月(提高|下降)(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)GW', text)
```

月聚合建议：取该月最后一次周评的**区间中值**（48 GW），同时保留 `value_low/value_high` 字段记录 ±2 GW 不确定度。

### (d) 最新一期数值

| 项目 | 数值 | 期间 | 发布日 |
| --- | --- | --- | --- |
| 国内组件排产（调研区间） | **46–50 GW**（中值 48），较 8 月 **+1~2 GW** | 2026 年 9 月 | 2026-09-15 |
| 头部厂商 | TOP5 多数保持提产预期；部分厂商开始减产、降价 | 当周 | 2026-09-15 |

### ⚠️ 矛盾（必须记录）

| 机构 | 发布日 | 口径 | 9 月排产 | 相对 8 月 |
| --- | --- | --- | --- | --- |
| SMM 上海有色网 | 2026-09-10 | 中国组件产量（**含中企海外产出**） | 约 55 GW（字典 note）；官方正文仅"环比微降 2%–3%" | **−2%~−3%** |
| 数字新能源 DataBM | 2026-09-15 | 组件排产（未说明是否含海外） | **46–50 GW** | **+1~2 GW** |

方向相反且差 7 GW，**推断是"是否含海外产出"+调研样本差，非同一口径冲突**（未获直接证据）。建议只锚定单一源（推荐 DataBM，唯一免费 SSR 绝对 GW 源），note 注明"SMM 含海外口径约 +7 GW，不可混用"。

### (e) 备选源 + 抓不到的部分

1. **SMM 组件排产数据栏目** `https://hq.smm.cn/photovoltaic/list/12856`（实测 SSR，标题含环比%，如"12月组件排产预计大降14.77％"）
   **但正文有登录墙**：实测 `https://hq.smm.cn/photovoltaic/content/104108255` 正文止于 `— 登录后查看全文 —`
   → **⛔ 不可自动抓绝对值（原因：付费/登录墙）**，仅可抓标题环比 % 做方向校验。
2. **InfoLink**：月度排产/产出绝对值在付费《光伏产业月报》（`https://www.trendforce.cn/research/download/RP260908ED`）。免费页 `https://www.infolink-group.com/energy/cn/7` 主要是出口数据与年度展望 → **⛔ 月度排产绝对值不可免费自动抓取（原因：付费报告）**。
3. **集邦月报** `https://www.trendforce.cn/presscenter/analysis`：免费侧只有周度价格与产业洞察，组件月度排产绝对值在付费月报内。
4. **智汇光伏**：仅微信公众号，无独立网站 → **⛔ 不可自动抓取**。

---

## 4. `tender_volume_pv` — 央国企组件招标量（GW）

### (a) 可抓取 URL ⭐ 本组最佳源

**集邦光储观察/TrendForce 月度招投标统计（energytrend.cn，免费全文 SSR）**

- 示例（实测成功，全文抽出）：`https://www.energytrend.cn/research/20260911-148583.html`
  标题《8月组件中标超14GW，协鑫、晶澳领跑定标榜》，发布 **2026-09-11 14:51**
- 分类页入口（实测 SSR，**摘要里就含数字**）：`https://www.energytrend.cn/taxonomy/term/5334/`（光伏招标）；RSS：`https://www.energytrend.cn/rss.xml`
- 同源镜像：`https://www.trendforce.cn/industry-news/green-energy/{YYYYMMDD}-{id}.html`
  （实测：`https://www.trendforce.cn/industry-news/green-energy/20260818-7319.html` = 7 月篇）
- 模板：`https://www.energytrend.cn/research/{YYYYMMDD}-{id}.html` 或 `.../news/...`，id 不可预测，从分类页/RSS 解析

### (b) 渲染方式

**服务器端渲染 ✅**，正文与分类页摘要均为裸 HTML。

### (c) 抽取建议

原文片段 #1（8 月篇正文，实测逐字）：

```
据集邦光储观察不完全统计，2026年8月国内光伏组件招标规模达7.72GW，中国铁建开启3GW TOPCon组件集采、中国南水北调集团发布1.3GW组件集采、申能股份启动1GW组件框采，招标规模大幅上涨。
```

原文片段 #2（分类页摘要，实测逐字，不进正文即可取数）：

```
<div class="moreinf" style="font-size: 16.5px;line-height:2.1"> 2026年8月，国内光伏组件招标7.72GW，环比大幅增长；中标量（含入围）14.11GW，协鑫、晶澳领跑定标榜；N型组件投标均价0.722元/W…
```

```python
import re
text = re.sub(r'[\u00a0\s]+', '', re.sub(r'<[^>]+>', '', html))
m  = re.search(r'(\d{4})年(\d{1,2})月国内光伏组件招标规模达([\d\.]+)GW', text)      # ('2026','8','7.72')
m2 = re.search(r'已公布中标结果及中标候选人的组件规模为([\d\.]+)GW，含已定标规模([\d\.]+)GW', text)
# 批量抓历史（分类页一次拿多期）
for mm in re.finditer(r'(\d{4})年(\d{1,2})月，国内光伏组件招标([\d\.]+)GW', text): print(mm.groups())
```

**注意**：同一篇里「招标」与「中标（含入围）」是两个口径；`tender_volume_pv` 要抓 **A 式（招标规模达 X GW）**，不要抓中标量。

### (d) 最新一期数值

| 项目 | 数值 | 期间 | 发布日 |
| --- | --- | --- | --- |
| 国内光伏组件**招标量** | **7.72 GW**（环比大幅增长） | 2026 年 8 月 | 2026-09-11 |
| 组件**中标量（含入围）** | 14.11 GW（已定标 13.24、第一候选人 0.87） | 2026 年 8 月 | 2026-09-11 |
| N 型组件投标均价 | 0.722 元/W（7 月 0.743） | 2026 年 8 月 | 2026-09-11 |
| 上期：7 月 | 招标 **2.7 GW**（环比大幅下降）；定标 18.07 GW | 2026 年 7 月 | 2026-08-18 |

同源历史（实测自分类页摘要）：5 月 14.21 GW、3 月 11.35 GW、Q1 合计 20.32 GW（同比 −61.56%）。

### (e) 备选源

1. **DataBM 周评/月评**：`https://www.databm.com/news/58265186333796115.html` 实测"本周招标需求约 201 MW、定标约 169 MW"；另有"DBM月评：…8月光伏组件定标同比涨超250%！"`https://www.databm.com/news/68417079881656115.html`（仅列表标题可见）→ 周频 MW 粒度，适合做周度辅助指标，与集邦月度值不直接可比。
2. **索比光伏网** `https://news.solarbe.com/202609/07/50028678.html`《8月14.9GW光伏组件中标一览》——**中标口径 14.9 GW，与集邦 14.11 GW 略有差异**，可交叉校验；本次未实测其 HTML。
3. **SMM《74GW 光伏组件招标及定标追踪》** `https://news.smm.cn/news/102981952`
   → ⛔ **不要用作 2026 数据源**：正文自述"1-8月光伏新增装机 139.99GW、8月新增 16.46GW"，与 2024 年口径吻合（2026 1–7 月仅 86.15 GW），**是 2024 年 8–9 月旧文**，会污染检索。

---

## 5. 明确「不可自动抓取」清单（含原因）

| 目标源 | URL | 结论 | 原因 |
| --- | --- | --- | --- |
| 北极星太阳能光伏网 | `https://m.bjx.com.cn/topics/guangfuzujianzhaobiao/`、`https://solar.bjx.com.cn/` | ⛔ 不可抓取 | 桌面站 fetch 直接失败；移动站返回**混淆反爬 JS 挑战页**（实测正文只有 `var arg1='5d0f…'` 加密串，无任何文章正文） |
| SMM 文章正文 | `https://hq.smm.cn/photovoltaic/content/104108255`、`https://news.smm.cn/news/103523622` | ⚠️ 部分可抓 | 列表页 SSR 可抓标题（含环比%）；**正文止于"— 登录后查看全文 —"** |
| NEA 月度页的一览表 | `.../20260825/1430d07985474c5b8110552785f1c74b/c.html` 等 | ⚠️ 表本身不可抓 | 2025-11 起该表是**图片**（png/jpg）；正文文字只给 0.1 亿千瓦粒度累计值 |
| CPIA 官网 | `https://www.chinapv.org.cn/` | ⛔ 月度数据不存在 | 实测**无月度产量/装机数据**（仅年度报告第 N 篇、政策月报、团标公示、会议），颗粒度=半年度/年度 |
| SolarZoom | `https://www.solarzoom.com/index.php/index/article/catid/1` | ⛔ 未能验证 | fetch 失败，未取到内容，**不列任何结论** |
| InfoLink / 集邦月度排产 | — | ⛔ 付费 | 绝对值在付费《光伏产业月报》内 |
| 智汇光伏 | — | ⛔ 无网站 | 仅微信公众号 |

---

## 6. 落地建议

1. **分级**：`tender_volume_pv`、`module_production_cn` 可设 `auto`（后者**先按口径警告改名/加 note**）；`pv_install_cn_monthly` 设 `seed`（URL hash/id 不可预测，需列表页+搜索页两段解析）；`module_schedule_cn` 保持 `seed`/`manual`（预测值 + 机构口径分歧）。
2. **发布节奏（实测）**：统计局=次月 15 日；NEA 月度=次月 22–26 日（07-22 发 1–6 月、08-25 发 1–7 月）；集邦招投标月报=次月中旬（08-18 发 7 月、09-11 发 8 月）；DataBM 周评=每周五。
3. **统一「列表页解析 URL + 正文正则取数」**：4 个主源中 NEA/pvmeng、统计局、集邦、DataBM **都有稳定 SSR 列表页**，避免硬编码 id。
4. **交叉校验**：装机用「累计新增差分」vs「累计容量差分」，差 >2 GW 告警；组件产量用统计局 ≈ CPIA×1.5~2.5，越界提示口径漂移；招标用集邦月度 vs DataBM 周度累加，差 >50% 告警。

---

## 7. 矛盾 / 缺失证据

**矛盾**

1. 9 月组件排产方向相反：SMM（09-10）"环比 −2%~−3%"vs DataBM（09-15）"46–50 GW，+1~2 GW"。推断为口径差（SMM 含海外，约 +7 GW），**未获直接证据证实**。
2. 8 月中标量：集邦 14.11 GW vs 索比 14.9 GW。可能差在"是否含第一候选人/未披露规模项目"，**未核实**。
3. 统计局单月 vs 累计不自洽：8 月 6702 万千瓦，但 44607.9 + 6702 ≠ 50590（差 719.9）。原因**未查明**（规上范围年度调整／修订／某期转述有误皆可能）。

**缺失证据**

1. **2026 年 1–8 月 NEA 月度装机稿**：截至 2026-09-16 **未检索到、也未在 nea.gov.cn 实测到**（NEA 惯例次月 22–26 日发布，1–7 月稿为 08-25）。**未能区分"尚未发布"与"检索未覆盖"**。
2. `pv_install_cn_monthly` 的 **1–6 月 pvmeng 转载页 URL 未实测**（我用 NEA 发布会稿 7207 万千瓦替代核验，两者一致）；若要跑差分需实测该页确认摘要行模板一致。
3. mysteel / 世纪新能源网 / 索比光伏网的**页面 HTML 未实测**（仅见检索摘要），作备选源前须补验。
4. 未取得**任何真实 HTTP 状态码**（工具不暴露），全部为 `2xx(推定)`。
5. 未在真实 Python 环境执行上述正则（本 run 无代码执行工具）；正则已对照实测原文逐字核对，**首跑仍需验证**。

---

## 8. 示例 curl 命令

```bash
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'

# ── 1. pv_install_cn_monthly ──
curl -sSL -A "$UA" --compressed 'https://www.pvmeng.com/?s=%E5%85%A8%E5%9B%BD%E7%94%B5%E5%8A%9B%E7%BB%9F%E8%AE%A1%E6%95%B0%E6%8D%AE' \
  | grep -oE 'https://www\.pvmeng\.com/20[0-9]{2}/[0-9]{2}/[0-9]{2}/[0-9]+/' | head -3
curl -sSL -A "$UA" --compressed 'https://www.pvmeng.com/2026/08/25/77041/' \
  | grep -oE '太阳能发电(今年新增装机|累计装机容量)[：:][0-9]+万千瓦'
curl -sSL -A "$UA" --compressed 'https://www.nea.gov.cn/20260825/1430d07985474c5b8110552785f1c74b/c.html' \
  | grep -oE '太阳能发电装机容量[0-9.]+亿千瓦，同比增长[0-9.]+%'
curl -sSL -A "$UA" --compressed 'https://www.nea.gov.cn/20260828/2f7ed7787e1e4e048bf29867e97d5ee0/c.html' \
  | grep -oE '太阳能发电新增[0-9]+万千瓦'

# ── 2. module_production_cn ──
curl -sSL -A "$UA" --compressed 'https://www.stats.gov.cn/sj/zxfbhjd/' \
  | grep -oE '\./20[0-9]{4}/t20[0-9]{6}_[0-9]+\.html" target="_blank" title=.20[0-9]{2}年[0-9]{1,2}月份规模以上工业增加值增长'
curl -sSL -A "$UA" --compressed 'https://www.stats.gov.cn/sj/zxfbhjd/202609/t20260915_1965308.html' \
  | sed -e 's/<[^>]*>/ /g' -e 's/&nbsp;/ /g' | tr -s ' \t\n' ' ' \
  | grep -oE '太阳能电池（光伏电池）（万千瓦）[[:space:]]*-?[0-9.]+[[:space:]]*-?[0-9.]+[[:space:]]*-?[0-9.]+[[:space:]]*-?[0-9.]+'

# ── 3. module_schedule_cn ──
curl -sSL -A "$UA" --compressed 'https://www.databm.com/photovoltaic/' \
  | grep -oE 'href="https://www\.databm\.com/news/[0-9]+\.html"' | head -20
curl -sSL -A "$UA" --compressed 'https://www.databm.com/news/58265186333796115.html' \
  | sed -e 's/<[^>]*>//g' | grep -oE '[0-9]{1,2}月组件排产集中于[0-9]{1,2}[-~—至][0-9]{1,2}GW'
curl -sSL -A "$UA" --compressed 'https://hq.smm.cn/photovoltaic/list/12856' \
  | grep -oE '组件排产[^<]{0,40}' | head -10

# ── 4. tender_volume_pv ──
curl -sSL -A "$UA" --compressed 'https://www.energytrend.cn/taxonomy/term/5334/' \
  | grep -oE '20[0-9]{2}年[0-9]{1,2}月，国内光伏组件招标[0-9.]+GW'
curl -sSL -A "$UA" --compressed 'https://www.energytrend.cn/research/20260911-148583.html' \
  | sed -e 's/<[^>]*>//g' | grep -oE '20[0-9]{2}年[0-9]{1,2}月国内光伏组件招标规模达[0-9.]+GW'
curl -sSL -A "$UA" --compressed 'https://www.trendforce.cn/industry-news/green-energy/20260818-7319.html' \
  | sed -e 's/<[^>]*>//g' | grep -oE '20[0-9]{2}年[0-9]{1,2}月国内光伏组件招标规模达[0-9.]+GW'

# ── 5. 反例：北极星（会拿到混淆 JS 挑战页，确认不可抓）──
curl -sSL -A "$UA" --compressed 'https://m.bjx.com.cn/topics/guangfuzujianzhaobiao/' | head -c 300
# 实测输出形如：{"l1":"var arg1='5d0f55a51005fe0694bc15e8e2d0c2f810c7bbdc38d8d77e0d';","l2":"GET"}oHhbljdw...
```

---

## 9. 来源清单

**保留（已用 fetch_content 实测抓取成功）**

- NEA 2026 年 1–7 月全国电力统计数据 `https://www.nea.gov.cn/20260825/1430d07985474c5b8110552785f1c74b/c.html` — 官方口径、SSR；证明表为图片
- NEA 2026 年 1–6 月全国电力统计数据 `https://www.nea.gov.cn/20260722/3b678308556b4df0a574537b59a28731/c.html` — 证明表为 PNG
- NEA 2025 年 1–11 月全国电力统计数据 `https://www.nea.gov.cn/20251226/640306962d7d421b921b902f48a04b47/c.html` — 证明表为 JPG（改版分界）
- NEA 2025 年 1–10 月全国电力统计数据 `https://www.nea.gov.cn/20251125/c08a5f9b4a54481696a432ecd6c70dd3/c.html` — 证明此前为 HTML `<table>`
- NEA《截至 7 月底我国发电装机同比增长 11%》`https://www.nea.gov.cn/20260828/2f7ed7787e1e4e048bf29867e97d5ee0/c.html` — 官方转载给出精确"太阳能发电新增 8615 万千瓦"
- NEA 2026 年上半年可再生能源并网运行情况（发布会稿）`https://www.nea.gov.cn/20260730/bb571bc20d7445e5ae7d9dc51c3f700d/c.html` — 官方精确 H1 新增并网 7207 万千瓦
- 光动百科 PVMeng（NEA 一览表 HTML 转载）`https://www.pvmeng.com/2026/08/25/77041/` — **本调研最关键发现**：把 NEA 图片表还原为 HTML，可正则取数
- 光动百科 PVMeng 搜索页 `https://www.pvmeng.com/?s=%E5%85%A8%E5%9B%BD%E7%94%B5%E5%8A%9B%E7%BB%9F%E8%AE%A1%E6%95%B0%E6%8D%AE` — 稳定的历史文章发现入口
- 国家统计局《2026 年 8 月份规模以上工业增加值增长 5.2%》`https://www.stats.gov.cn/sj/zxfbhjd/202609/t20260915_1965308.html` — 官方月度光伏电池产量，SSR 表格行
- 国家统计局《2026 年 8 月份能源生产情况》`https://www.stats.gov.cn/sj/zxfb/202609/t20260915_1965312.html` — **用于排除**（该页无太阳能电池产量行）
- 国家统计局「最新发布和解读聚合」`https://www.stats.gov.cn/sj/zxfbhjd/` — SSR 列表页，解析每月文章 URL
- 集邦《8 月组件中标超 14GW，协鑫、晶澳领跑定标榜》`https://www.energytrend.cn/research/20260911-148583.html` — 招标 7.72 GW，免费全文
- 集邦「光伏招标」分类页 `https://www.energytrend.cn/taxonomy/term/5334/` — SSR，摘要含历史各月 GW
- TrendForce《7 月组件定标 18.07GW》`https://www.trendforce.cn/industry-news/green-energy/20260818-7319.html` — 同源镜像，7 月招标 2.7 GW
- DataBM《DBM 周评：放假、减产、降价…》`https://www.databm.com/news/58265186333796115.html` — 9 月排产 46–50 GW，免费全文
- DataBM 光伏频道 `https://www.databm.com/photovoltaic/` — SSR 列表页 + 价格指数
- SMM《光伏组件产量 8 月延续回暖 9 月排产环比微降》`https://hq.smm.cn/photovoltaic/content/104108255` — 证明登录墙（"— 登录后查看全文 —"）
- SMM「组件排产数据」栏目 `https://hq.smm.cn/photovoltaic/list/12856` — SSR 列表，标题含环比 %
- 北极星移动站招标专题 `https://m.bjx.com.cn/topics/guangfuzujianzhaobiao/` — 作为**反例证据**保留（混淆 JS 挑战）
- CPIA 官网 `https://www.chinapv.org.cn/` — 作为**排除证据**保留（无月度数据）

**拒绝 / 降权**

- `https://news.smm.cn/news/102981952` — 正文"1-8 月新增装机 139.99GW / 8 月 16.46GW"对应 **2024 年**口径，旧文，会污染 2026 检索
- `https://news.smm.cn/news/103523622` — 2025-09-09 旧文（对应 2025 年 9 月），且正文被截断
- 各类 SEO/镜像站 `bg.sgpjbg.com`、`d.qianzhan.com`、`m.babacucu.com`、`lainuogm.com`、`chukou.hangyexinwen.com`、`sjcfw.net`、`zuojing.com`、`pvnews.cn` — 内容为抄袭/洗稿或加壳，仅作线索，不作证据
