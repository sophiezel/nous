# A 股日线历史回补设计（2014-01 → 2026-07）

> **状态**：读路径已合入；数据洞补续跑中（2026-07-23）  
> **范围**：接年分表 + 标的级洞补；打通 2015→2026 读路径；后台补 2014；供回测使用。  
> **已知**：2015–2019 存在跨年标的空洞（如 `000001`/`600519`）；`--hole-fill` 现按「任意更晚年份/热表有、本年无」筛选。

---

## 1. 目标与成功标准

### 1.1 目标区间

| 区间 | 用途 | 成功门槛 |
|------|------|----------|
| **2015-01 → 2026-07** | 正式回测主窗（因子可先覆盖此窗） | 统一读路径可读；交易日密度达标 |
| **2014-01 → 2014-12** | 延长历史 / 因子预热 | 写入 `stock_daily_2014`；视图含 2014；可后台续跑至达标 |
| 热表最新日 | 鲜度 assert / 日更 | 仍以热表 `stock_daily`（或 `max(hot, year_2026)`）定义「最新」 |

### 1.2 断言成功标准

1. **统一读路径**：`stock_daily_all` **或** `daily_from_sql(start,end)` 分区路由，覆盖 **2014-01-01～2026-07-16**（2014 随回补进度爬升）。
2. **交易日密度**（按年报告 min / p50 / max 日股票数）：
   - 2015–2024：p50 ≥ 当年合理下限（约 1700→5300 量级，与现有分表一致）
   - 2025：薄日（日均 &lt;1000）尽量修复；至少 2025-05-19 后保持高覆盖
   - 2026：分表 `MAX(trade_date)` ≥ 热表同步截止日（目标 `2026-07-16`）
   - 2014：回补完成后 p50 ≥ ~2000（当时 A 股规模）；进行中以 checkpoint % 衡量
3. **空洞日**：正式回测窗内，相对 `index_daily`/`trading_calendar` 的缺失交易日列表为空或可解释（节假日除外）。
4. **回测/因子读路径**：`data_handler` / `backtest/engine` / `factor_compute` / `walk_forward` 等关键查询走 all 或分区 helper；鲜度 assert 对「最新日」仍看热表（或合理 `max(all)` 定义，不以扫全历史为代价）。
5. **验证产物**：`docs/data/freshness/2026-07-17/HISTORY_COVERAGE.md`（+ 可选 JSON）。

---

## 2. 架构

### 2.1 表与视图

```
stock_daily          热表（约近 1 年滚动；采集写入目标）
stock_daily_YYYY     年分表 2009…2026（历史权威；回测主存）
stock_daily_all      VIEW = 年分表 UNION + 热表「超出年分表 max」尾部
```

**现状问题**

- 视图仅含热表 + 2015…2026，**不含 2014**（及更早空/薄表）。
- 热表与 `stock_daily_2025/2026` 日期重叠 → `UNION ALL` 会产生重复行。
- 引擎/因子多数仍 `FROM stock_daily`，只能看到热表窗口。

**目标视图语义（去重）**

```sql
-- 年分表 2009…2026 全量 UNION ALL
-- + 热表 WHERE trade_date > COALESCE(MAX(stock_daily_2026.trade_date), '0000-01-01')
```

同步 2026-04+ 进分表后，热表尾部仅贡献「分表尚未追上」的最新日。

### 2.2 读路径 Helper（新建）

模块：`nous.data.storage.daily_bars`（并由 `storage/__init__.py` 再导出）

| API | 行为 |
|-----|------|
| `daily_table_for(trade_date)` | 单日路由：历史年 → `stock_daily_YYYY`；当前热窗口优先热表（写入语义）或分表（只读历史） |
| `daily_from_sql(start=None, end=None, alias="d")` | 按区间只 UNION **涉及的年分表 + 必要热表尾**，避免 `stock_daily_all` 全库爆炸 |
| `STOCK_DAILY_ALL = "stock_daily_all"` | 符号查询 / 日历 DISTINCT 等可直接用视图 |
| `ensure_stock_daily_all_view(conn)` | 幂等重建视图（含 2009–2014） |
| `latest_trade_date_for_freshness(conn)` | assert/鲜度：`MAX(stock_daily)` 热表优先 |

**原则**：区间已知时用 `daily_from_sql`；单标的长历史可用 `stock_daily_all`；日更/覆盖率 assert 继续盯热表。

### 2.3 写路径（不变为主）

- 日更采集仍写 **热表** `stock_daily`。
- 历史回补写 **对应年分表**（baostock `adjustflag=2` 前复权，与既有 2015 脚本一致）。
- 不把 12 年全量重拉进热表。

---

## 3. 分阶段执行顺序

| # | 阶段 | 阻塞？ | 说明 |
|---|------|--------|------|
| 1 | 扩展/重建 `stock_daily_all`（含 2009–2014） | 是 | 空表 UNION 安全；去重热表尾 |
| 2 | Helper + 关键读路径改为历史可读 | 是 | `factor_compute`、`data_handler`、`engine.py`、`walk_forward`、`trading_calendar` fallback、`optimizer` 等 |
| 3 | 热表 → `stock_daily_2026` 同步 `2026-04-01`～热表 max | 是 | `INSERT OR REPLACE` |
| 4 | 修复 `stock_daily_2025` 薄日（&lt;1000 只，约 96 天，多在 2025-01～05-18） | 建议 | baostock 定点回补；不阻塞 2015+ 主读通 |
| 5 | baostock 回补 **2014-01-01～2014-12-31** → `stock_daily_2014` | 并行 | 多进程 + checkpoint；可后台续跑 |
| 6 | 可选：2015+ 相对 `stock_basic` 缺标的 | 否 | 不阻塞主路径 |
| 7 | 重算因子 | 可后置 | 至少正式回测窗 2015+；2014 因子需预热说明 |
| 8 | 验证脚本 / `HISTORY_COVERAGE.md` | 是（可与回补并行抽样） | 按年 min/p50/max、空洞、引擎抽样 |

---

## 4. 防爬与多源（硬性约束）

> **禁止**：裸 baostock/akshare 高并发打单源；禁止 `workers≥4` 无令牌桶的暴力回补。  
> **必须**：限速 + 退避 + 抖动 + 并发上限 + checkpoint；主备切换 + 抽样交叉校验。

### 4.1 复用封装（禁止从零造轮子）

| 层级 | 路径 | 用途 |
|------|------|------|
| Hermes HTTP 管道 | `~/.hermes/scripts/hermes_http_interceptor.py` | `install()` 后 akshare/`requests`/`curl_cffi` 自动走熔断/并发守卫/自适应限流/TLS |
| Hermes 弹性请求 | `~/.hermes/scripts/resilient_fetcher.py` + `anti_ban.py` + `clash_controller.py` | 诊断失败类型 → 直连/代理/换节点（拦截器已内嵌；脚本勿绕过） |
| Nous 自愈 | `nous.data.collectors`：`resilient_fetch` / `CircuitBreaker` / `heartbeat` | 源级熔断 + 指数退避重试 + 降级 + 心跳 |
| 令牌桶 | `nous.data.collectors.rate_limiter`：`acquire_with_multiplier` / `SOURCE_LIMITERS` | 源级 QPS 预算；开盘保护乘子 |
| 多源共识 | `nous.data.collectors.multi_source`：`median_consensus` / `reconcile_pair` / `multi_source_fetch` | S0/S1/S2 分歧分级 |
| 日线双源 | `nous.data.collectors.fetchers.a_share`：`fetch_daily` / `fetch_tx_daily` / `fetch_daily_dual` | Sina 主 + 腾讯备（按需） |
| 缺口三路 | `nous.data.collectors.gap_repair.repair_gap` | 主源→备源→盲区标记 |
| Hermes 规范 | `~/.hermes/skills/data-science/financial-scraping/SKILL.md` | 16 中间件管道铁律 |

### 4.2 主备源顺序（年分表历史回补）

```
[主] baostock query_history_k_data_plus (adjustflag=2 前复权)
        ↓ 失败 / 空 / 熔断
[备1] akshare stock_zh_a_hist (东财，经 hermes interceptor + akshare 令牌桶)
        ↓ 再失败
[备2] akshare stock_zh_a_daily (新浪全历史，经 interceptor + sina 桶)
        ↓
[抽样交叉] 每 CROSS_CHECK_EVERY 只：腾讯 stock_zh_a_hist_tx 对 close 做 reconcile_pair
           S2(>1%) → 记 provenance，仍以主源 OHLCV 入库（历史窗腾讯常缺 volume）
```

- **热表日更**：保持现有采集链路，不改本回补脚本写热表。
- **BJ(8/4/920)**：baostock 常失败 → skip + checkpoint 记 fail；universe 本就排除。

### 4.3 QPS / 并发 / 退避（默认值，写进脚本常量）

| 参数 | 值 | 说明 |
|------|-----|------|
| `workers` | **1**（默认）；上限 **2** | 禁止 ≥3；跨进程由 interceptor ConcurrencyGuard + 本脚本进程数双控 |
| `baostock` 令牌桶 | rate=**1.0**/s, capacity=**2** | baostock 非 HTTP，拦截器管不到，必须显式限流 |
| `akshare` | 已有 rate=**2**/s, capacity=**2** | 备源 |
| `sina` / `tencent` | rate=**5**/s（已有） | 备2 / 抽样交叉 |
| 符号间抖动 | `uniform(0.3, 1.2)` s | 成功后 |
| 失败退避 | `resilient_fetch`：`base_delay=1.0`，最多 3 次；连败 ≥5 → 该源 CircuitBreaker 冷却 120–180s | |
| 批次提交 | 每成功 **20** 只 `commit` 一次 | 降写锁竞争 |
| checkpoint | `~/nous-data/backfill_checkpoints/stock_daily_{year}.json` | 已完成/失败 symbol，可恢复 |

### 4.4 失败降级策略

1. 主源熔断 OPEN → 整批切备1，不再打主源直到 half-open 探测成功。  
2. 备1/备2 皆失败 → checkpoint `failed`，不阻塞后续 symbol。  
3. HTTP 429 / rate_limited（interceptor 分类）→ 加倍抖动睡眠，勿换更高并发。  
4. SQLite `database is locked` → 短退避重试写；勿开多写进程。  
5. 进度心跳：`heartbeat(f"backfill_year_{year}")` → `~/.hermes/cache/heartbeats/`。

### 4.5 执行入口（唯一允许路径）

```bash
# 通宵编排（推荐；Cursor 持久后台）
bash scripts/overnight_chain.sh

# 或单步（可重复；读 checkpoint）；默认 workers=1；交叉校验默认关（--cross-check 才开）
cd ~/code/nous
PYTHONPATH=src python scripts/backfill_year_partition.py --year 2014 --workers 1
# 薄日修复：--year 2025 --start 2025-01-01 --end 2025-05-18 --thin-only
# 年份大洞：--year 2018 --hole-fill
```

旧脚本 `stock-screener/scripts/backfill_history_2015.py`（4 进程裸 baostock）**本轮禁用**。

---

## 5. 数据源与脚本

| 用途 | 优先 | 落点 |
|------|------|------|
| 2014 全市场 / 2025 薄日 / 年份洞 | **§4 安全路径**（baostock→sina→akshare） | `scripts/backfill_year_partition.py` |
| 通宵编排 | chain | `scripts/overnight_chain.sh` |
| 备选单票补洞 | `gap_repair` / `fetch_daily_dual` | 仅定点，勿全量重拉 |
| 热→年同步 | SQL（无外网） | `scripts/sync_hot_to_year.py` |
| 覆盖报告 | | `scripts/report_history_coverage.py` |

**Checkpoint**：`~/nous-data/backfill_checkpoints/stock_daily_2014.json`。

**BJ 代码**：失败记 skip，不阻塞。

---

## 6. 风险

| 风险 | 缓解 |
|------|------|
| 前复权不一致（baostock vs akshare vs 热表） | 历史分表统一 baostock `adjustflag=2`；热表保持现有采集源；跨源拼接日注明 |
| BJ / 退市代码 | skip + 日志；universe 过滤 |
| SQLite 写锁 | 回补 LOW 优先级 / 批量 commit；避免与日更高峰重叠 |
| 耗时 | 2014 全市场多进程后台；文档给出进度命令 |
| 磁盘 | 年分表增量；不复制热表全历史 |
| `UNION ALL` 重复 | 视图热表尾条件；验证用 `COUNT` vs `COUNT(DISTINCT symbol,trade_date)` 抽检 |

---

## 7. 明确不做

- 不无全量重拉 12 年覆盖现有完好分表。
- 不 force-push；不改 git config。
- 不默认 commit/push（除非用户另说）。
- 不把鲜度 assert 改成必须扫 `stock_daily_all` 全历史。
- **不用裸高并发单源回补**（含旧 `backfill_history_2015.py` 的 4 进程模式）。

---

## 8. 续跑与进度命令

```bash
# 2014 回补（可重复；读 checkpoint；默认 workers=1）
cd ~/code/nous
PYTHONPATH=src python scripts/backfill_year_partition.py --year 2014 --workers 1

# 进度
sqlite3 ~/nous-data/screener.db \
  "SELECT COUNT(DISTINCT symbol), COUNT(*), MIN(trade_date), MAX(trade_date) FROM stock_daily_2014;
   SELECT ROUND(AVG(c),0) FROM (SELECT COUNT(*) c FROM stock_daily_2014 GROUP BY trade_date);"
cat ~/nous-data/backfill_checkpoints/stock_daily_2014.json | head -c 500

# 覆盖报告
PYTHONPATH=src python scripts/report_history_coverage.py --out docs/data/freshness/2026-07-17/HISTORY_COVERAGE.md
```

---

## 9. 回测可用区间（预期）

| 阶段完成后 | 价格可读 | 因子就绪 |
|------------|----------|----------|
| 阶段 1–3 | **2015-01 → 2026-07**（经 helper/视图） | 仍取决于现有 factor 快照窗 |
| 阶段 5 进行中 | 2014 逐步可用 | 2014 需预热重算后才建议纳入正式因子回测 |
| 阶段 4 后 | 2025 上半年密度改善 | 同左 |

**正式回测推荐窗（本轮）**：`2015-01-01` → 热表最新交易日；2014 作加长/预热可选。
