#!/bin/bash
# /opt/dashboard/health-check.sh
# ECS 端健康检查脚本，每15分钟 cron 执行
# 健康 → 静默; 不健康 → 写日志 + 尝试自愈

LOG="/var/log/dashboard-health.log"
HEALTH=$(curl -sf --max-time 10 https://tiangong.uno/api/health 2>/dev/null || echo '{"status":"unreachable"}')

if echo "$HEALTH" | grep -q '"db_ok":true'; then
  # 健康 → 记录 OK（每小时只记一条，避免日志爆炸）
  HOUR=$(date +%Y%m%d%H)
  LAST_OK=$(grep "OK $HOUR" "$LOG" 2>/dev/null | tail -1)
  if [ -z "$LAST_OK" ]; then
    echo "$(date -Iseconds) [health] OK $HEALTH" >> "$LOG"
  fi
  exit 0
fi

# 不健康 → 尝试自愈
echo "$(date -Iseconds) [health] FAIL $HEALTH" >> "$LOG"

# 自愈: 从 .bak 恢复 + 重启
if [ -f /opt/dashboard/data/screener.db.bak ]; then
  echo "$(date -Iseconds) [health] attempting rollback from .bak" >> "$LOG"
  cp /opt/dashboard/data/screener.db.bak /opt/dashboard/data/screener.db
  cp /opt/dashboard/data/reports.db.bak /opt/dashboard/data/reports.db 2>/dev/null
  pm2 reload dashboard --update-env 2>/dev/null
  sleep 5
  HEALTH2=$(curl -sf --max-time 10 https://tiangong.uno/api/health 2>/dev/null || echo '{"status":"unreachable"}')
  if echo "$HEALTH2" | grep -q '"db_ok":true'; then
    echo "$(date -Iseconds) [health] RECOVERED after rollback" >> "$LOG"
    exit 0
  fi
  echo "$(date -Iseconds) [health] rollback failed" >> "$LOG"
fi

# 自愈失败 → 重启 Dashboard（最后一招）
pm2 restart dashboard --update-env 2>/dev/null
sleep 8
HEALTH3=$(curl -sf --max-time 10 https://tiangong.uno/api/health 2>/dev/null || echo '{"status":"unreachable"}')
if echo "$HEALTH3" | grep -q '"db_ok":true'; then
  echo "$(date -Iseconds) [health] RECOVERED after restart" >> "$LOG"
  exit 0
fi

echo "$(date -Iseconds) [health] ALL RECOVERY ATTEMPTS FAILED" >> "$LOG"
exit 1
