"""Allow: python -m nous.scheduler"""
from nous.scheduler import start, list_jobs, run_job
import sys

if "--list" in sys.argv:
    list_jobs()
elif "--run" in sys.argv:
    idx = sys.argv.index("--run")
    if idx + 1 < len(sys.argv):
        run_job(sys.argv[idx + 1])
    else:
        print("Usage: python -m nous.scheduler --run <job_name>")
else:
    start()
