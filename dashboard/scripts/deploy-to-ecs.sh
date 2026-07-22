#!/bin/bash
# ~/code/dashboard/scripts/deploy-to-ecs.sh
# Mac → ECS Dashboard 部署脚本
# 构建 + 推送 + 重启，不含 --delete 避免误删 ECS 手写文件
set -e

SSH_KEY="$HOME/.ssh/id_ed25519"
DEST="user@your-server:/opt/dashboard/"
SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
RSYNC="rsync -avz --exclude='node_modules/better-sqlite3' -e \"ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10\""

echo "=== 1. Build ==="
cd ~/code/dashboard
npx next build 2>&1 | tail -5

echo "=== 2. Rsync standalone (no --delete) ==="
eval "$RSYNC .next/standalone/ $DEST"

echo "=== 3. Rsync static ==="
eval "$RSYNC .next/static/ user@your-server:/opt/dashboard/.next/static/"

echo "=== 4. Fix platform-specific native modules ==="
eval "$SSH user@your-server 'cd /opt/dashboard && if [ ! -f node_modules/better-sqlite3/build/Release/better_sqlite3.node ]; then npm pack better-sqlite3 && tar xzf better-sqlite3-*.tgz && rm -rf node_modules/better-sqlite3 && mv package node_modules/better-sqlite3 && cd node_modules/better-sqlite3 && npx node-gyp rebuild && rm -f /opt/dashboard/better-sqlite3-*.tgz; fi'"

echo "=== 5. PM2 reload ==="
eval "$SSH user@your-server 'pm2 reload dashboard --update-env && pm2 status'"

echo "=== Done. Verify: https://tiangong.uno ==="
