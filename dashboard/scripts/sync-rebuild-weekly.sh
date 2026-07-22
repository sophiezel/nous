#!/bin/bash
# Weekly full sync — data integrity alignment between Mac and ECS
# Cron: 0 3 * * 0 (Sunday 3am)

set -e
SSH_KEY="$HOME/.ssh/id_ed25519"
ECS="user@your-server"
LOG="/tmp/weekly-sync.log"

echo "[$(date)] === weekly full sync ===" | tee "$LOG"

# 1. Mac integrity
echo "[$(date)] Mac integrity_check..." | tee -a "$LOG"
sqlite3 ~/code/stock-screener/data/screener.db "PRAGMA quick_check;" | tee -a "$LOG"

# 2. ECS integrity (via SSH)
echo "[$(date)] ECS integrity_check..." | tee -a "$LOG"
ssh -i "$SSH_KEY" -o ConnectTimeout=10 "$ECS" \
  "sqlite3 /opt/dashboard/data/screener.db 'PRAGMA quick_check;'" 2>&1 | tee -a "$LOG"

# 3. Compare key table row counts
echo "[$(date)] Row count comparison..." | tee -a "$LOG"
for table in stock_daily index_daily sentiment_cache screen_results; do
    mac_count=$(sqlite3 ~/code/stock-screener/data/screener.db "SELECT COUNT(*) FROM $table;")
    ecs_count=$(ssh -i "$SSH_KEY" -o ConnectTimeout=10 "$ECS" \
      "sqlite3 /opt/dashboard/data/screener.db 'SELECT COUNT(*) FROM $table;'")
    echo "  $table: Mac=$mac_count ECS=$ecs_count" | tee -a "$LOG"
    if [ "$mac_count" != "$ecs_count" ]; then
        echo "  WARN: mismatch!" | tee -a "$LOG"
    fi
done

# 4. VACUUM to reclaim space
echo "[$(date)] ECS VACUUM..." | tee -a "$LOG"
ssh -i "$SSH_KEY" -o ConnectTimeout=10 "$ECS" \
  "sqlite3 /opt/dashboard/data/screener.db 'PRAGMA optimize;'" 2>&1 | tee -a "$LOG"

echo "[$(date)] === done ===" | tee -a "$LOG"
