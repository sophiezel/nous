#!/bin/bash
# SAKURA FRP setup script for Hermes Dashboard
# Usage: bash setup_sakura.sh <access_token> <tunnel_id>

set -e

TOKEN="$1"
TUNNEL_ID="$2"

if [ -z "$TOKEN" ] || [ -z "$TUNNEL_ID" ]; then
    echo "Usage: bash setup_sakura.sh <access_token> <tunnel_id>"
    echo ""
    echo "=== 获取方式 ==="
    echo "1. 访问 https://www.natfrp.com 注册并实名认证"
    echo "2. 创建 HTTP 隧道: 本地IP=127.0.0.1 本地端口=3456"
    echo "3. 访问密钥: https://www.natfrp.com/user/"
    echo "4. 隧道ID: 创建隧道后显示"
    exit 1
fi

FRPC="$HOME/bin/frpc-sakura"
CONFIG_DIR="$HOME/.config/natfrp"
LOGS_DIR="$CONFIG_DIR/logs"
PLIST="$HOME/Library/LaunchAgents/com.hermes.sakura-frp.plist"

mkdir -p "$LOGS_DIR"

# ─── Step 1: Fetch & save tunnel config ───
echo "=== Step 1: 从 SAKURA FRP 拉取隧道配置 ==="
"$FRPC" -f "${TOKEN}:${TUNNEL_ID}" -w -c "$CONFIG_DIR/frpc.toml"
echo "✅ 配置已保存: $CONFIG_DIR/frpc.toml"

# ─── Step 2: Generate launchd plist ───
echo ""
echo "=== Step 2: 生成 launchd 自启配置 ==="
cat > "$PLIST" << PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hermes.sakura-frp</string>
    <key>ProgramArguments</key>
    <array>
        <string>${FRPC}</string>
        <string>-f</string>
        <string>${TOKEN}:${TUNNEL_ID}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>NetworkState</key>
        <true/>
        <key>Crashed</key>
        <true/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>${LOGS_DIR}/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOGS_DIR}/stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
</dict>
</plist>
PLIST_EOF
echo "✅ plist 已生成: $PLIST"

# ─── Step 3: Unload old service ───
echo ""
echo "=== Step 3: 卸载旧服务 ==="
launchctl unload "$PLIST" 2>/dev/null && echo "已卸载旧实例" || echo "(无旧实例)"

# ─── Step 4: Load service ───
echo ""
echo "=== Step 4: 加载 launchd 服务 ==="
launchctl load "$PLIST"
sleep 2
SERVICE_STATUS=$(launchctl list com.hermes.sakura-frp 2>&1)
echo "$SERVICE_STATUS"

# ─── Step 5: Verify ───
echo ""
echo "=== Step 5: 验证隧道状态 ==="
sleep 3
if tail -20 "$LOGS_DIR/stdout.log" 2>/dev/null | grep -q "success"; then
    echo "✅ 隧道启动成功！"
    echo ""
    echo "📍 查看日志: tail -f $LOGS_DIR/stdout.log"
    echo "📍 Dashboard 地址 (参照 SAKURA 面板显示的域名):"
    echo "   https://<你的隧道域名>/mobile"
else
    echo "⏳ 隧道可能还在启动中..."
    echo "📍 查看日志: tail -f $LOGS_DIR/stdout.log"
    echo ""
    echo "⚠️  如果启动失败，检查:"
    echo "   1. 访问密钥是否正确"
    echo "   2. 隧道ID是否正确"
    echo "   3. Dashboard 是否运行在 localhost:3456"
fi
