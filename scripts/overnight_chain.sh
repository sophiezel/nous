#!/bin/bash
# Continue overnight steps; if 2014 backfill already running, wait for it.
set -u
ROOT="~/code/nous"
LOG="$HOME/nous-data/logs"
PY="$ROOT/.venv/bin/python"
export PYTHONPATH="$ROOT/src"
export PYTHONUNBUFFERED=1
mkdir -p "$LOG"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG/watchdog.log"; }

wait_for_2014() {
  local deadline=$(( $(date +%s) + 10*3600 ))
  while true; do
    if pgrep -f "backfill_year_partition.py --year 2014" >/dev/null; then
      local done_n avg
      done_n=$(python3 -c "import json,os; p=os.path.expanduser('~/nous-data/backfill_checkpoints/stock_daily_2014.json');
import pathlib; 
d=json.load(open(p)) if pathlib.Path(p).exists() else {'done':[]}; print(len(d.get('done',[])))" 2>/dev/null || echo 0)
      avg=$(sqlite3 "$HOME/nous-data/screener.db" "SELECT ROUND(AVG(c),0) FROM (SELECT COUNT(*) c FROM stock_daily_2014 GROUP BY trade_date);" 2>/dev/null || echo 0)
      log "waiting 2014 pid alive done=$done_n avg=$avg"
      sleep 60
      continue
    fi
    # not running — check gate or start
    avg=$(sqlite3 "$HOME/nous-data/screener.db" "SELECT ROUND(AVG(c),0) FROM (SELECT COUNT(*) c FROM stock_daily_2014 GROUP BY trade_date);" 2>/dev/null || echo 0)
    if python3 -c "import sys; sys.exit(0 if float('${avg:-0}' or 0) >= 1800 else 1)"; then
      log "2014 gate PASS avg=$avg"
      return 0
    fi
    if [[ $(date +%s) -ge $deadline ]]; then
      log "2014 wait deadline; avg=$avg"
      return 1
    fi
    log "starting 2014 backfill (avg=$avg)"
    echo "running: backfill 2014" > "$LOG/overnight_backfill_status.txt"
    "$PY" -u "$ROOT/scripts/backfill_year_partition.py" --year 2014 --workers 1 \
      >> "$LOG/backfill_2014.log" 2>&1 || true
  done
}

wait_for_2014

# Hole-fill later→earlier first optional; logic now scans ALL later years so
# 2015 can see 2020+ presence (fixes multi-year gaps like 000001/600519).
for y in 2019 2018 2017 2016 2015; do
  log "hole-fill $y"
  echo "running: hole-fill $y" > "$LOG/overnight_backfill_status.txt"
  "$PY" -u "$ROOT/scripts/backfill_year_partition.py" --year "$y" --hole-fill --workers 1 \
    >> "$LOG/backfill_${y}_holes.log" 2>&1 || true
done

log "2025 thin"
echo "running: 2025 thin" > "$LOG/overnight_backfill_status.txt"
"$PY" -u "$ROOT/scripts/backfill_year_partition.py" --year 2025 \
  --start 2025-01-01 --end 2025-05-18 --thin-only --workers 1 \
  >> "$LOG/backfill_2025_thin.log" 2>&1 || true

log "coverage report"
"$PY" "$ROOT/scripts/report_history_coverage.py" \
  --out "$ROOT/docs/data/freshness/2026-07-17/HISTORY_COVERAGE.md" \
  >> "$LOG/coverage_report.log" 2>&1 || true

cat > "$LOG/run_factor_recompute_2015plus.sh" <<EOF
#!/bin/bash
cd "$ROOT"
export PYTHONPATH=src PYTHONUNBUFFERED=1
exec .venv/bin/python -m nous.engine.ml.factor_compute save --start 2015-01-01 --engine pandas
EOF
chmod +x "$LOG/run_factor_recompute_2015plus.sh"

echo "finished: $(date '+%F %T')" > "$LOG/overnight_backfill_status.txt"
echo "factor_script=$LOG/run_factor_recompute_2015plus.sh" >> "$LOG/overnight_backfill_status.txt"
log "CHAIN COMPLETE"
