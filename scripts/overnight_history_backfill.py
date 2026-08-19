#!/usr/bin/env python3
"""Overnight history backfill orchestrator — sync → safe backfill → coverage.

Runs without interactive confirmation. Checkpoint-resumable.
workers=1 for baostock stability (no multi-process login races).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nous.core.paths import log_dir

PY = sys.executable
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
LOG = log_dir()
LOG.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str], log_name: str) -> int:
    log_path = LOG / log_name
    print(f"==> {' '.join(cmd)}", flush=True)
    print(f"    log={log_path}", flush=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n===== START {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        fh.flush()
        p = subprocess.run(cmd, cwd=str(ROOT), env=ENV, stdout=fh, stderr=subprocess.STDOUT)
        fh.write(f"\n===== END rc={p.returncode} {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    print(f"    rc={p.returncode}", flush=True)
    return p.returncode


def main() -> int:
    steps: list[tuple[list[str], str]] = [
        (
            [PY, "scripts/sync_hot_to_year.py", "--year", "2026", "--start", "2026-01-01", "--end", "2026-12-31"],
            "sync_2026.log",
        ),
        (
            [PY, "scripts/sync_hot_to_year.py", "--year", "2025", "--start", "2025-07-15", "--end", "2025-12-31"],
            "sync_2025_hot.log",
        ),
        # 2014 full market — single worker, baostock primary
        (
            [PY, "scripts/backfill_year_partition.py", "--year", "2014", "--workers", "1"],
            "backfill_2014.log",
        ),
        # Hole-fill 2015–2019 (e.g. 000001 missing from early partitions)
        *[
            (
                [PY, "scripts/backfill_year_partition.py", "--year", str(y), "--hole-fill", "--workers", "1"],
                f"backfill_{y}_holes.log",
            )
            for y in range(2015, 2020)
        ],
        # 2025 thin days Jan–mid May
        (
            [
                PY,
                "scripts/backfill_year_partition.py",
                "--year",
                "2025",
                "--start",
                "2025-01-01",
                "--end",
                "2025-05-18",
                "--thin-only",
                "--workers",
                "1",
            ],
            "backfill_2025_thin.log",
        ),
        (
            [
                PY,
                "scripts/report_history_coverage.py",
                "--out",
                "docs/data/freshness/2026-07-17/HISTORY_COVERAGE.md",
            ],
            "coverage_report.log",
        ),
    ]

    status_path = LOG / "overnight_backfill_status.txt"
    worst = 0
    for cmd, log_name in steps:
        status_path.write_text(
            f"running: {' '.join(cmd)}\nstarted: {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
            encoding="utf-8",
        )
        rc = run(cmd, log_name)
        worst = max(worst, abs(rc))

    # Factor recompute is heavy — write launch script; do not block overnight on full recompute.
    factor_sh = LOG / "run_factor_recompute_2015plus.sh"
    factor_sh.write_text(
        "#!/bin/bash\n"
        f"cd {ROOT}\n"
        "export PYTHONPATH=src\n"
        "exec .venv/bin/python -m nous.engine.ml.factor_compute save "
        "--start 2015-01-01 --engine pandas "
        f">> {LOG}/factor_recompute_2015plus.log 2>&1\n",
        encoding="utf-8",
    )
    factor_sh.chmod(0o755)

    status_path.write_text(
        f"finished: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"worst_rc={worst}\n"
        f"factor_script={factor_sh}\n",
        encoding="utf-8",
    )
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
