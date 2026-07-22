# Nous — Architecture & Integration Plan

> 独立量化投研系统：双引擎荐股(海鹰F3+龙脉TRL) × 鳄鱼派信号引擎 × 全链路自动化

**Version:** 1.0  
**Date:** 2026-07-12  
**Status:** Planning — 不开工，仅方案

---

## 1. 项目定位

Nous 是整个量化投研体系的"拱顶石"——一个独立于 Hermes Agent 的 monorepo，
整合分散在 5 个仓库（stock-screener/stock-advisor/data-service/dashboard/wiki-finance）
中的全部投研代码、数据管线、交易逻辑和知识库。

**核心目标：**
- 脱离 Hermes Agent 依赖（cron调度/skill/LLM编排/配置管理）
- 统一项目结构、依赖管理、配置和日志
- 保持现有数据资产（screener.db / reports.db）完全兼容
- 可独立部署到 ECS（YOUR_SERVER）运行

---

## 2. 现状审计

### 2.1 子系统盘点

| 模块 | 代码量 | 定位 | Hermes依赖 |
|------|--------|------|-----------|
| stock-screener | ~35,600 行 Python | 核心引擎(筛选/回测/信号/ETL/质量) | 重度(LLM cron/配置/DB) |
| stock-advisor | ~11,700 行 Python | 交易执行(风控/持仓/情绪/订单) | 中度(cron/script引用) |
| data-service | ~400 行 Python | FastAPI 只读数据代理(Dashboard后端) | 轻度(ECS部署脚本) |
| dashboard | Next.js 15 | 前端 Dashboard | 无(已独立) |
| wiki/finance | Markdown | 知识库/概念页/信号引擎文档 | 无(纯内容) |

### 2.2 Hermes 依赖矩阵

| 依赖类型 | 数量 | 严重度 | 迁移策略 |
|---------|------|--------|---------|
| Cron 定时任务 | ~70 个 | P0 | APScheduler + launchd |
| LLM-driven Skill | 5 个(stock-analysis等) | P0 | core/llm_client.py + CLI |
| 散落脚本(~/.hermes/scripts/) | 50+ 个 | P0 | 迁入 scheduler/jobs/ |
| Hermes 配置(.env/config.yaml) | 1 套 | P1 | core/config.py + .env |
| Hermes 消息推送(WeChat) | gateway | P2 | 最后切，短期保留 |
| DB 路径碎片 | screener.db 多处引用 | P2 | core/db.py 统一 |

### 2.3 数据库资产

| 数据库 | 大小 | 核心表 | 位置 |
|--------|------|--------|------|
| screener.db | ~2.2 GB | stock_daily, stock_basic, stock_fundamental, screen_results, lhb_daily, hsgt_market_daily | ~/code/stock-screener/data/ |
| reports.db | ~50 MB | daily_recommendations, review_reports, portfolio_state, risk_metrics | ECS: /data/reports.db |
| bayesian_tracker.db | ~10 MB | 模型性能追踪 | ECS |

**迁移原则：** DB 文件位置不变（兼容现有脚本），通过 `DATA_DIR` 环境变量统一定位。

---

## 3. Monorepo 架构

```
nous/
│
├── core/                          # 共享基础设施
│   ├── __init__.py
│   ├── config.py                  # 统一配置(YAML + env)
│   ├── db.py                      # SQLite 连接池(WAL + Write Proxy)
│   ├── logging.py                 # 结构化日志(structlog)
│   └── llm_client.py              # LLM 调用封装(DeepSeek API)
│
├── data/                          # 数据管道层
│   ├── __init__.py
│   ├── collectors/                # 采集器(Sina/akshare/东方财富)
│   │   ├── daily_update.py        # 日线全量更新(full_daily_update_v8)
│   │   ├── multi_source.py        # 多源交叉验证引擎
│   │   ├── fundamentals.py        # 基本面采集
│   │   ├── lhb.py                 # 龙虎榜
│   │   ├── margin.py              # 融资融券
│   │   ├── hsgt.py                # 沪深港通
│   │   ├── fund_flow.py           # 资金流向
│   │   ├── etf_flow.py            # ETF资金流
│   │   ├── futures.py             # 期货基差
│   │   ├── global_index.py        # 全球指数
│   │   ├── block_trade.py         # 大宗交易
│   │   ├── minute_collector.py    # 分钟行情
│   │   └── sentiment.py           # 情绪采集
│   ├── etl/                       # ETL 流水线
│   │   ├── pipeline.py            # Polars ETL 编排
│   │   ├── rollover.py            # 日切/冷热分离
│   │   └── gap_repair.py          # 数据缺口修复
│   ├── quality/                   # 数据质量
│   │   ├── assertions.py          # 动态基线断言(data_assert)
│   │   ├── freshness.py           # 数据新鲜度检查
│   │   ├── cross_validate.py      # 多源交叉验证
│   │   └── gap_detector.py        # 断点检测
│   └── storage/                   # 存储管理
│       ├── backup.py              # 备份(db_backup)
│       ├── maintenance.py         # 维护(ANALYZE/VACUUM/integrity)
│       ├── archive.py             # 月度归档
│       └── write_proxy.py         # Write Proxy Daemon
│
├── engine/                        # 分析引擎层
│   ├── __init__.py
│   ├── screening/                 # 双引擎筛选
│   │   ├── haiying_f3.py          # 海鹰F3(基本面+技术面+动量)
│   │   ├── longmai_trl.py         # 龙脉TRL(趋势+反转+流动性)
│   │   └── pool_builder.py        # 股票池构建
│   ├── signals/                   # 信号引擎
│   │   ├── crocodile.py           # 鳄鱼派6信号(两只脚/火车头/拥挤度/主线/资金/基差)
│   │   ├── power.py               # 强势信号
│   │   ├── ai_chain.py            # AI产业链扩散/信号/发现
│   │   └── macro_scorer.py        # 宏观评分
│   ├── backtest/                  # 回测引擎
│   │   ├── engine.py              # 回测核心
│   │   ├── signal_engine.py       # 信号回测
│   │   ├── metrics.py             # 绩效指标
│   │   ├── attribution.py         # 归因分析
│   │   ├── benchmarks.py          # 基准对比
│   │   └── survivorship.py        # 存活偏差处理
│   ├── models/                    # ML 模型
│   │   ├── training.py            # LGB/CatBoost 训练
│   │   ├── inference.py           # 推理管道
│   │   ├── bayesian.py            # 贝叶斯校准
│   │   ├── drift.py               # 模型漂移检测
│   │   └── health_check.py        # 模型健康检查
│   └── recommendation/            # 荐股管道
│       ├── pipeline.py            # 日常荐股流水线
│       ├── parser.py              # 荐股解析
│       └── tracker.py             # 荐股追踪/绩效
│
├── trader/                        # 交易执行层
│   ├── __init__.py
│   ├── executor.py                # 模拟/实盘执行(trader_open_buy + sim_executor)
│   ├── portfolio.py               # 持仓管理
│   ├── risk.py                    # 风控(VaR/CVaR/ATR/熔断)
│   ├── discipline.py              # 纪律检查(止盈止损/移动止盈)
│   ├── reconciler.py              # 持仓对账(sim_reconciler)
│   └── hk_executor.py             # 港股执行(竞价/收盘)
│
├── reports/                       # 报告引擎层
│   ├── __init__.py
│   ├── renderer.py                # HTML/SVG 渲染引擎(report_renderer)
│   ├── daily.py                   # 每日复盘报告
│   ├── recommendation.py          # 荐股报告(HTML+MD)
│   ├── performance.py             # 交易绩效报告
│   ├── risk.py                    # 风险报告
│   └── templates/                 # HTML/CSS 模板
│       ├── base.css
│       ├── base-light.css
│       └── report.html.j2
│
├── scheduler/                     # 调度系统(替代 Hermes cron)
│   ├── __init__.py
│   ├── scheduler.py               # APScheduler 主调度器
│   ├── jobs/                      # 定时任务(从 ~/.hermes/scripts/ 迁移)
│   │   ├── __init__.py
│   │   ├── data/                  # 数据类
│   │   │   ├── daily_update.py
│   │   │   ├── daily_rollover.py
│   │   │   ├── backfill.py
│   │   │   ├── hk_backfill.py
│   │   │   ├── minute_collector.py
│   │   │   ├── futures_fetch.py
│   │   │   ├── global_index.py
│   │   │   ├── fund_flow.py
│   │   │   ├── lhb_daily.py
│   │   │   ├── margin_daily.py
│   │   │   ├── block_trade.py
│   │   │   ├── hsgt_sync.py
│   │   │   ├── etf_flow_backfill.py
│   │   │   └── sentiment_collect.py
│   │   ├── quality/               # 质量类
│   │   │   ├── data_assert.py
│   │   │   ├── cross_validate.py
│   │   │   ├── gap_detector.py
│   │   │   ├── post_update_verify.py
│   │   │   └── data_health_check.py
│   │   ├── trading/               # 交易类
│   │   │   ├── open_buy.py
│   │   │   ├── hk_close.py
│   │   │   ├── portfolio_risk.py
│   │   │   ├── sim_executor.py
│   │   │   ├── sim_reconciler.py
│   │   │   ├── preflight_check.py
│   │   │   └── discipline_check.py
│   │   ├── report/                # 报告类
│   │   │   ├── daily_report.py
│   │   │   ├── recommendation.py
│   │   │   ├── performance.py
│   │   │   ├── portfolio_review.py
│   │   │   ├── midday_patrol.py
│   │   │   └── afternoon_review.py
│   │   ├── ml/                    # ML类
│   │   │   ├── weekly_train.py
│   │   │   ├── model_health.py
│   │   │   ├── ai_pool_refresh.py
│   │   │   ├── ai_chain_phase.py
│   │   │   └── ai_chain_signals.py
│   │   ├── maintenance/           # 维护类
│   │   │   ├── db_backup.py
│   │   │   ├── db_maintenance.py
│   │   │   ├── monthly_archive.py
│   │   │   └── data_cleanup.py
│   │   └── bridge/                # 桥接类
│   │       ├── sync_dashboard.py
│   │       ├── sync_reports.py
│   │       └── bridge_to_dashboard.py
│   └── launchd/                   # macOS launchd plist
│       └── com.nous.scheduler.plist
│
├── api/                           # FastAPI 数据服务(原 data-service)
│   ├── __init__.py
│   ├── main.py                    # FastAPI 入口
│   ├── sse_manager.py             # SSE 实时推送
│   ├── data_cache.py              # 数据缓存
│   └── routes/                    # API 路由
│       ├── index.py
│       ├── quant.py
│       ├── reports.py
│       ├── risk.py
│       ├── portfolio.py
│       ├── macro.py
│       ├── flow.py
│       ├── futures.py
│       ├── sentiment.py
│       ├── theme.py
│       └── messages.py
│
├── dashboard/                     # Next.js 前端(原 dashboard，基本不变)
│   ├── app/
│   ├── components/
│   ├── scripts/                   # 脚本迁移到 scheduler/jobs/bridge/
│   ├── package.json
│   └── next.config.js
│
├── wiki/                          # 知识库(原 wiki/finance/concepts/)
│   ├── finance/
│   │   ├── concepts/
│   │   │   ├── 鳄鱼派-操盘手册.md
│   │   │   ├── 鳄鱼派-市场信号.md
│   │   │   ├── 鳄鱼派-行业轮动.md
│   │   │   ├── 鳄鱼派-核心体系.md
│   │   │   ├── 鳄鱼派-风控体系.md
│   │   │   ├── 鳄鱼派-选股方法.md
│   │   │   └── soul-系统决策灵魂.md
│   │   └── engineering/           # 工程知识库
│   └── portfolio/                 # 持仓状态(脱敏)
│       └── state.yaml
│
├── cli.py                         # 统一CLI入口
├── Makefile                       # 常用命令
├── pyproject.toml                 # 统一依赖管理
├── .env.example                   # 环境变量模板
├── config/                        # 配置文件
│   ├── default.yaml               # 默认配置
│   └── production.yaml            # 生产配置
├── tests/                         # 全量测试
│   ├── core/
│   ├── data/
│   ├── engine/
│   ├── trader/
│   ├── reports/
│   ├── scheduler/
│   └── integration/
├── scripts/                       # 运维脚本
│   ├── deploy.sh                  # ECS 部署
│   ├── health_check.sh            # 健康检查
│   └── init_db.sh                 # 数据库初始化
└── ARCHITECTURE.md                # 本文档
```

---

## 4. 核心设计决策

### 4.1 数据库：SQLite WAL + Write Proxy

**不换 PostgreSQL。** SQLite WAL 模式已在生产环境运行数月，2.2GB screener.db 稳定。
问题出在"70+ 进程同时写入"的并发模型，而不是 SQLite 本身。

**解决方案：Write Proxy Daemon**
- 单一进程持有唯一写连接
- 其他进程通过 Unix Socket 提交写入（JSON-line 协议）
- 读取继续走直接文件访问（WAL-safe）
- 优先级队列：HIGH(交易执行) > NORMAL(数据采集) > LOW(回补) > SYSTEM(维护)

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ collector    │   │ trader       │   │ backfill     │
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       │ write             │ write            │ write
       ▼                   ▼                  ▼
┌──────────────────────────────────────────────────┐
│              Write Proxy Daemon                   │
│              /tmp/nous-write.sock             │
│         唯一 SQLite 写连接 + 优先级队列            │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
              ┌─────────────────┐
              │  screener.db    │
              │  (WAL mode)     │
              └─────────────────┘
```

### 4.2 调度器：APScheduler + launchd

**替代 Hermes cron**（~70个定时任务）

- APScheduler `BackgroundScheduler` 管理所有 job
- 从 `scheduler/jobs/` 目录自动发现任务
- 每个 job 是独立 Python 函数，可单独测试和调试
- launchd plist 提供系统级 KeepAlive
- 失败重试、超时保护、日志集中

**调度精度：**
- 交易时段(9:00-16:00)：秒级精度（分钟行情/交易执行）
- 非交易时段：分钟级精度（数据回补/维护）
- 关键P0任务（开盘买入）独立超时和告警

**Hermes cron → APScheduler 映射：**

```python
# Before (Hermes cron)
# schedule: 56 16 * * 1-5, skills: stock-analysis

# After (APScheduler)
@scheduler.scheduled('56 16 * * 1-5', name='daily-stock-screen')
def daily_stock_screen():
    from engine.screening import run_full_screen
    from core.llm_client import enhance_recommendations
    results = run_full_screen()
    enhanced = enhance_recommendations(results)
    return enhanced
```

### 4.3 LLM 调用：轻量封装

**脱离 Hermes skill 机制。** Skill 本质是 "prompt + tool dispatch + loop"。
我们只需要 LLM 推理能力，不需要编排能力。

```python
# core/llm_client.py
from openai import OpenAI

client = OpenAI(
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ["DEEPSEEK_API_KEY"],
)

def analyze_market(context: str) -> dict:
    """单次 LLM 调用，不做多轮 Tool Use"""
    response = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
        temperature=0.1,
    )
    return parse_analysis(response.choices[0].message.content)
```

**使用场景（取代 skill）：**

| 原 Skill | 新方式 | LLM 调用频率 |
|---------|--------|------------|
| stock-analysis 筛选增强 | `engine/screening/` + `llm.analyze_stocks()` | 每日1次 |
| stock-analysis 复盘生成 | `reports/daily.py` + `llm.generate_review()` | 每日1次 |
| stock-analysis 月度汇总 | `reports/monthly.py` + `llm.summarize()` | 每月1次 |
| stock-review 交互复盘 | `cli.py review` + 交互式 LLM | 按需 |

### 4.4 配置管理：YAML + env

脱离 Hermes 的 `config.yaml` + `.env` 双重配置。

```yaml
# config/default.yaml
nous:
  data_dir: "~/nous-data"       # 数据目录(DB/raw/archive)
  log_dir: "~/nous-data/logs"
  env: "development"

database:
  screener:
    path: "screener.db"             # 相对 data_dir
    busy_timeout: 30000
    wal: true
  reports:
    path: "reports.db"

api:
  host: "0.0.0.0"
  port: 8000

llm:
  provider: "deepseek"
  model: "deepseek-v4-pro"
  api_key_env: "DEEPSEEK_API_KEY"

scheduler:
  timezone: "Asia/Shanghai"
  max_workers: 4
```

```bash
# .env (不提交)
DEEPSEEK_API_KEY=sk-xxx
ECS_HOST=user@your-server
ECS_SSH_KEY=~/.ssh/id_ed25519
```

### 4.5 CLI：统一入口

```bash
# 替代 hermes chat -q "xxx"
nous screen           # 运行全量筛选
nous review           # 生成今日复盘
nous recommend        # 生成荐股报告
nous trade check      # 检查持仓纪律
nous risk report      # 风险报告
nous data update      # 日线更新
nous data health      # 数据健康检查
nous model train      # 模型训练
nous serve            # 启动 API 服务
nous cron list        # 列出定时任务
nous cron run <name>  # 手动触发
```

---

## 5. 迁移路线图

### Phase 0: 骨架搭建（1天）

**目标：** 空仓库可运行 `nous --help`

| 子任务 | 产出 |
|--------|------|
| 创建 `pyproject.toml` | 合并4个项目的依赖 |
| 创建 `core/` 模块 | config.py / db.py / logging.py |
| 创建 `cli.py` | typer CLI 骨架 |
| 创建 `config/default.yaml` + `.env.example` | 配置模板 |
| 创建 `Makefile` | make install / make test / make lint |

### Phase 1: 核心引擎迁移（2天）

**目标：** stock-screener 核心可独立运行

| 子任务 | 来源 | 注意事项 |
|--------|------|---------|
| `data/collectors/` | `stock-screener/src/collectors/` | import 路径重写 |
| `data/etl/` | `stock-screener/src/etl/` | Polars 惰性求值陷阱 |
| `data/quality/` | `stock-screener/src/data_quality/` | 动态基线保留 |
| `data/storage/` | `stock-screener/src/storage/` | Write Proxy 集成 |
| `engine/screening/` | `stock-screener/src/screening/` | 双引擎 |
| `engine/signals/` | `stock-screener/src/signals/` | 鳄鱼派6信号 |
| `engine/backtest/` | `stock-screener/src/backtest/` | 回测 |
| `engine/models/` | `stock-screener/src/models/` | ML 训练/推理 |

### Phase 2: 交易层迁移（1天）

**目标：** stock-advisor 归入 monorepo

| 子任务 | 来源 | 注意事项 |
|--------|------|---------|
| `trader/executor.py` | `stock-advisor/trader/` | 多源价格兜底保留 |
| `trader/portfolio.py` | `stock-advisor/trader/portfolio.py` | 脱敏格式兼容 |
| `trader/risk.py` | `stock-advisor/trader/risk.py` | VaR/CVaR/熔断 |
| `trader/discipline.py` | `stock-advisor/trader/sell_discipline.py` | 三层止盈 |
| `trader/hk_executor.py` | `stock-advisor/quant_trader/` | 港股竞价 |

### Phase 3: 报告层迁移（0.5天）

**目标：** report-engineering skill → 独立模块

| 子任务 | 来源 | 注意事项 |
|--------|------|---------|
| `reports/renderer.py` | `~/.hermes/scripts/report_renderer.py` | CSS变量体系 |
| `reports/daily.py` | stock-review skill 逻辑 | LLM结合 |
| `reports/recommendation.py` | stock-analysis skill 逻辑 | 双引擎+信号 |
| `reports/templates/` | `~/.hermes/scripts/templates/` | base.css |

### Phase 4: 调度器 + 脚本迁移（2天）

**目标：** ~70 cron jobs → APScheduler，所有脚本入仓库

**迁移矩阵（按优先级的代表性任务）：**

| 优先级 | 数量 | 关键任务 | 目标位置 |
|--------|------|---------|---------|
| P0 数据 | ~15 | full_daily_update, daily_rollover, post_update_verify | scheduler/jobs/data/ |
| P0 交易 | ~10 | trader_open_buy, trader_hk_close, portfolio_risk | scheduler/jobs/trading/ |
| P0 报告 | ~8 | daily_recommendation, afternoon_review, performance | scheduler/jobs/report/ |
| P1 质量 | ~6 | data_assert, cross_validate, gap_detector | scheduler/jobs/quality/ |
| P1 ML | ~6 | weekly_train, ai_pool_refresh, ai_chain | scheduler/jobs/ml/ |
| P2 维护 | ~8 | db_backup, db_maintenance, archive, cleanup | scheduler/jobs/maintenance/ |
| P2 桥接 | ~5 | sync_dashboard, sync_reports, bridge | scheduler/jobs/bridge/ |

**每条迁移检查清单：**
- [ ] import 路径改为 `nous.xxx`
- [ ] 硬编码路径 → `config.data_dir`
- [ ] `print()` → `logger.info()`
- [ ] `sys.exit(0)` always（no_agent 语义）
- [ ] 独立测试通过（`python -m scheduler.jobs.data.daily_update`）

### Phase 5: API + Dashboard 整合（1天）

**目标：** data-service + dashboard 在 monorepo 内可运行

| 子任务 | 来源 | 注意事项 |
|--------|------|---------|
| `api/` | `code/data-service/` | 路由迁移 + SSE |
| `dashboard/` | `code/dashboard/` | Next.js 项目原样迁移 |

### Phase 6: 清理与文档（1天）

| 子任务 |
|--------|
| 删除 `~/.hermes/scripts/` 中已迁移的脚本 |
| 删除 Hermes cron jobs（保留 no_agent 脚本型） |
| 更新 `ARCHITECTURE.md` 为最终版 |
| 更新 `wiki/` 知识库索引 |
| 全链路 E2E 测试 |
| 撰写 `README.md` |

---

## 6. 关键技术决策

### 6.1 为什么是 Monorepo？

**优点：**
- 统一 `pyproject.toml`，消除跨项目 import 地狱
- 统一 DB 连接（不再有 3 个不同的 `get_db()` 实现）
- CI/CD 一条 pipeline
- 重构时可全局 grep，改 import 路径不怕遗漏

**放弃的替代方案：**
- Polyrepo（子仓库独立）→ 已经被证明会导致脚本散落 + import 地狱
- pip installable packages → 过度工程，内部工具不需要发布到 PyPI

### 6.2 为什么保留 SQLite？

- 2.2GB 数据已经稳定运行数月
- WAL 模式解决了读写并发
- Write Proxy 解决多进程写入冲突
- 零运维成本（不需要 PostgreSQL DBA）
- 数据文件可以直接 scp 到 ECS

**迁移到 PostgreSQL 的条件（未来）：**
- 单表超过 100M 行
- 需要实时 replication
- 需要多地域读副本

### 6.3 为什么用 APScheduler 而不是 Celery/Prefect？

- ~70 个 job 的规模，不需要分布式任务队列
- APScheduler 单进程即可，launchd 提供进程级 KeepAlive
- Celery 需要 Redis/RabbitMQ → 又一个依赖
- Prefect 适合复杂 DAG，但我们的依赖关系简单（定时触发为主）

### 6.4 LLM 调用：同步 vs 异步？

**默认同步调用。** 理由：
- DeepSeek API 响应时间 < 5s（大部分场景）
- 批处理任务（每日筛选/复盘）不需要高并发 LLM 调用
- 异步增加代码复杂度，边际收益低
- 未来高频场景（如实时信号增强）再引入异步

### 6.5 消息推送：保留 Hermes Gateway（过渡期）

微信 iLink 推送链路高度耦合 Hermes gateway，独自分叉成本高。
**策略：** 短期内保留 Hermes gateway 仅用于消息推送，Nous 通过
`scheduler/jobs/bridge/sync_messages.py` 向 gateway 投递消息。
长期逐步迁移到独立 WeChat 通道。

---

## 7. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| ~70 cron 迁移遗漏 | 中 | 高 | 先完整清单 → 逐条划线验证 → grep 残留 |
| 脚本硬编码 `~/.hermes/` 路径 | 高 | 中 | 全局 `grep -r "\.hermes"` → 统一替换 |
| DB 路径碎片导致数据丢失 | 低 | 致命 | Phase 1 先跑全量备份 |
| Python 3.13 Unicode 陷阱 | 低 | 中 | CI 中加 ast.parse 检查 |
| Write Proxy 引入新故障模式 | 中 | 中 | 渐进式：先不改写路径，Phase 0 只设计 |
| ECS 部署脚本断裂 | 中 | 中 | 保留原 data-service 部署脚本做参考 |

---

## 8. 时间线

```
Week 1:
  Day 1: Phase 0 (骨架) + Phase 1 开始
  Day 2: Phase 1 完成 (核心引擎)
  Day 3: Phase 2 (交易层)
  Day 4: Phase 3 (报告层) + Phase 4 开始
  Day 5: Phase 4 完成 (调度器+脚本)

Week 2:
  Day 6: Phase 5 (API+Dashboard)
  Day 7: Phase 6 (清理+文档+E2E)
  Day 8: 缓冲日

总计：7-8天（可利用晚间分批推进）
```

---

## 9. 附录

### A. Cron 迁移完整清单

见 `scheduler/jobs/` 目录下各子包的 `__init__.py`，
每个 job 标注了原始 Hermes cron ID 和迁移状态。

### B. Import 路径映射

```
# Before → After
src.collectors.xxx        → nous.data.collectors.xxx
src.etl.xxx               → nous.data.etl.xxx
src.data_quality.xxx      → nous.data.quality.xxx
src.storage               → nous.data.storage
src.screening.xxx         → nous.engine.screening.xxx
src.signals.xxx           → nous.engine.signals.xxx
src.backtest.xxx          → nous.engine.backtest.xxx
src.models.xxx            → nous.engine.models.xxx
trader.xxx                → nous.trader.xxx
quant_trader.xxx          → nous.trader.xxx
fetchers.xxx              → nous.data.collectors.xxx (部分)
```

### C. 数据库兼容性保证

所有现有 SQLite 文件 **无需迁移**。
`screener.db` / `reports.db` / `bayesian_tracker.db` 的 schema 保持不变。
Nous 仅改变访问方式（统一连接 + Write Proxy），不改变存储格式。

### D. Dashboard 兼容性

Dashboard (Next.js) 部署在 ECS，通过 data-service (FastAPI) 读取数据。
Nous 的 `api/` 模块完全兼容原 data-service 的 API 契约，
Dashboard 无需任何代码变更即可切换后端。
