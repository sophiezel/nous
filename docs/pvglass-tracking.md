# 光伏玻璃产业链跟踪（`nous pv`）

> 一套**可命令化拉取**的细分行业跟踪系统：全维度上下游指标 + 拐点信号引擎 + 龙头个股盈利模型。
> 锚定标的：**信义光能 00968.HK**；对标：福莱特（601865 / 06865.HK）、信义能源（03868.HK）。

---

## 1. 快速开始

```bash
# 首次：建库 + 导入已知事实基线（带日期与来源）
nous pv seed

# 抓取全部数据源（港交所公告 / 一致预期 / 产业链价格 / 期货 / 行情 / 玻璃报价）
nous pv fetch

# 看指标仪表盘（默认按分组），或看单指标时序
nous pv show
nous pv show -g price
nous pv show glass_price_2_0_single

# 拐点信号看板（自动读库判定"是否真的在修复"）
nous pv signal

# 信义光能盈利模型（bear/base/bull + 敏感性 + 一致预期对照）
nous pv xinyi --sens

# 生成周报（默认写 wiki/finance/concepts/光伏玻璃产业链-跟踪.md）
nous pv digest --stdout     # 只看不写
nous pv digest              # 落盘

# 人工录入未自动化的指标
nous pv set pv_install_cn_monthly 14.13 -d 2026-07-31 --note "7月单月推算"

# 数据源清单与采集审计
nous pv sources
nous pv log
```

---

## 2. 全维度指标字典（63 个）

字典是**单一事实来源**：`config/pvglass_indicators.yaml`。采集器按 `source` 分发，
信号引擎按 `id` 取数，周报按 `group` 排版，`bias` 决定阈值方向。

| 分组 | 覆盖内容 | 代表指标 |
| --- | --- | --- |
| **price 价格** | 玻璃报价/结算价（2.0 单镀·双镀·背板、3.2mm）、产业链价格 | `glass_price_2_0_single`、`glass_settlement_price`、`polysilicon_price`、`cell_price_182`、`module_price_topcon` |
| **supply 供给** | 库存天数、在产日熔量、冷修、点火复产、产能利用率、库存 | `glass_inventory_days`、`glass_capacity_cold_repair_ytd`、`polysilicon_inventory` |
| **cost_upstream 成本** | 纯碱（期货+现货）、天然气、重油、浮法玻璃期货 | `soda_ash_futures`、`natgas_price` |
| **demand 需求** | 国内月度装机、组件产量/排产、招标量、出口、双玻渗透率 | `pv_install_cn_monthly`、`module_schedule_cn`、`tender_volume_pv` |
| **company 公司** | 信义产能、海外占比、ASP、销量、分部毛利率、归母、减值、净负债率、回购 | `xinyi_glass_gm`、`xinyi_attributable_profit`、`xinyi_impairment` |
| **peers 同业** | 福莱特毛利率/股价、信义能源利润 | `flat_glass_glass_gm`、`xinyi_energy_profit` |
| **valuation 估值** | 股价、PB、一致预期（FY26/27/28 中位数）、目标价 | `consensus_np_fy2026`、`xinyi_pb` |
| **policy 政策** | 反内卷成本核算、强制性国标、多晶硅收储平台 | `policy_low_price_rule`、`policy_mandatory_standard` |

自动化程度（`auto` 字段）：

| 类型 | 数量 | 含义 |
| --- | --- | --- |
| `auto` | 32 | 采集器全自动 |
| `seed` | 8 | 半自动：需近期文章 URL（索引不稳定）或用 `nous pv set` 补 |
| `manual` | 23 | 需人工录入（`nous pv set`），如减值、出口、双玻渗透率、福莱特产能 |

> `nous pv show --stale` 可只列出缺数据的指标；`nous pv digest` 的"数据缺口"章节会自动列出待补项。

---

## 3. 数据源与实测结论

| source | 来源 | 类型 | 说明 |
| --- | --- | --- | --- |
| `hkex` | 港交所披露易 | auto | 公告/盈警/中报/月报/回购。**需先取 cookie**，`stockId` 需用 `prefix.do` 反查（00968 → 97365） |
| `etnet` | etnet 盈利预测概览 | auto | 逐家券商预测 + 综合；同时落**中位数**（均值会被极值污染） |
| `energytrend` | 集邦 TrendForce | auto | 硅料/硅片/电池/组件周度价格 + 库存 + 玻璃产业新闻（政策/冷修/点火事件线索） |
| `akshare_futures` | 新浪期货 | auto | 纯碱 SA0 / 浮法玻璃 FG0 连续合约 |
| `akshare_quote` | akshare | auto | 00968 / 06865 / 03868（`stock_hk_daily`）；A 股优先新浪 `stock_zh_a_daily`，退东财 |
| `demand` | 需求侧多源 | auto | 装机（光动百科转载 NEA 一览表，**差分**得单月）/ 电池产量（统计局）/ 排产（DataBM 周评）/ 招标（集邦） |
| `report_pdf` | 业绩公告 PDF | auto | 港交所公告 → `pdftotext -layout` → 分部毛利率/收入/归母；**需 poppler**，缺则 skipped 不降级 |
| `mysteel` | Mysteel/隆众 | seed | 光伏玻璃报价/结算价/库存/冷修，正文正则抽取 + 原文留证 |

**已知限制（已做防御，不静默出错）**

1. **Mysteel 无稳定的光伏玻璃列表页**：旧索引会返回多年前文章 → 采集器加了 `max_age_days=45` 新鲜度过滤，并按 URL 日期倒序；索引无新文时回退到 `seed_urls`。
   日常维护：`nous pv fetch -s mysteel -u <新文章URL>`，或把 URL 追加到 `config/pvglass_indicators.yaml → sources.mysteel.seed_urls`。
2. **东方财富系接口偶发 `ProxyError`**（`stock_hk_hist` / `stock_hk_spot_em`）→ 采集器只用新浪直连（`stock_hk_daily`），单项失败降级为 `partial`。
3. **硅料"有价无市"时没有成交价** → `polysilicon_price` 会留空，信号显示"样本不足"，这是**正确行为**（不要用报价冒充成交价）。
4. **口径陷阱**：同一天"光伏玻璃价格"可差近一倍（SMM 2.0mm 报 17.5–18.5 vs Mysteel/隆众 10.5）。字典默认口径为 **2.0mm 单镀面板·含税送到**，并区分**报价**与**结算价**。
5. **港股不披露季报**：`xinyi` 的季度/半年数据来自中报/年报与盈警，季度值用 `nous pv set` 录入。

---

## 4. 拐点信号引擎

信号定义在同一份 YAML 的 `signals` 段，支持算子：
`gte / lte / gt / lt / pct_chg_gte / pct_chg_lte / mom_streak_gte / mom_streak_lte`，`window_days` 控制取值窗口。

| 信号 | 判定条件（简述） | 为什么 |
| --- | --- | --- |
| `S1_glass_price` | 2.0mm单镀 ≥ **10.5** 且 库存天数 ≤ 40 | 结算价站稳才算收入端修复（报价不算） |
| `S2_polysilicon` | 硅料库存 ≤ 45 万吨 且 价格窗口涨幅 ≥ 5% | "有价无市"→规模成交，是反内卷落地的最强领先证据 |
| `S3_demand` | 组件排产连续 ≥3 期环比正 **或** 装机窗口 +10% | 没有需求配合的涨价只是政策脉冲 |
| `S4_supply_exit` | 冷修 ≥ 19,600 t/d 且 在产 ≤ 72,000 t/d | 关停/冷修落地 > 减产倡议书 |
| `S5_xinyi_margin` | 信义玻璃分部毛利率 ≥ 8% | 龙头报表确认（单季） |
| `S6_valuation` | FY2027 一致预期窗口 +10% 或 PB ≥ 0.8 | 预期上修 = 预期交易启动 |

汇总判读：≥3 条确认 → `BULLISH`；1–2 条 → `WATCH`；条件部分满足但缺数据 → `PENDING`；全未触发 → `BEARISH`。

> 设计要点：**缺数据不会被当成"未触发"**。`PENDING` 与 `INSUFFICIENT_DATA` 分开，避免"没数据 = 利空"的误判。

---

## 5. 信义光能盈利模型

文件：`config/pvglass_model.yaml`（可直接改参数）；实现：`src/nous/research/pvglass/xinyi.py`。

```
玻璃收入 = 日熔量 × 天数 × ㎡/吨 × 产能利用率 × 实现均价
玻璃毛利 = 销量 × (实现均价 − 完全成本)
总毛利   = 玻璃毛利 + 可再生能源毛利
经营利润 = 总毛利 − opex(销售/行政/其他净额)
净利润   = 经营利润 − 财务费用与税项
少数股东 = 49.25% × 信义能源集团净利        # 信义光能持股 50.75%
归母     = 净利润 − 减值 − 少数股东
```

**校准方法**：用 2026H1 已披露报表反推"毛利以下费用块"与少数股东比例，
即 `opex=492.3`、`finance_tax=200.0`、`renewable_group_net=384.0`、`minority_ratio=0.4925`，
模型可**精确复现** H1 的经营利润（920.3 − 492.3 = 428.0）与净利润（428.0 − 200.0 = 228.0）。
测试 `test_model_calibrates_to_h1_actuals` 固化了这一点。

**当前输出（2026-09 假设）**

| 情景 | Q4实现价 | 减值 | H2玻璃毛利率 | FY2026 归母 | EPS(分) | PE |
| --- | --- | --- | --- | --- | --- | --- |
| bear | 9.30 | 600 | -0.5% | **-873** | -9.55 | 亏损 |
| base | 10.20 | 0 | 4.2% | **66** | 0.72 | 262x |
| bull | 11.00 | 0 | 8.0% | **376** | 4.11 | 46x |

> **关键洞察**：参数的不确定区间恰好解释了整个卖方一致预期区间 ——
> `bull ≈ etnet 中位数 371`；`bear ≈ 高盛最低值 -927`。
> 所以争论从来不是模型，而是 **Q4 实现价能否站稳 + 年末是否再减值**。
> 敏感性：**每 0.1 元/㎡ ≈ 单季毛利 0.37 亿元**（用 `--sens` 查看完整阶梯）。

---

## 6. 数据库

`~/nous-data/pvglass.db`（可用 `NOUS_DATA_DIR` 覆盖）：

| 表 | 主键 | 用途 |
| --- | --- | --- |
| `obs` | (indicator_id, obs_date, source) | 指标时序；**同键覆盖，不同来源并存**（seed 与自动采集互不覆盖），保留 `source_url`/`note` 供审计 |
| `announcements` | (stock_code, ann_date, title) | 公告（分类：盈警/业绩/回购/月报…） |
| `consensus` | (stock_code, fiscal_year, metric, broker, as_of) | 逐家券商预测 + 综合 |
| `fetch_log` | id | 采集审计（状态/条数/耗时/说明） |

---

## 7. 每周例行工作流

```bash
nous pv weekly        # 采集 → 基线 → 信号 → 周报（与调度器同一实现）
nous pv weekly --no-fetch   # 只重算信号/出报告
# 人工指标（分部毛利率/减值/出口/双玻渗透率）
nous pv set xinyi_glass_gm 8.5 -d 2026-09-30 --note "单季回升"
nous pv backtest --bootstrap --id S1_glass_price   # 信号是否有前瞻收益";
```

调度器已注册 `pvglass-weekly`（每周一 08:40，`nous cron list` 可见）：
入口 `src/nous/scheduler/jobs/research/pvglass_weekly.py`，输出带
`===REPORT_START/END===` 块，可直接接现有推送。
`Makefile` 也提供 `make pv-daily / pv-fetch / pv-signal / pv-digest`。

---

## 8. 扩展指南

| 想做什么 | 改哪里 |
| --- | --- |
| 加指标 | `config/pvglass_indicators.yaml → indicators`（必须声明已存在的 `source`） |
| 加阈值/改判读方向 | 同文件 `bull` / `bear` / `bias`（`up_bearish` = 越低越好） |
| 加信号 | 同文件 `signals`（算子限 `OPS` 列表；指标必须已定义） |
| 加数据源 | 新建 `sources/<name>.py` 实现 `collect(conn, registry, **kw) -> FetchResult`，在 `sources/__init__.py` 的 `_MODULES`/`_FUNCS` 注册 |
| 改盈利模型 | `config/pvglass_model.yaml`（或 `nous pv xinyi --model <path>` 试算） |

新增解析器请配套**离线测试**（`tests/research/test_pvglass_parsers.py` 用真实正文片段断言数值）。

---

## 9. 多锚定标的（信义光能 / 福莱特 / 信义能源）

指标用 `anchor` 字段归属（未声明者默认 `xinyi`），`anchors` 段声明标的与价格指标：

```bash
nous pv show -g peers --anchor flat     # 只看福莱特
nous pv compare                         # 三标的横比（营收/归母/毛利率/产能/股价…）
nous pv compare -a xinyi -a flat
```

* 港交所 `stock_ids` 已预置（00968→97365 / 06865→133883 / 03868→209025），未命中时自动 `prefix.do` 反查
* 福莱特 H 股（06865）与信义能源（03868）的公告已在同一套 hkex 采集器里，A 股公告不再单独抓（同一份中期业绩）
* `modules_schedule` 等需求指标与锚定标的无关，属行业级，留在 xinyi 下渲染

## 10. 信号回测（信号有没有用？）

```bash
nous pv backtest --bootstrap                 # 先灌历史收盘价，再回测全部信号
nous pv backtest --id S1_glass_price --horizons 20,60,120 --cooldown 20
```

* 点对点复现：时点 t 只用 `obs_date <= t` 的数据评估信号（`store.*(as_of=...)`），**无未来信息泄漏**
* 冷却期把「同一事件连续多日确认」合并，避免样本重复
* 输出胜率/平均收益 vs 全样本基线（基线就是「随机入场」），差值为超额
* 数据要求：价格历史 `--bootstrap`（信义 3,138 个交易日）+ 指标历史（自动采集累积）
* 设计原则：指标历史不足时报 `insufficient_history`，**不猜**

## 11. 数据源调研归档（可复用的「先取证再写代码」流程）

| 文档 | 内容 |
| --- | --- |
| `docs/research/pvglass-demand-sources.md` | 装机/电池产量/排产/招标：逐条 URL 实测 + 原文片段 + 正则 + **不可抓清单及原因** |
| `docs/research/pvglass-peer-sources.md` | 福莱特/信义能源：HKEX stockId 反查、cninfo 契约、H1 经营数据、玻璃产能/库存源逐个测 |

新接数据源时按同样流程：① 逐条实测并留原文片段 → ② 写正则 + 离线测试 → ③ 写清「为什么抓不到」。

---

## 12. 推送通道（`nous pv notify`）

仓库原来没有出站消息能力（`dashboard/*push_agent*` 是推数据给看板；`===REPORT_START/END===` 是 Hermes 时代约定）。现新增 `core/notify.py`，可插拔五通道：

| 通道 | 环境变量 | 说明 |
| --- | --- | --- |
| `stdout` | — | 永远可用（launchd 日志/终端） |
| `wecom` | `NOUS_PUSH_WECOM_KEY` | 企业微信群机器人（markdown，超长自动截断） |
| `webhook` | `NOUS_PUSH_WEBHOOK` | 通用 POST JSON `{title,text,source,ts}` |
| `bark` | `NOUS_PUSH_BARK_URL` | iOS 推送 |
| `serverchan` | `NOUS_PUSH_SC_KEY` | Server酱 |

```bash
nous pv notify                  # 看通道状态（凭据是否齐备）
export NOUS_NOTIFY_CHANNELS=wecom,stdout
export NOUS_PUSH_WECOM_KEY=xxx
nous pv notify --test           # 发一条测试
nous pv weekly --push           # 周度自动推送（默认：配了真实通道就推）
nous pv weekly --no-push        # 关掉
```

设计：**推送失败不影响主流程**（返回 `NotifyResult(error)`），未配真实通道时默认不推（避免交互式刷屏）。调度器任务固定 `push=True`。

## 13. 业绩公告 PDF 抽取（`report_pdf`）

半年报/年报里的分部数据原靠人工，现自动抽：

```bash
brew install poppler            # 必需：pdftotext -layout 是唯一可用后端
nous pv fetch -s report_pdf     # 自动取最新业绩公告 → 抽取 6 个指标
```

* **为什么必须 pdftotext**：pypdf 抽港交所中文 PDF **汉字全乱码**（HKEX 中文子集字体缺 glyph→Unicode 映射），只剩数字可读，无法按关键词定位表格。缺 poppler 时明确报 `skipped`，不静默降级。
* 已覆盖：信义光能（分部表→玻璃/可再生毛利率、财务摘要→收入/归母）、福莱特（主要产品毛利表→光伏玻璃毛利率、整体毛利率）。实测值与半年报真值一致（3.32%/55.67%、6.73%/8.18%）。
* **两个已固化为回归测试的坑**：① 利润表的「毛利」只有 2 列，不加区分会把 2025 年总毛利当成分部毛利；② 福莱特同一份报告里有 3 张以「產品種類」开头的表，用它会命中**收益表**（87.90% 是收入占比）——必须用毛利表独有的「毛利 + 毛利率」表头做锚点。
* 兜底：`sanity` 区间校验（毛利率必须 −100~100），抽错宁可跳过；抽不到就保持人工。

## 14. 历史回填（让回测有真样本）

```bash
nous pv backfill --dry-run                      # 先看能拿到多少
nous pv backfill -s tender                      # 1 请求 → 12-13 个月招标
nous pv backfill -s prices --limit 8            # 7 个 taxonomy → 12-14 个月周度价格/库存
nous pv backfill -s install --months 18         # pvmeng 归档 → 18-24 个月单月装机
nous pv backfill -s stats --pages 40            # 统计局翻页（≈0.5 月/页）
nous pv backfill -s install --reset             # 修正解析器后重跑
```

* 写入带 `source=backfill`，与日常增量源**互不覆盖**（obs 主键含 source）
* 装机链路：逐帖取「累计新增」→ 相邻期差分 → 单月；并处理三类边界：**跨年重置**（上年最后一期非 12 月则跳过，不牽连下期）、**1-2 月合并期**、**中间缺月**（标注「跨 N 个月」）、**累计回落**（疑似修订，该期与下期都不输出）
* 实测数据质量（2025-05 → 2026-07）：YTD 年内单调递增，单月 7–25 GW；其中 **2025-05 = 92.92 GW 是真实的 531 抢装潮**
* **不可回填**：光伏玻璃 2.0mm 价格——Mysteel 无翻页、卓创 404、SMM 登录墙、智汇仅公众号，只能从本期开始累积（`docs/research/pvglass-backfill-sources.md` 有逐条实测）

### 回测现状（`nous pv backtest`）

复现网格取**所有条件指标的观测日并集**（原先只看第一个条件，导致有历史的信号也报"样本不足"）。当前：

| 信号 | 状态 | 样本 |
| --- | --- | --- |
| S3 需求端 | ✅ 有真样本 | 3 次触发（20日胜率 67%/超额 +1.6%；120日 −25.5%） |
| S1/S2/S6 | ✅ 可跑，0 次触发 | 均有真实基线（20d 47%/+0.8%、60d 49%/+2.6%、120d 46%/+5.4%） |
| S4/S5/S5f | ⚪ 样本不足 | 触发指标本身只有 1 个观测（半年报/月度冷修数据，天然少） |

---

> 免责声明：本系统用于投研跟踪与假设管理，输出不是投资建议；`seed` 基线为公开信息的时点快照，请以原始公告/资讯为准。
