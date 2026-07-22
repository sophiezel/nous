#!/bin/bash
# /opt/dashboard/disk-monitor.sh
# ECS 端磁盘监控，每小时 cron 执行
# 超过80%写日志，超过90%重启Dashboard释放WAL

LOG="/var/log/dashboard-disk.log"
PCT=$(df -h /opt | awk 'NR==2{print $5}' | tr -d '%')

if [ "$PCT" -gt 90 ]; then
  echo "$(date -Iseconds) [disk] CRITICAL ${PCT}% - restarting dashboard to free WAL" >> "$LOG"
  pm2 restart dashboard --update-env 2>/dev/null
elif [ "$PCT" -gt 80 ]; then
  echo "$(date -Iseconds) [disk] WARNING ${PCT}%" >> "$LOG"
  # 清理旧的日志和临时文件
  find /var/log/pm2 -name "*.log" -mtime +7 -delete 2>/dev/null
  find /tmp -name "dashboard-sync-*" -mtime +1 -exec rm -rf {} \; 2>/dev/null
  find /tmp -name "screener-*" -mtime +1 -delete 2>/dev/null
fi
# <80% → 静默
