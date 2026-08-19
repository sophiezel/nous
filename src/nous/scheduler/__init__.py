"""APScheduler-based job scheduler — replaces Hermes cron.

Jobs run via subprocess for robustness — no import-time coupling.
Each job definition points to a real CLI command or Python module.

Usage:
    python -m nous.scheduler           # start daemon
    python -m nous.scheduler --list    # list jobs
    python -m nous.scheduler --run <name>  # run one job
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("nous.scheduler")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # nous repo root (src/nous/scheduler → root)
VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python3")


def _archive_job_should_skip(job_name: str) -> bool:
    """Skip archive jobs when backups/factors already live on a missing volume."""
    try:
        from nous.core.volume import archive_job_should_skip

        return archive_job_should_skip(job_name)
    except Exception as exc:
        logger.warning("volume check failed for %s: %s", job_name, exc)
        return False


def _run_cmd(cmd: str, timeout: int = 300, workdir: str = ""):
    """Run a command via subprocess, log output."""
    cwd = workdir or str(PROJECT_ROOT)
    logger.info(f"Running: {cmd}")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        if result.stdout:
            for line in result.stdout.strip().split("\n")[:20]:
                logger.info(f"  {line}")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[:10]:
                logger.warning(f"  stderr: {line}")
        if result.returncode != 0:
            logger.error(f"  exit={result.returncode}")
    except subprocess.TimeoutExpired:
        logger.error(f"  TIMEOUT after {timeout}s")
    except Exception as e:
        logger.error(f"  FAILED: {e}")


# ── Job definitions ─────────────────────────────────────────────────────
# Format: (name, schedule, command, description, timeout_sec)
#
# Freshness Provider DAG (2026-07-23):
#   post-close-chain = S1 Features → S2 Factors → S3 Assert → S4 Consume → S5 Observe
#   morning-chain    = assert → remediable reproduce → re-assert
# Close-path ETL/assert/recommend ONLY via these chains (no parallel legacy cron).

JOBS = [
    # === Provider DAG (canonical) ===
    ("post-close-chain",   "40 16 * * 1-5", f"{VENV_PYTHON} -m nous.data.quality.pipeline_dag post-close", "收盘全链路", 7200),
    ("morning-chain",      "30 8 * * 1-5",  f"{VENV_PYTHON} -m nous.data.quality.pipeline_dag morning", "早间断言+补产", 1800),

    # === Intraday / off-DAG collection ===
    ("daily-rollover",     "0 2 * * *",     f"bash {PROJECT_ROOT}/scripts/daily_rollover.sh", "日切", 600),
    ("minute-collector",   "* 9-11,13-15 * * 1-5", f"{VENV_PYTHON} -m nous.data.collectors.minute_collector", "分钟行情", 60),
    ("futures-fetch",      "30 15 * * 1-5", f"{VENV_PYTHON} -c \"from nous.data.collectors.unified import collect_futures; collect_futures()\"", "期货(盘后早取)", 120),
    ("global-index",       "30 5 * * 2-6",  f"{VENV_PYTHON} -c \"from nous.data.collectors.unified import collect_global_index; collect_global_index()\"", "全球指数", 300),
    ("margin-daily",       "5 8 * * 1-5",   f"{VENV_PYTHON} -c \"from nous.data.collectors.unified import collect_margin; collect_margin()\"", "融资融券(早盘前)", 120),
    ("industry-weekly",    "0 4 * * 0",      f"{VENV_PYTHON} -c \"from nous.data.collectors.unified import collect_industry; collect_industry()\"", "行业分类(周)", 300),
    ("hk-backfill",        "*/30 * * * *",   f"bash {PROJECT_ROOT}/scripts/hk_daily_backfill.sh", "港股回补", 600),
    ("backfill-bao",       "0 3 * * *",      f"{VENV_PYTHON} -m nous.data.collectors.gap_repair", "历史回补", 900),
    ("etf-flow-backfill",  "0 4 * * 0",      f"{VENV_PYTHON} -m nous.data.collectors.etf_flow_collector", "ETF回补", 300),

    # === Trading ===
    ("trader-open-buy",    "32 9 * * 1-5",  f"{VENV_PYTHON} {PROJECT_ROOT}/src/nous/scheduler/jobs/trading/trader_open_buy.py", "开盘买入", 30),
    ("trader-hk-close",    "12 16 * * 1-5", f"{VENV_PYTHON} {PROJECT_ROOT}/src/nous/scheduler/jobs/trading/trader_hk_close.py", "港股收盘", 30),
    ("trader-poll-hk",     "35 9 * * 1-5",  f"{VENV_PYTHON} {PROJECT_ROOT}/src/nous/scheduler/jobs/trading/trader_poll.py", "港股轮询", 60),
    ("portfolio-risk",     "10 9 * * 1-5",  f"{VENV_PYTHON} -m nous.trader.risk", "持仓风控", 60),
    ("sim-executor",       "31,1 9-11,14 * * 1-5", f"{VENV_PYTHON} -m nous.data.collectors.sim_executor", "模拟执行", 120),
    ("sim-reconciler",     "38 16 * * *",   f"{VENV_PYTHON} -m nous.data.collectors.sim_reconciler", "模拟对账", 60),
    ("preflight",          "30 9 * * 1-5",  f"{VENV_PYTHON} -c \"from nous.data.quality.validators import preflight; preflight()\"", "开盘检查", 30),
    ("midday-patrol",      "0 11 * * 1-5",  f"{VENV_PYTHON} -c \"from nous.trader.portfolio import midday_patrol; midday_patrol()\"", "午间巡逻", 30),

    # === Reports (non-DAG) ===
    ("performance",        "52 16 * * 1-5", f"{VENV_PYTHON} -m nous.trader.reporter", "交易绩效", 120),
    ("portfolio-review",   "20 16 * * 1-5", f"{VENV_PYTHON} -m nous.trader.portfolio portfolio_review", "持仓审计", 120),

    # === ML ===
    ("weekly-train",       "0 2 * * 0",     f"{VENV_PYTHON} -m nous.engine.ml.weekly_retrain", "周度训练", 3600),
    ("factor-full-recompute","30 2 * * 0",  f"{VENV_PYTHON} -m nous.engine.ml.factor_compute save --start 2015-01-01", "因子全量重算(周)", 7200),
    ("model-health",       "30 16 * * 1-5", f"{VENV_PYTHON} -c \"from nous.engine.ml.retrain_trigger import check; check()\"", "模型健康", 120),
    ("ai-pool-refresh",    "31 15 * * 1-5", f"{VENV_PYTHON} -m nous.data.collectors.fetchers.ai_pool_refresh", "AI池刷新", 300),
    ("ai-chain-phase",     "0 9 * * 1-5",   f"{VENV_PYTHON} -c \"from nous.engine.signals.concept_signals import chain_phase; chain_phase()\"", "AI链相位", 60),
    ("ai-chain-signals",   "5 9 * * 1-5",   f"{VENV_PYTHON} -c \"from nous.engine.signals.concept_signals import chain_signals; chain_signals()\"", "AI链信号", 60),

    # === Maintenance ===
    ("db-backup",          "0 * * * *",      f"{VENV_PYTHON} -c \"from nous.data.storage import backup; backup.run_hourly()\"", "DB备份", 300),
    ("db-maintain",        "0 4 * * *",      f"{VENV_PYTHON} -c \"from nous.data.storage import maintenance; maintenance.quick()\"", "DB维护", 120),
    ("db-maintain-deep",   "30 4 * * 0",     f"{VENV_PYTHON} -c \"from nous.data.storage import maintenance; maintenance.deep()\"", "DB深度维护", 600),
    ("monthly-archive",    "0 3 1 * *",      f"bash {PROJECT_ROOT}/scripts/monthly_archive.sh", "月度归档", 600),
    ("data-cleanup",       "0 3 * * *",      f"{VENV_PYTHON} -c \"from nous.data.quality.etl_metrics import cleanup; cleanup()\"", "过期清理", 120),
    ("db-integrity",       "0 4 * * 0",      f"{VENV_PYTHON} -c \"from nous.data.storage import maintenance; maintenance.integrity_check()\"", "完整性检查", 600),

    # === Bridge ===
    ("bridge-dashboard",   "20 9 * * 1-5",  f"{VENV_PYTHON} -m nous.data.bridge_to_dashboard", "Dashboard桥接", 60),
    ("sync-dashboard",     "45 16 * * 1-5", f"bash {PROJECT_ROOT}/scripts/sync_dashboard.sh", "Dashboard同步", 120),
    ("sync-reports",       "5 17 * * 1-5",  f"bash {PROJECT_ROOT}/scripts/sync_reports.sh", "报告同步", 60),
]


# ── Scheduler ───────────────────────────────────────────────────────────

def start():
    """Start the APScheduler daemon."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("ERROR: apscheduler not installed. Run: pip install apscheduler")
        sys.exit(1)

    sched = BackgroundScheduler(timezone="Asia/Shanghai")

    for name, schedule, cmd, desc, timeout in JOBS:
        # Closure capture fix: use default args
        def make_runner(c=cmd, t=timeout, n=name):
            def runner():
                if _archive_job_should_skip(n):
                    logger.info("SKIP %s: archive volume not mounted", n)
                    return
                _run_cmd(c, timeout=t)
            return runner

        trigger = CronTrigger.from_crontab(schedule)
        sched.add_job(make_runner(), trigger=trigger, id=name, name=desc, replace_existing=True)

    sched.start()
    logger.info(f"Nous Scheduler started — {len(JOBS)} jobs registered")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sched.shutdown()


def list_jobs():
    """Print all registered jobs."""
    print(f"{'NAME':<25} {'SCHEDULE':<22} {'DESCRIPTION':<20} {'TIMEOUT':>8}")
    print("-" * 78)
    for name, schedule, cmd, desc, timeout in JOBS:
        print(f"{name:<25} {schedule:<22} {desc:<20} {timeout:>6}s")
    print(f"\n{len(JOBS)} jobs registered")


def run_job(name: str):
    """Manually trigger a single job by name."""
    for jname, schedule, cmd, desc, timeout in JOBS:
        if jname == name:
            if _archive_job_should_skip(name):
                print(f"SKIP {name}: archive volume not mounted")
                return
            print(f"Running: {name} — {desc}")
            _run_cmd(cmd, timeout=timeout)
            return
    print(f"Job not found: {name}")


# ── CLI entry ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    if "--list" in sys.argv:
        list_jobs()
    elif "--run" in sys.argv:
        idx = sys.argv.index("--run")
        if idx + 1 < len(sys.argv):
            run_job(sys.argv[idx + 1])
        else:
            print("Usage: --run <job_name>")
    else:
        start()
