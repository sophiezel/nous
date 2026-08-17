#!/bin/bash
# Nous 一键安装 — 克隆、依赖、冷启动数据。装完可直接 nous screen / recommend
set -e

REPO="https://github.com/sophiezel/nous.git"
INSTALL_DIR="$HOME/code/nous"
DATA_DIR="$HOME/nous-data"
VENV="$INSTALL_DIR/.venv"
BIN_DIR="$HOME/bin"
NOUS_BIN="$VENV/bin/nous"

echo "========================================="
echo "  Nous 量化投研系统 — 一键安装"
echo "========================================="
echo ""

PYVER="$(python3 -c 'import sys; print("%d.%d" % (sys.version_info.major, sys.version_info.minor))' 2>/dev/null || true)"
case "$PYVER" in
    3.11|3.12|3.13) echo "Python $PYVER" ;;
    *)
        echo "需要 Python 3.11–3.13，当前: ${PYVER:-未找到 python3}"
        echo "macOS: brew install python@3.12"
        exit 1
        ;;
esac

# --- 1. Clone ----------------------------------------------------------
if [ -d "$INSTALL_DIR" ]; then
    if [ "${NOUS_INSTALL_SKIP_PULL:-0}" != "1" ]; then
        echo "[1/8] 仓库已存在, 更新..."
        cd "$INSTALL_DIR" && git pull
        exec env NOUS_INSTALL_SKIP_PULL=1 bash "$INSTALL_DIR/install.sh" "$@"
    fi
else
    echo "[1/8] 克隆仓库..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO" "$INSTALL_DIR"
fi

# --- 2. Python venv ----------------------------------------------------
echo "[2/8] 创建虚拟环境..."
cd "$INSTALL_DIR"
python3 -m venv "$VENV" 2>/dev/null || python3 -m venv "$VENV" --without-pip

# --- 3. Install --------------------------------------------------------
echo "[3/8] 安装依赖 (api/scheduler/ml/trading/backtest)..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -e ".[api,scheduler,ml,trading,backtest]" -q

# --- 4. Config ---------------------------------------------------------
echo "[4/8] 配置..."
[ -f "$INSTALL_DIR/.env" ] || cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
echo "  → LLM 可选: 编辑 $INSTALL_DIR/.env 填入 DEEPSEEK_API_KEY"
echo "  → 筛股/荐股不强制需要 Key"

# --- 5. Data dir -------------------------------------------------------
echo "[5/8] 数据目录 $DATA_DIR ..."
mkdir -p "$DATA_DIR"/{logs,factors,models,ic_analysis}

# --- 6. 全局命令 -------------------------------------------------------
echo "[6/8] 注册全局命令 nous..."
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/nous" <<EOF
#!/bin/bash
export NOUS_CONFIG_DIR="$INSTALL_DIR/config"
export NOUS_DATA_DIR="\${NOUS_DATA_DIR:-$DATA_DIR}"
exec "$NOUS_BIN" "\$@"
EOF
chmod +x "$BIN_DIR/nous"

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

if [ -f "$HOME/.zshrc" ] && grep -q "alias nous=" "$HOME/.zshrc" 2>/dev/null; then
    sed -i.bak "s|^alias nous=.*|alias nous=\"$BIN_DIR/nous\"|" "$HOME/.zshrc" 2>/dev/null || true
fi

export PATH="$BIN_DIR:$PATH"
export NOUS_CONFIG_DIR="$INSTALL_DIR/config"
export NOUS_DATA_DIR="$DATA_DIR"

# --- 7. launchd template ----------------------------------------------
echo "[7/8] 生成调度配置..."
PLIST_SRC="$INSTALL_DIR/scheduler/launchd/com.nous.scheduler.plist.template"
PLIST_DST="$INSTALL_DIR/scheduler/launchd/com.nous.scheduler.plist"
if [ -f "$PLIST_SRC" ]; then
    sed -e "s|__NOUS_ROOT__|$INSTALL_DIR|g" -e "s|__DATA_DIR__|$DATA_DIR|g" \
        "$PLIST_SRC" > "$PLIST_DST"
fi

# --- 8. Bootstrap + verify --------------------------------------------
echo "[8/8] 冷启动行情（高流动性约 800 只、近一年日线，需联网，数分钟）..."
if [ "${NOUS_SKIP_BOOTSTRAP:-0}" = "1" ]; then
    echo "  跳过 bootstrap (NOUS_SKIP_BOOTSTRAP=1)"
else
    "$NOUS_BIN" data bootstrap
fi
"$NOUS_BIN" version

echo ""
echo "========================================="
echo "  安装完成 — 无需 source .venv"
echo ""
echo "  新开终端，或: source ~/.zshrc"
echo ""
echo "    nous screen              # 筛选（装完即可）"
echo "    nous recommend           # 荐股（装完即可）"
echo "    nous review              # 鳄鱼派复盘"
echo "    nous data status"
echo "    nous data chain --chain post-close   # 收盘全链路（更全）"
echo ""
echo "  回测/验收需要更长历史与因子，请再跑:"
echo "    nous data chain --chain S2"
echo "    nous backtest --strategy 海鹰F3"
echo ""
echo "  可选调度 (macOS):"
echo "    mkdir -p ~/Library/LaunchAgents"
echo "    cp $PLIST_DST ~/Library/LaunchAgents/"
echo "    launchctl load ~/Library/LaunchAgents/com.nous.scheduler.plist"
echo "========================================="
