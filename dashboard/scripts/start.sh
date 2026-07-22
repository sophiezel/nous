#!/bin/bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm use 22 > /dev/null 2>&1

# ── Decrypt secrets via age ──
cd ~/code/dashboard
source scripts/decrypt-env.sh

# ── Start Next.js ──
# standalone模式需要手动同步static文件
if [ -d ".next/static" ] && [ -d ".next/standalone/.next" ]; then
  cp -r .next/static .next/standalone/.next/ 2>/dev/null
fi
exec node .next/standalone/server.js
