#!/bin/bash
# SSH tunnel keepalive — persistent Mac:3099 → ECS:3000
# Managed by launchd: ~/Library/LaunchAgents/com.hermes.ssh-tunnel.plist

: "${ECS_HOST:?Set ECS_HOST to user@host}"
SSH_KEY="${ECS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE="$ECS_HOST"
LOCAL_PORT=3099
REMOTE_PORT=3000
HEALTH_URL="http://127.0.0.1:${LOCAL_PORT}/login"
LOG_DIR="$HOME/Library/Logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/ssh-tunnel.log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "SSH tunnel starting: 127.0.0.1:${LOCAL_PORT} → ${REMOTE}:${REMOTE_PORT}"

FAIL_COUNT=0
MAX_FAIL=3

while true; do
    # Establish tunnel
    ssh -N -L ${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT} \
        -i "$SSH_KEY" \
        -o ServerAliveInterval=15 \
        -o ServerAliveCountMax=3 \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=10 \
        -o ExitOnForwardFailure=yes \
        "$REMOTE" 2>>"$LOG" &
    
    SSH_PID=$!
    log "SSH PID=$SSH_PID, waiting for connection..."
    sleep 5
    
    # Health check loop
    while kill -0 $SSH_PID 2>/dev/null; do
        if curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$HEALTH_URL" 2>/dev/null | grep -qE '^(200|403|301|302)'; then
            FAIL_COUNT=0
        else
            FAIL_COUNT=$((FAIL_COUNT + 1))
            log "health check fail ($FAIL_COUNT/$MAX_FAIL)"
            if [ $FAIL_COUNT -ge $MAX_FAIL ]; then
                log "too many failures, restarting tunnel"
                kill $SSH_PID 2>/dev/null
                sleep 2
                break
            fi
        fi
        sleep 30
    done
    
    log "SSH tunnel died, restarting in 5s..."
    sleep 5
    FAIL_COUNT=0
done
