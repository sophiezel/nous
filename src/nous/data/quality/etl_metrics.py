"""ETL耗时指标收集 — 优化7

追踪每次数据更新的:
- 耗时
- 数据量(行数)
- 成功率
- 错误类型分布

存储到 screener.db 的 etl_metrics 表 + JSON快照。

用法:
  from nous.data.quality.etl_metrics import ETLSession
  with ETLSession("daily_update") as sess:
      sess.record_phase("fetch", rows=5200, duration_s=120)
      sess.record_phase("validate", rows=5195, duration_s=2)
"""

import sqlite3
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"
METRICS_JSON = Path.home() / "wiki" / "finance" / "raw" / "etl_metrics.json"


def init_table():
    """创建etl_metrics表"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS etl_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        task_name TEXT NOT NULL,
        phase TEXT NOT NULL,
        started_at TIMESTAMP,
        duration_s REAL,
        rows_processed INTEGER,
        rows_failed INTEGER DEFAULT 0,
        errors TEXT,
        notes TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_etl_task ON etl_metrics(task_name, started_at);
    CREATE INDEX IF NOT EXISTS idx_etl_session ON etl_metrics(session_id);
    ''')
    conn.commit()
    conn.close()


class ETLPhase:
    """单个ETL阶段"""
    def __init__(self, name: str):
        self.name = name
        self.t0 = time.time()
        self.rows = 0
        self.failed = 0
        self.errors: list[str] = []
        self.done = False
    
    def finish(self, rows: int = 0, failed: int = 0, error: str = ""):
        self.duration_s = round(time.time() - self.t0, 2)
        self.rows = rows
        self.failed = failed
        if error:
            self.errors.append(error)
        self.done = True


class ETLSession:
    """一次性ETL会话，记录所有阶段"""
    
    def __init__(self, task_name: str):
        self.task_name = task_name
        self.session_id = f"{task_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.phases: list[ETLPhase] = []
        self._started = False
        self._t_start = 0.0
    
    def __enter__(self):
        init_table()
        self._started = True
        self._t_start = time.time()
        return self
    
    def __exit__(self, *args):
        self._save()
    
    def record_phase(self, phase_name: str, rows: int = 0, 
                     failed: int = 0, duration_s: float = 0,
                     error: str = "", notes: str = ""):
        """记录一个已完成阶段"""
        phase = ETLPhase(phase_name)
        phase.rows = rows
        phase.failed = failed
        phase.duration_s = duration_s if duration_s > 0 else round(time.time() - self._t_start, 2)
        if error:
            phase.errors.append(error)
        phase.done = True
        self.phases.append(phase)
        return phase
    
    def start_phase(self, phase_name: str) -> ETLPhase:
        """开始一个阶段(调用方自己finish)"""
        phase = ETLPhase(phase_name)
        self.phases.append(phase)
        return phase
    
    def _save(self):
        """保存到SQLite + JSON快照"""
        total_duration = round(time.time() - self._t_start, 2)
        total_rows = sum(p.rows for p in self.phases)
        
        # SQLite
        try:
            conn = sqlite3.connect(str(DB_PATH))
            for p in self.phases:
                conn.execute(
                    "INSERT INTO etl_metrics (session_id, task_name, phase, started_at, duration_s, rows_processed, rows_failed, errors, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.session_id, self.task_name, p.name,
                     datetime.now().isoformat(), p.duration_s,
                     p.rows, p.failed,
                     "; ".join(p.errors)[:500] if p.errors else None,
                     None)
                )
            # 清理>90天旧数据
            conn.execute("DELETE FROM etl_metrics WHERE started_at < datetime('now', '-90 days')")
            conn.commit()
            conn.close()
        except Exception:
            pass
        
        # JSON快照
        summary = {
            "session_id": self.session_id,
            "task_name": self.task_name,
            "timestamp": datetime.now().isoformat(),
            "total_duration_s": total_duration,
            "total_rows": total_rows,
            "phases": [
                {"name": p.name, "duration_s": p.duration_s, 
                 "rows": p.rows, "failed": p.failed,
                 "errors": p.errors}
                for p in self.phases
            ]
        }
        try:
            METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
            METRICS_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        except Exception:
            pass


def get_recent_metrics(task_name: str = None, limit: int = 10) -> list[dict]:
    """获取最近的ETL指标"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if task_name:
        rows = conn.execute(
            "SELECT * FROM etl_metrics WHERE task_name=? ORDER BY started_at DESC LIMIT ?",
            (task_name, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM etl_metrics ORDER BY started_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_etl_summary(days: int = 30) -> dict:
    """获取最近N天ETL汇总"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT task_name, COUNT(*) as runs,
               ROUND(AVG(duration_s),1) as avg_duration,
               ROUND(MAX(duration_s),1) as max_duration,
               SUM(rows_processed) as total_rows
        FROM etl_metrics 
        WHERE started_at > datetime('now', ?) AND phase = 'total'
        GROUP BY task_name ORDER BY avg_duration DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [{"task": r[0], "runs": r[1], "avg_s": r[2], "max_s": r[3], "total_rows": r[4]} for r in rows]


def cleanup(keep_days: int = 90) -> dict:
    """Prune old etl_metrics rows (scheduler data-cleanup job)."""
    path = Path.home() / "nous-data" / "screener.db"
    if not path.exists():
        path = Path(__file__).resolve().parents[4] / "data" / "screener.db"
    if not path.exists():
        return {"ok": False, "deleted": 0, "reason": "no db"}
    try:
        conn = sqlite3.connect(str(path))
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS etl_metrics ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session_id TEXT, task_name TEXT, phase TEXT,"
            "started_at TIMESTAMP, duration_s REAL,"
            "rows_processed INTEGER, rows_failed INTEGER DEFAULT 0,"
            "errors TEXT, notes TEXT);"
        )
        cur = conn.execute(
            "DELETE FROM etl_metrics WHERE started_at < datetime('now', ?)",
            (f"-{keep_days} days",),
        )
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        return {"ok": True, "deleted": deleted, "keep_days": keep_days}
    except Exception as e:
        return {"ok": False, "deleted": 0, "reason": str(e)}


if __name__ == "__main__":
    # 快速测试
    with ETLSession("manual_test") as sess:
        sess.record_phase("fetch", rows=5200, duration_s=45.2)
        sess.record_phase("validate", rows=5195, failed=5, duration_s=2.1)
        sess.record_phase("save", rows=5195, duration_s=1.5)
    
    print("ETL metrics saved")
    summary = get_etl_summary(30)
    for s in summary:
        print(f"  {s['task']}: {s['runs']}runs avg={s['avg_s']}s rows={s['total_rows']}")
