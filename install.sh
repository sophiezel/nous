#!/bin/bash
# Nous 一键安装脚本 — 装完直接用 nous，无需 source .venv
set -e

REPO="git@github.com:sophiezel/nous.git"
INSTALL_DIR="$HOME/code/nous"
DATA_DIR="$HOME/nous-data"
VENV="$INSTALL_DIR/.venv"
BIN_DIR="$HOME/bin"
NOUS_BIN="$VENV/bin/nous"

echo "========================================="
echo "  Nous 量化投研系统 — 一键安装"
echo "========================================="
echo ""

# ── 1. Clone ──────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR" ]; then
    echo "[1/7] 仓库已存在, 更新..."
    cd "$INSTALL_DIR" && git pull
else
    echo "[1/7] 克隆仓库..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO" "$INSTALL_DIR"
fi

# ── 2. Python venv ─────────────────────────────────────────────────────
echo "[2/7] 创建虚拟环境..."
cd "$INSTALL_DIR"
python3 -m venv "$VENV" 2>/dev/null || python3 -m venv "$VENV" --without-pip

# ── 3. Install ─────────────────────────────────────────────────────────
# V2: ml(LightGBM+pyarrow) trading(PyPortfolioOpt/HRP) backtest
# 注: Python 3.14 下 pandas-ta/numba 不可用，已从 backtest extras 移除
echo "[3/7] 安装依赖 (dev/api/scheduler/ml/trading/backtest)..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -e ".[dev,api,scheduler,ml,trading,backtest]" -q

# ── 4. Config ──────────────────────────────────────────────────────────
echo "[4/7] 配置..."
[ -f "$INSTALL_DIR/.env" ] || cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
echo "  → 请编辑 $INSTALL_DIR/.env 填入 DEEPSEEK_API_KEY"

# ── 5. Data dir ────────────────────────────────────────────────────────
echo "[5/7] 数据目录..."
mkdir -p "$DATA_DIR"/{logs,factors,models,ic_analysis}
for db in "$HOME/code/stock-screener/data/screener.db" "$INSTALL_DIR/data/screener.db"; do
    if [ -f "$db" ] && [ ! -f "$DATA_DIR/screener.db" ]; then
        ln -sf "$db" "$DATA_DIR/screener.db"
        echo "  → 已链接 screener.db ← $db"
        break
    fi
done
echo "  → 数据根目录: $DATA_DIR"

# ── 6. 全局命令（无需 activate）────────────────────────────────────────
echo "[6/7] 注册全局命令 nous..."
mkdir -p "$BIN_DIR"
ln -sfn "$NOUS_BIN" "$BIN_DIR/nous"

# PATH: ~/bin
if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
    if [ -f "$HOME/.zshrc" ] && ! grep -q 'export PATH="$HOME/bin:$PATH"' "$HOME/.zshrc" 2>/dev/null; then
        echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.zshrc"
        echo "  → 已写入 PATH=~/bin 到 ~/.zshrc"
    fi
    if [ -f "$HOME/.bashrc" ] && ! grep -q 'export PATH="$HOME/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null; then
        echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"
        echo "  → 已写入 PATH=~/bin 到 ~/.bashrc"
    fi
fi

# 兼容旧 alias（指向 venv 内 binary，同样无需 activate）
if grep -q "alias nous=" "$HOME/.zshrc" 2>/dev/null; then
    # 刷新为最新路径
    sed -i.bak "s|^alias nous=.*|alias nous=\"$NOUS_BIN\"|" "$HOME/.zshrc" 2>/dev/null \
        || true
else
    echo "alias nous=\"$NOUS_BIN\"" >> "$HOME/.zshrc"
    echo "  → 已添加 alias nous"
fi

export PATH="$BIN_DIR:$PATH"

# ── 7. Verify ──────────────────────────────────────────────────────────
echo "[7/7] 验证安装..."
"$NOUS_BIN" version
"$NOUS_BIN" data status 2>/dev/null || echo "  (数据库未配置, 跳过 data status)"

echo ""
echo "========================================="
echo "  安装完成 — 无需 source .venv"
echo ""
echo "  新开一个终端，或执行: source ~/.zshrc"
echo "  之后任意目录直接使用:"
echo ""
echo "    nous version"
echo "    nous screen              # 双引擎筛选（海鹰F3+龙脉TRL）"
echo "    nous review"
echo "    nous recommend"
echo "    nous backtest --strategy 海鹰F3"
echo "    nous backtest --strategy 龙脉TRL"
echo "    nous accept              # 双引擎 WF 验收（海鹰+龙脉）"
echo "    nous data status"
echo "    nous data health"
echo "    nous trade check"
echo "    nous model status"
echo "    nous cron list"
echo "    nous serve"
echo ""
echo "  验收产物: $INSTALL_DIR/docs/acceptance/<日期>/ACCEPTANCE_REPORT.md"
echo ""
echo "  调度器守护进程:"
echo "    cp $INSTALL_DIR/scheduler/launchd/com.nous.scheduler.plist ~/Library/LaunchAgents/"
echo "    launchctl load ~/Library/LaunchAgents/com.nous.scheduler.plist"
echo "========================================="
