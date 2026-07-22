#!/bin/bash
# ~/code/dashboard/scripts/sync-to-ecs-v2.sh
# 增量同步脚本 v2：只传今日新增数据行 + 完整性验证 + 安全回滚
# 替代旧的全量 rsync --inplace 模式
# 
# 架构：
#   Mac: sqlite3 导出今日新增行 → 压缩 → scp 到 ECS /tmp
#   ECS: 先备份主DB → 导入增量 → PRAGMA integrity_check → 确认 → PM2 reload
#   (全量同步降级为每周日凌晨一次)

set -e

SOURCE_DB="$HOME/code/stock-screener/data/screener.db"
REPORTS_DB="$HOME/code/dashboard/data/reports.db"
DEST_HOST="user@your-server"
DEST_DB="/opt/dashboard/data/screener.db"
DEST_REPORTS="/opt/dashboard/data/reports.db"
SSH_KEY="$HOME/.ssh/id_ed25519"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=30"
LOCKFILE="/tmp/dashboard-sync-v2.lock"
LOG_TAG="[sync-v2]"

# 锁
shlock -f "$LOCKFILE" -p $$ || { echo "$(date +%T) $LOG_TAG already running, skip"; exit 0; }
trap 'rm -f "$LOCKFILE"' EXIT

START_TS=$(date +%s)
echo "$(date -Iseconds) $LOG_TAG === start ==="

# ─── 判断是否周日全量同步 ──────────────────────────
DOW=$(date +%u)  # 1=Mon, 7=Sun
if [ "$DOW" = "7" ]; then
    echo "$(date +%T) $LOG_TAG Sunday full sync mode"
    FULL_SYNC=true
else
    FULL_SYNC=false
fi

# ─── L1: Mac端数据准备 ────────────────────────────
echo "$(date +%T) $LOG_TAG L1: prepare data"

TMPDIR=$(mktemp -d /tmp/sync-v2-XXXXXX)
trap 'rm -f "$LOCKFILE" "$TMPDIR"/* 2>/dev/null; rmdir "$TMPDIR" 2>/dev/null' EXIT

if $FULL_SYNC; then
    # 周日：全量备份校验
    sqlite3 "$SOURCE_DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null
    sqlite3 "$SOURCE_DB" "PRAGMA integrity_check;" > "$TMPDIR/integrity.txt" 2>&1
    if ! grep -q "ok" "$TMPDIR/integrity.txt"; then
        echo "$(date -Iseconds) $LOG_TAG L1 FAIL: local DB corrupted!"
        exit 1
    fi
    echo "$(date +%T) $LOG_TAG L1: full sync — using compressed transfer"
    # 全量用压缩 + 流式传输
    gzip -c "$SOURCE_DB" > "$TMPDIR/screener.db.gz"
    SYNC_FILE="$TMPDIR/screener.db.gz"
    FULL_MODE=true
else
    # 工作日：增量导出
    TODAY=$(date +%Y-%m-%d)
    
    # 导出今日 stock_daily 新行
    sqlite3 "$SOURCE_DB" ".mode insert" \
        "SELECT * FROM stock_daily WHERE trade_date='$TODAY';" \
        > "$TMPDIR/inc_stock_daily.sql" 2>/dev/null
    
    # 导出今日 sentiment_cache
    sqlite3 "$SOURCE_DB" ".mode insert" \
        "SELECT * FROM sentiment_cache WHERE date='$TODAY';" \
        > "$TMPDIR/inc_sentiment.sql" 2>/dev/null
    
    # 导出今日 screen_results
    sqlite3 "$SOURCE_DB" ".mode insert" \
        "SELECT * FROM screen_results WHERE screen_date='$TODAY';" \
        > "$TMPDIR/inc_screen.sql" 2>/dev/null
    
    # 最近3天 index_daily（容错周末）
    sqlite3 "$SOURCE_DB" ".mode insert" \
        "SELECT * FROM index_daily WHERE trade_date >= date('$TODAY','-3 days');" \
        > "$TMPDIR/inc_index.sql" 2>/dev/null
    
    # 合并 + 压缩
    cat "$TMPDIR"/inc_*.sql > "$TMPDIR/inc_all.sql" 2>/dev/null
    INCSIZE=$(stat -f%z "$TMPDIR/inc_all.sql" 2>/dev/null || echo 0)
    echo "$(date +%T) $LOG_TAG L1: incremental SQL size=$INCSIZE bytes"
    
    if [ "$INCSIZE" -eq 0 ]; then
        echo "$(date +%T) $LOG_TAG L1: no new data today, skip"
        exit 0
    fi
    
    gzip -c "$TMPDIR/inc_all.sql" > "$TMPDIR/inc_all.sql.gz"
    SYNC_FILE="$TMPDIR/inc_all.sql.gz"
    FULL_MODE=false
    
    # reports.db 也增量
    sqlite3 "$REPORTS_DB" ".mode insert" \
        "SELECT * FROM macro_scores WHERE date='$TODAY';" \
        > "$TMPDIR/inc_reports.sql" 2>/dev/null
    gzip -c "$TMPDIR/inc_reports.sql" > "$TMPDIR/inc_reports.sql.gz" 2>/dev/null
fi

# ─── L2: 传输到 ECS ───────────────────────────────
echo "$(date +%T) $LOG_TAG L2: transfer to ECS"

# 传输主数据
scp $SSH_OPTS "$SYNC_FILE" "$DEST_HOST:/tmp/sync-data.gz" 2>&1

# 传输 reports.db 增量
if [ -f "$TMPDIR/inc_reports.sql.gz" ]; then
    scp $SSH_OPTS "$TMPDIR/inc_reports.sql.gz" "$DEST_HOST:/tmp/sync-reports.gz" 2>&1
fi

# ─── L3: ECS 端导入 + 验证 ─────────────────────────
echo "$(date +%T) $LOG_TAG L3: import + verify"

if $FULL_MODE; then
    # 全量模式：先备份，解压替换，验证
    ssh $SSH_OPTS "$DEST_HOST" "
        # 停止 Dashboard
        pm2 stop dashboard 2>/dev/null
        # 备份当前 DB
        cp $DEST_DB ${DEST_DB}.bak 2>/dev/null || true
        # 解压替换
        gunzip -c /tmp/sync-data.gz > ${DEST_DB}.new
        INTEGRITY=\$(sqlite3 ${DEST_DB}.new 'PRAGMA integrity_check;' 2>&1)
        if [ \"\$INTEGRITY\" = 'ok' ]; then
            mv ${DEST_DB}.new $DEST_DB
            rm -f /tmp/sync-data.gz ${DEST_DB}.bak
            echo 'FULL_SYNC_OK'
        else
            echo \"FULL_SYNC_FAIL: \$INTEGRITY\"
            rm -f ${DEST_DB}.new /tmp/sync-data.gz
        fi
    " 2>&1
else
    # 增量模式：先备份，导入SQL，验证
    ssh $SSH_OPTS "$DEST_HOST" "
        # 备份（轻量，只做 checkpoint）
        cp $DEST_DB ${DEST_DB}.bak 2>/dev/null || true
        
        # 导入增量 SQL
        gunzip -c /tmp/sync-data.gz | sqlite3 $DEST_DB 2>&1
        
        # 导入 reports 增量
        if [ -f /tmp/sync-reports.gz ]; then
            gunzip -c /tmp/sync-reports.gz | sqlite3 $DEST_REPORTS 2>&1
            rm -f /tmp/sync-reports.gz
        fi
        
        # 完整性验证
        INTEGRITY=\$(sqlite3 $DEST_DB 'PRAGMA integrity_check;' 2>&1)
        if [ \"\$INTEGRITY\" = 'ok' ]; then
            echo 'INC_SYNC_OK'
            rm -f ${DEST_DB}.bak /tmp/sync-data.gz
        else
            echo \"INC_SYNC_FAIL: \$INTEGRITY\"
            # 回滚
            mv ${DEST_DB}.bak $DEST_DB
            rm -f /tmp/sync-data.gz
        fi
    " 2>&1
fi

# ─── L4: 重载 + 健康检查 ───────────────────────────
echo "$(date +%T) $LOG_TAG L4: reload + health"

ssh $SSH_OPTS "$DEST_HOST" "
    pm2 start dashboard 2>/dev/null || pm2 reload dashboard 2>/dev/null
    sleep 5
    HEALTH=\$(curl -sf --max-time 8 http://127.0.0.1:3000/api/health 2>&1 || echo 'FAIL')
    echo \"HEALTH=\$HEALTH\"
" 2>&1

ELAPSED=$(($(date +%s) - START_TS))
echo "$(date -Iseconds) $LOG_TAG === done elapsed=${ELAPSED}s ==="
