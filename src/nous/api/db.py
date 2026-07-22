"""只读 SQLite 连接 — 线程级单例, busy_timeout, 重试"""
import sqlite3, os, time, threading

SCREENER_DB = os.path.expanduser("~/code/stock-screener/data/screener.db")
REPORTS_DB = os.path.expanduser("~/code/dashboard/data/reports.db")

# 线程本地存储: 每个线程持有自己的只读连接
_local = threading.local()

def _connect_readonly(path: str) -> sqlite3.Connection:
    for attempt in range(3):
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            db.execute("PRAGMA busy_timeout = 30000")
            db.row_factory = sqlite3.Row
            return db
        except sqlite3.OperationalError:
            if attempt < 2:
                time.sleep(0.5)
            else:
                raise

def get_readonly_db(path: str) -> sqlite3.Connection:
    """每个线程独立连接, 避免多线程竞争同一个连接对象"""
    if not hasattr(_local, "connections"):
        _local.connections = {}
    if path not in _local.connections:
        _local.connections[path] = _connect_readonly(path)
    return _local.connections[path]

def safe_query(db_path: str, sql: str, params=()):
    """统一查询: try/catch → HTTPException"""
    from fastapi import HTTPException
    try:
        db = get_readonly_db(db_path)
        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError as e:
        raise HTTPException(503, detail=f"Database error: {e}")
