#!/bin/bash
# /opt/dashboard/env-validator.sh
# Dashboard 启动前环境验证。失败不启动，记录错误。
# 由 start.sh 在 exec node 前调用: bash env-validator.sh || exit 1

set -e
ERRORS=0

log() { echo "[validator] $1"; }
fail() { echo "[validator] FAIL: $1"; ERRORS=$((ERRORS+1)); }

# 1. DB 路径验证
SCREENER_PATH="${DASHBOARD_SCREENER_DB:-${SCREENER_DB_PATH:-}}"
REPORTS_PATH="${DASHBOARD_REPORTS_DB:-${REPORTS_DB_PATH:-}}"

if [ -z "$SCREENER_PATH" ]; then
  fail "SCREENER_DB_PATH or DASHBOARD_SCREENER_DB not set"
elif [ ! -f "$SCREENER_PATH" ]; then
  fail "screener.db not found: $SCREENER_PATH"
else
  log "screener.db: $SCREENER_PATH ($(stat -c%s "$SCREENER_PATH") bytes)"
fi

if [ -z "$REPORTS_PATH" ]; then
  fail "REPORTS_DB_PATH or DASHBOARD_REPORTS_DB not set"
elif [ ! -f "$REPORTS_PATH" ]; then
  log "reports.db not found: $REPORTS_PATH (will create on first use)"
else
  log "reports.db: $REPORTS_PATH ($(stat -c%s "$REPORTS_PATH") bytes)"
fi

# 2. better-sqlite3 二进制架构验证
NODE_PATH="/opt/dashboard/node_modules/better-sqlite3/build/Release/better_sqlite3.node"
if [ -f "$NODE_PATH" ]; then
  ARCH=$(file "$NODE_PATH" | grep -o 'x86-64\|arm64\|aarch64')
  if [ "$ARCH" = "x86-64" ]; then
    log "better-sqlite3.node: ELF x86-64 OK"
  else
    fail "better-sqlite3.node architecture mismatch: $ARCH (expected x86-64)"
  fi
else
  fail "better-sqlite3.node not found: $NODE_PATH"
fi

# 3. Node.js 版本
NODE_VER=$(node -v 2>/dev/null || echo "missing")
log "Node.js: $NODE_VER"

# 4. 磁盘空间
DISK_PCT=$(df -h /opt | awk 'NR==2{print $5}' | tr -d '%')
if [ "$DISK_PCT" -gt 90 ]; then
  fail "disk usage ${DISK_PCT}% > 90%"
else
  log "disk: ${DISK_PCT}% used"
fi

# 5. 端口可用性
if ss -tlnp | grep -q ":${PORT:-3000}"; then
  log "port ${PORT:-3000}: in use (may be previous instance, PM2 handles this)"
else
  log "port ${PORT:-3000}: free"
fi

if [ $ERRORS -gt 0 ]; then
  echo "[validator] $ERRORS check(s) failed, refusing to start"
  exit 1
fi

log "all checks passed"
