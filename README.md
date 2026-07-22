# Nous

投研心智 — 双引擎荐股 × 鳄鱼派信号 × 全链路自动化。

## 快速开始（一键安装，无需 activate）

```bash
bash install.sh
# 或:
curl -fsSL https://raw.githubusercontent.com/sophiezel/nous/main/install.sh | bash
```

安装脚本会：创建 `.venv`、装齐依赖、把 `nous` 链到 `~/bin` 并写入 PATH。  
**装完后新开终端即可直接用 `nous`，不必再 `source .venv/bin/activate`。**

```bash
source ~/.zshrc   # 当前终端立刻生效；或新开终端
nous version
nous --help
```

数据目录：`~/nous-data/`（`screener.db`、`factors/`、`models/`）。

## 架构

```
nous/
├── src/nous/     Python 包（CLI / 数据 / 引擎 / 交易 / 报告 / 调度）
├── docs/             设计文档 + 验收产物
├── dashboard/        Next.js 前端
└── wiki/             知识库
```

详见 [ARCHITECTURE.md](./ARCHITECTURE.md)。  
V2 方案：[docs/superpowers/specs/2026-07-17-backtest-recommend-fix-design.md](./docs/superpowers/specs/2026-07-17-backtest-recommend-fix-design.md)

## CLI（统一入口：nous）

所有操作都走 `nous`，不要依赖 `make` / `PYTHONPATH`。

### 数据分析
```bash
nous screen -n 20        # 全量筛选 (海鹰F3+龙脉TRL)
nous review               # 鳄鱼派六信号复盘
nous recommend -n 10      # 每日荐股报告
```

### 回测 (V2)
```bash
# 双引擎分别回测（默认 Walk-Forward）
nous backtest --strategy 海鹰F3
nous backtest --strategy 龙脉TRL

# 指定样本区间 + 折数
nous backtest --strategy 海鹰F3 --start 2025-11-01 --end 2026-07-10 --folds 5
nous backtest --strategy 龙脉TRL --start 2025-11-01 --end 2026-07-10 --folds 5

# 批量跑全部策略
nous backtest --batch
```

终端表含：总收益、最大回撤、夏普/截尾夏普、单日极值、收益尖刺、**净值可信度**。

### 投研验收
```bash
nous accept
```

门禁：引擎回归 + **海鹰F3 与 龙脉TRL 各自 WF 回测**（TRUSTED、无尖刺、折窗唯一、禁止 FALLBACK_MOMENTUM）+ 数据鲜度 P0 + 荐股/仓位约束。  
产物含 `f3_backtest.json` / `trl_backtest.json` / `data_assert.json`；中文报告：`docs/acceptance/<日期>/ACCEPTANCE_REPORT.md`

### 数据管理
```bash
nous data status
nous data health
nous data freshness
nous data assert                 # 鲜度+完整性门禁（P0 失败非零退出）
nous data assert --consumer recommend
nous data list
nous data update -s all
```

鲜度契约：[`docs/data/FRESHNESS.md`](docs/data/FRESHNESS.md)  
设计：[`docs/superpowers/specs/2026-07-17-data-freshness-design.md`](docs/superpowers/specs/2026-07-17-data-freshness-design.md)

### 交易 / 模型 / 调度 / 服务
```bash
nous trade check
nous trade position
nous model status
nous cron list
nous cron run -j hsgt-daily
nous serve
nous version
```

## 环境要求

- Python 3.11+（推荐 3.12/3.13）
- SQLite 3.35+ (WAL)
- DeepSeek API Key
- macOS (launchd) 或 Linux (systemd)

开发者可选：`make test` / `make acceptance`（内部转调 `nous accept`），日常请用 `nous`。
