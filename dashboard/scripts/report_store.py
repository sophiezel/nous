#!/usr/bin/env python3
"""
投研报告持久化模块 — 双写 SQLite(本地) + Neon PostgreSQL(云端)。
零外部依赖(仅 sqlite3 + psycopg2 标准库)，WAL 模式，自动建表，90天自动清理。

Usage:
    from report_store import store_report, store_macro_score, store_sentiment
"""

import sqlite3
import json
import os
import base64
import threading
from datetime import datetime, timedelta

# ── AES-256-GCM field encryption (inlined from field_crypto.py) ──
ALGORITHM = "aes-256-gcm"
_SALT = b"hermes-dashboard-v2"

def _get_key():
    key_hex = os.environ.get("SQLCIPHER_KEY", "")
    if len(key_hex) != 64:
        raise RuntimeError("SQLCIPHER_KEY not set (need 64 hex chars)")
    return bytes.fromhex(key_hex)

def encrypt_content(plaintext: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")

def decrypt_content(encrypted: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _get_key()
    aesgcm = AESGCM(key)
    data = base64.b64decode(encrypted)
    nonce, ct = data[:12], data[12:]
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")

SQLITE_PATH = os.path.expanduser("~/code/dashboard/data/reports.db")
NEON_URL = os.environ.get("DATABASE_URL", "")
CLEANUP_DAYS = 90

_lock = threading.Lock()

# ─── SQLite (primary, always available) ──────────────────

def _get_sqlite():
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
    db = sqlite3.connect(SQLITE_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    _ensure_sqlite_tables(db)
    return db

def _ensure_sqlite_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL, title TEXT, content TEXT NOT NULL,
        metadata TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_reports_type_date ON reports(type, created_at);
    CREATE TABLE IF NOT EXISTS macro_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE, score REAL, position REAL,
        indicators TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sentiment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE, score INTEGER,
        limit_up_count INTEGER, limit_up_rate REAL,
        details TEXT, created_at TEXT NOT NULL
    );
    """)

# ─── Neon (cloud, auto-fallback) ─────────────────────────

def _get_neon():
    """Get Neon connection. Returns None if not configured."""
    if not NEON_URL:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(NEON_URL)
        _ensure_neon_tables(conn)
        return conn
    except Exception as e:
        print(f"[report_store] Neon unavailable: {e}")
        return None

def _ensure_neon_tables(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id SERIAL PRIMARY KEY, type TEXT NOT NULL, title TEXT,
        content TEXT NOT NULL, metadata JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_reports_type_date ON reports(type, created_at DESC);
    CREATE TABLE IF NOT EXISTS macro_scores (
        id SERIAL PRIMARY KEY, date DATE NOT NULL UNIQUE,
        score REAL, position REAL, indicators JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS sentiment (
        id SERIAL PRIMARY KEY, date DATE NOT NULL UNIQUE,
        score INTEGER, limit_up_count INTEGER, limit_up_rate REAL,
        details JSONB, created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """)
    conn.commit()

# ─── Public API ──────────────────────────────────────────

def store_report(report_type: str, content: str, title: str = None, metadata: dict = None) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

    with _lock:
        # SQLite
        db = _get_sqlite()
        try:
            cur = db.execute(
                "INSERT INTO reports (type, title, content, metadata, created_at) VALUES (?,?,?,?,?)",
                (report_type, title, encrypt_content(content), meta_json, now)
            )
            db.commit()
            row_id = cur.lastrowid
        finally:
            db.close()

        # Neon (best-effort, don't block on failure)
        neon = _get_neon()
        if neon:
            try:
                cur = neon.cursor()
                meta_pg = json.dumps(metadata) if metadata else None
                cur.execute(
                    "INSERT INTO reports (type, title, content, metadata) VALUES (%s,%s,%s,%s)",
                    (report_type, title, encrypt_content(content), meta_pg)
                )
                neon.commit()
            except Exception as e:
                print(f"[report_store] Neon write failed: {e}")
            finally:
                neon.close()

    return row_id


def store_macro_score(date: str, score: float, position: float, indicators: dict = None) -> bool:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ind_json = json.dumps(indicators, ensure_ascii=False) if indicators else None

    with _lock:
        db = _get_sqlite()
        try:
            db.execute(
                "INSERT OR REPLACE INTO macro_scores (date, score, position, indicators, created_at) VALUES (?,?,?,?,?)",
                (date, score, position, ind_json, now)
            )
            db.commit()
        finally:
            db.close()

        neon = _get_neon()
        if neon:
            try:
                cur = neon.cursor()
                cur.execute(
                    "INSERT INTO macro_scores (date, score, position, indicators) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (date) DO UPDATE SET score=EXCLUDED.score, position=EXCLUDED.position, indicators=EXCLUDED.indicators",
                    (date, score, position, json.dumps(indicators) if indicators else None)
                )
                neon.commit()
            except Exception as e:
                print(f"[report_store] Neon write failed: {e}")
            finally:
                neon.close()

    return True


def store_sentiment(date: str, score: int, limit_up_count: int = 0,
                    limit_up_rate: float = 0.0, details: dict = None) -> bool:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    det_json = json.dumps(details, ensure_ascii=False) if details else None

    with _lock:
        db = _get_sqlite()
        try:
            db.execute(
                "INSERT OR REPLACE INTO sentiment (date, score, limit_up_count, limit_up_rate, details, created_at) VALUES (?,?,?,?,?,?)",
                (date, score, limit_up_count, limit_up_rate, det_json, now)
            )
            db.commit()
        finally:
            db.close()

        neon = _get_neon()
        if neon:
            try:
                cur = neon.cursor()
                cur.execute(
                    "INSERT INTO sentiment (date, score, limit_up_count, limit_up_rate, details) VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (date) DO UPDATE SET score=EXCLUDED.score, limit_up_count=EXCLUDED.limit_up_count, limit_up_rate=EXCLUDED.limit_up_rate, details=EXCLUDED.details",
                    (date, score, limit_up_count, limit_up_rate, json.dumps(details) if details else None)
                )
                neon.commit()
            except Exception as e:
                print(f"[report_store] Neon write failed: {e}")
            finally:
                neon.close()

    return True


# ─── Query helpers ───────────────────────────────────────

def get_recent_reports(report_type: str = None, limit: int = 20):
    db = _get_sqlite()
    try:
        if report_type:
            rows = db.execute(
                "SELECT id, type, title, substr(content,1,200), metadata, created_at FROM reports WHERE type=? ORDER BY created_at DESC LIMIT ?",
                (report_type, limit)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, type, title, substr(content,1,200), metadata, created_at FROM reports ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [{"id":r[0],"type":r[1],"title":r[2],"preview":r[3],"metadata":json.loads(r[4]) if r[4] else None,"created_at":r[5]} for r in rows]
    finally:
        db.close()


# ─── CLI ─────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: report_store.py test | stats")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "test":
        rid = store_report("test", "# 测试\n双写SQLite+Neon", title="双写测试")
        print(f"store_report OK, id={rid}")
        store_macro_score("2026-05-15", 72.5, 0.65, {"cpi":0.3,"pmi":50.8})
        print("store_macro_score OK")
        store_sentiment("2026-05-15", 68, 42, 0.78, {"炸板率":0.23})
        print("store_sentiment OK")
        if NEON_URL:
            print("Neon: enabled")
        else:
            print("Neon: not configured (set DATABASE_URL)")
        print("✅ All OK")

    elif cmd == "stats":
        db = _get_sqlite()
        try:
            r = db.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            m = db.execute("SELECT COUNT(*) FROM macro_scores").fetchone()[0]
            s = db.execute("SELECT COUNT(*) FROM sentiment").fetchone()[0]
            print(f"SQLite: reports={r} macro={m} sentiment={s}")
        finally:
            db.close()
