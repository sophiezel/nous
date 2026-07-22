#!/bin/bash
# 天工 Dashboard - 生成邀请码（直接写入 ECS）
# 用法: bash ~/gen-invite.sh [角色] [有效小时数]
# 角色: guest | member | vip | admin | super_admin (默认 guest)
# 有效期: 默认24小时

ECS_HOST="user@your-server"
ECS_DB="/opt/dashboard/data/screener.db"
SSH_KEY="$HOME/.ssh/id_ed25519"

ROLE="${1:-guest}"
TTL_HOURS="${2:-24}"

# 角色 → 前缀/中文名映射
case "$ROLE" in
  super_admin) PREFIX="TG-SUPER-"  ROLE_CN="超级管理员" ;;
  admin)       PREFIX="TG-ADMIN-"  ROLE_CN="管理员"     ;;
  vip)         PREFIX="TG-VIP-"    ROLE_CN="VIP会员"    ;;
  member)      PREFIX="TG-MBR-"    ROLE_CN="普通会员"   ;;
  guest)       PREFIX="TG-GST-"    ROLE_CN="访客"       ;;
  *)
    echo "[错误] 无效角色: $ROLE"
    echo "可选: guest | member | vip | admin | super_admin"
    exit 1
    ;;
esac

CODE="${PREFIX}$(openssl rand -hex 6 | tr 'a-f' 'A-F')"

# 生成临时 Node 脚本 → scp → ECS 执行
# 注意: heredoc 不带引号，让 bash 展开 $CODE $ROLE $TTL_HOURS $ECS_DB
TMP_JS="/tmp/gen-invite-$$.js"
cat > "$TMP_JS" << ENDSCRIPT
const Database = require('better-sqlite3');
const MAX_RETRIES = 5;
const RETRY_DELAY = 3000; // 3 seconds

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function main() {
  for (let i = 0; i <= MAX_RETRIES; i++) {
    const db = new Database('$ECS_DB');
    db.pragma('busy_timeout=15000');
    try {
      db.prepare("INSERT INTO invite_codes (code, phone, role, expires_at) VALUES (?, 'admin', ?, datetime('now', '+$TTL_HOURS hours'))").run('$CODE', '$ROLE');
      const row = db.prepare('SELECT expires_at FROM invite_codes WHERE code=?').get('$CODE');
      console.log(row ? row.expires_at : 'ERROR');
      db.close();
      return;
    } catch (e) {
      db.close();
      if (i < MAX_RETRIES && e.code === 'SQLITE_BUSY') {
        await sleep(RETRY_DELAY);
      } else {
        console.log('ERROR');
        return;
      }
    }
  }
}
main();
ENDSCRIPT

# Copy to ECS (into /opt/dashboard so Node resolves better-sqlite3)
scp -q -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
  "$TMP_JS" "$ECS_HOST:/opt/dashboard/gen-invite-tmp.js" || {
  echo "[错误] scp 到 ECS 失败"
  rm -f "$TMP_JS"
  exit 1
}

# Execute on ECS
RESULT=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
  "$ECS_HOST" "cd /opt/dashboard && node gen-invite-tmp.js; rm -f gen-invite-tmp.js")
rm -f "$TMP_JS"

if [ "$RESULT" = "ERROR" ] || [ -z "$RESULT" ]; then
  echo "[错误] 写入 ECS 失败，请检查 SSH 连接和数据库状态"
  exit 1
fi

EXPIRES="$RESULT"

echo ""
echo "  ╔══════════════════════════════════╗"
echo "  ║  天工 Dashboard 邀请码          ║"
echo "  ╠══════════════════════════════════╣"
printf "  ║  %-30s  ║\n" "$CODE"
echo "  ╠══════════════════════════════════╣"
printf "  ║  角色:   %-22s  ║\n" "$ROLE_CN"
printf "  ║  有效期: %-22s  ║\n" "${TTL_HOURS}小时"
printf "  ║  过期:   %-22s  ║\n" "$EXPIRES"
echo "  ╚══════════════════════════════════╝"
echo ""
