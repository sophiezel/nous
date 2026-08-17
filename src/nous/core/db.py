"""Unified SQLite connection management — WAL mode, busy_timeout, connection pooling.

Usage:
    from nous.core.db import get_db
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM stock_daily").fetchall()

    # Write connection (higher busy_timeout):
    conn = get_db(write=True)
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


# ── Default timeouts ───────────────────────────────────────────────────
WRITE_BUSY_TIMEOUT = 30000  # 30s for write operations
READ_BUSY_TIMEOUT = 5000  # 5s for read operations


def _resolve_path(db_name: str) -> str:
    """Resolve a database path relative to the configured data directory.

    If ``db_name`` starts with ``~``, ``/``, or is an in-memory database,
    it is returned as-is. Otherwise it is resolved relative to
    ``config.nous.data_dir``.
    """
    # In-memory or absolute path
    if db_name == ":memory:" or db_name.startswith("/") or db_name.startswith("~"):
        return str(Path(db_name).expanduser())

    override = os.environ.get("NOUS_DATA_DIR", "").strip()
    if override:
        data_dir = Path(override).expanduser()
    else:
        try:
            from nous.core.config import config

            data_dir = Path(config.nous.data_dir).expanduser()
        except Exception:
            data_dir = Path.home() / "nous-data"

    full_path = data_dir / db_name
    return str(full_path)


def _connect(path: str, write: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection with proper pragmas.

    Args:
        path: Database file path (or ``:memory:``).
        write: If True, sets higher busy_timeout and opens read-write.
               If False, opens read-only when possible.

    Returns:
        A configured sqlite3.Connection.
    """
    # Ensure parent directory exists
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    if write:
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(f"PRAGMA busy_timeout = {WRITE_BUSY_TIMEOUT}")
    else:
        # Try read-only mode for file-based DBs
        if path != ":memory:" and Path(path).exists():
            uri = f"file:{path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        else:
            conn = sqlite3.connect(path)
        conn.execute(f"PRAGMA busy_timeout = {READ_BUSY_TIMEOUT}")

    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row

    return conn


@contextmanager
def get_db(db_name: str = "screener.db", write: bool = False) -> Generator[sqlite3.Connection, None, None]:
    """Get a SQLite connection as a context manager.

    Usage::

        with get_db() as conn:
            rows = conn.execute("SELECT ...").fetchall()

        with get_db(write=True) as conn:
            conn.execute("INSERT INTO ...")
            conn.commit()

    Args:
        db_name: Database identifier or path. Defaults to ``"screener.db"``
                 which resolves to ``<data_dir>/screener.db``.
        write: If True, open with write permissions and higher busy_timeout.

    Yields:
        A configured sqlite3.Connection. Auto-closed on context exit.
    """
    path = _resolve_path(db_name)
    conn = _connect(path, write=write)
    try:
        yield conn
    finally:
        conn.close()


def get_readonly_db(db_name: str = "screener.db"):
    """Legacy compatibility: open a read-only connection."""
    from nous.data.storage import connect_readonly
    return connect_readonly()


def safe_query(query: str, params: tuple = ()) -> list:
    """Execute a read-only query and return results as list of dicts."""
    from nous.data.storage import connect_readonly
    conn = connect_readonly()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
SCREENER_DB = "screener.db"
REPORTS_DB = "reports.db"
