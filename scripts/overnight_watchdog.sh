#!/bin/bash
# Watchdog: keep overnight history backfill alive until coverage gate or morning.
set -u
ROOT="~/code/nous"
LOG="$HOME/nous-data/logs"
CKPT="$HOME/nous-data/backfill_checkpoints/stock_daily_2014.json"
PY="$ROOT/.venv/bin/python"
export PYTHONPATH="$ROOT/src"
mkdir -p "$LOG"

status() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG/watchdog.log"
}

run_step() {
  local name="$1"; shift
  status "START $name: $*"
  echo "running: $*" > "$LOG/overnight_backfill_status.txt"
  echo "started: $(date '+%F %T')" >> "$LOG/overnight_backfill_status.txt"
  "$@" >> "$LOG/${name}.log" 2>&1
  local rc=$?
  status "END $name rc=$rc"
  return $rc
}

# Ensure sync + view once
run_step sync_2026 "$PY" "$ROOT/scripts/sync_hot_to_year.py" --year 2026 --start 2026-01-01 --end 2026-12-31 || true
run_step sync_2025_hot "$PY" "$ROOT/scripts/sync_hot_to_year.py" --year 2025 --start 2025-07-15 --end 2025-12-31 || true

# 2014 until density OK or 8h
deadline=$(( $(date +%s) + 8*3600 ))
while true; do
  done_n=0
  if [[ -f "$CKPT" ]]; then
    done_n=$(python3 -c "import json;print(len(json.load(open('$CKPT')).get('done',[])))" 2>/dev/null || echo 0)
  fi
  avg=$(sqlite3 "$HOME/nous-data/screener.db" "SELECT ROUND(AVG(c),0) FROM (SELECT COUNT(*) c FROM stock_daily_2014 GROUP BY trade_date);" 2>/dev/null || echo 0)
  status "2014 progress done_ckpt=$done_n avg_per_day=$avg"
  # Gate: ~2000+ stocks/day typical for 2014
  if python3 -c "import sys; sys.exit(0 if float('${avg:-0}' or 0) >= 1800 else 1)"; then
    status "2014 density gate PASS avg=$avg"
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    status "2014 deadline reached; continue remaining steps anyway"
    break
  fi
  run_step backfill_2014 "$PY" -u "$ROOT/scripts/backfill_year_partition.py" --year 2014 --workers 1 || true
  # if script exits quickly with nothing to do, break
  pending=$(python3 - <<PY
import json,sqlite3
from pathlib import Path
ckpt=json.load(open("$CKPT")) if Path("$CKPT").exists() else {"done":[]}
done=set(ckpt.get("done") or [])
conn=sqlite3.connect(str(Path.home()/"nous-data"/"screener.db"))
syms=[r[0] for r in conn.execute("SELECT symbol FROM stock_basic WHERE market='a' AND symbol NOT LIKE '8%' AND symbol NOT LIKE '4%' AND symbol NOT LIKE '920%'")]
print(sum(1 for s in syms if s not in done))
PY
)
  status "pending_after_run=$pending"
  if [[ "${pending:-1}" -eq 0 ]]; then
    break
  fi
done

# Hole fills
for y in 2015 2016 2017 2018 2019; do
  run_step "backfill_${y}_holes" "$PY" -u "$ROOT/scripts/backfill_year_partition.py" --year "$y" --hole-fill --workers 1 || true
done

# 2025 thin
run_step backfill_2025_thin "$PY" -u "$ROOT/scripts/backfill_year_partition.py" --year 2025 \
  --start 2025-01-01 --end 2025-05-18 --thin-only --workers 1 || true

# Coverage
run_step coverage_report "$PY" "$ROOT/scripts/report_history_coverage.py" \
  --out "$ROOT/docs/data/freshness/2026-07-17/HISTORY_COVERAGE.md" || true

# Factor helper script
cat > "$LOG/run_factor_recompute_2015plus.sh" <<EOF
#!/bin/bash
cd "$ROOT"
export PYTHONPATH=src
exec .venv/bin/python -m nous.engine.ml.factor_compute save --start 2015-01-01 --engine pandas
EOF
chmod +x "$LOG/run_factor_recompute_2015plus.sh"

echo "finished: $(date '+%F %T')" > "$LOG/overnight_backfill_status.txt"
echo "factor_script=$LOG/run_factor_recompute_2015plus.sh" >> "$LOG/overnight_backfill_status.txt"
status "ALL STEPS COMPLETE"
