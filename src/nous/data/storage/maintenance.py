"""DB maintenance stubs for scheduler jobs (quick / deep / integrity_check)."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def _db_path() -> Path:
    p = Path.home() / "nous-data" / "screener.db"
    if p.exists():
        return p
    return Path(__file__).resolve().parents[4] / "data" / "screener.db"


def quick() -> dict:
    """Lightweight maintenance: ANALYZE + WAL checkpoint."""
    path = _db_path()
    if not path.exists():
        return {"ok": False, "reason": "no db"}
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA optimize")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
        return {"ok": True, "action": "quick"}
    finally:
        conn.close()


def deep() -> dict:
    """Deeper maintenance: VACUUM + integrity_check."""
    path = _db_path()
    if not path.exists():
        return {"ok": False, "reason": "no db"}
    conn = sqlite3.connect(str(path))
    try:
        r = conn.execute("PRAGMA integrity_check").fetchone()
        ok = r and r[0] == "ok"
        if ok:
            conn.execute("VACUUM")
        return {"ok": bool(ok), "integrity": r[0] if r else None, "action": "deep"}
    finally:
        conn.close()


def integrity_check() -> dict:
    """Full integrity_check for weekly job."""
    path = _db_path()
    if not path.exists():
        return {"ok": False, "reason": "no db"}
    conn = sqlite3.connect(str(path))
    try:
        r = conn.execute("PRAGMA integrity_check").fetchone()
        ok = r and r[0] == "ok"
        logger.info("integrity_check: %s", r[0] if r else None)
        return {"ok": bool(ok), "result": r[0] if r else None}
    finally:
        conn.close()
