# Nous

投研心智 — 双引擎荐股 × 鳄鱼派信号 × 全链路自动化。

## 快速开始（一键安装，无需 activate）

公开仓库，HTTPS clone 即可，不必登录 GitHub CLI。

```bash
git clone https://github.com/sophiezel/nous.git ~/code/nous && cd ~/code/nous && bash install.sh
```

国内若 `github.com` 不稳定，可先装 [GitHub CLI](https://cli.github.com/) 再：

```bash
gh repo clone sophiezel/nous ~/code/nous && cd ~/code/nous && bash install.sh
```

若仓库已在本地：

```bash
cd ~/code/nous && bash install.sh
```

安装脚本会：检查 Python 3.11–3.13、创建 `.venv`、安装依赖、写入 PATH，并**联网冷启动**高流动性 A 股近一年日线。  
**装完后新开终端即可 `nous screen` / `nous recommend`，不必 `source .venv/bin/activate`。**

```bash
source ~/.zshrc   # 当前终端立刻生效；或新开终端
nous version
nous screen -n 20
nous recommend -n 10
```

数据目录：`~/nous-data/`。跳过冷启动：`NOUS_SKIP_BOOTSTRAP=1 bash install.sh`，之后再跑 `nous data bootstrap`。

LLM 解读（可选）：编辑 `~/code/nous/.env` 填入 `DEEPSEEK_API_KEY`。筛股/荐股不依赖它。

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
nous data bootstrap              # 空库冷启动（安装时已跑）
nous data status
nous data health
nous data freshness
nous data assert                 # 鲜度+完整性门禁（P0 失败非零退出）
nous data assert --consumer recommend
nous data chain --chain post-close
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

- Python 3.11–3.13（3.14 不支持）
- 能访问行情源（东方财富 / akshare）
- SQLite 3.35+ (WAL)
- DeepSeek API Key（仅 LLM 解读，可选）
- 调度：macOS launchd（install 生成 plist）；Linux 需自行配 systemd

开发者可选：`make test` / `make acceptance`（内部转调 `nous accept`），日常请用 `nous`。
