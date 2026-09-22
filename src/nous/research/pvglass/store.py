"""指标时序库 — ~/nous-data/pvglass.db

三张核心表:
    obs            指标观测值时序（indicator_id, obs_date, source 唯一）
    announcements  港交所公告（盈警/中报/回购/月报）
    consensus      券商一致预期与目标价
    fetch_log      采集审计日志
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from nous.core.db import get_db

DB_NAME = "pvglass.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS obs (
    indicator_id TEXT NOT NULL,
    obs_date     TEXT NOT NULL,
    value        REAL,
    value_text   TEXT,
    unit         TEXT,
    source       TEXT,
    source_url   TEXT,
    confidence   TEXT DEFAULT 'parsed',
    note         TEXT,
    ingested_at  TEXT,
    PRIMARY KEY (indicator_id, obs_date, source)
);
CREATE INDEX IF NOT EXISTS idx_obs_date ON obs(obs_date);
CREATE INDEX IF NOT EXISTS idx_obs_indicator ON obs(indicator_id, obs_date DESC);

CREATE TABLE IF NOT EXISTS announcements (
    stock_code TEXT NOT NULL,
    ann_date   TEXT NOT NULL,
    title      TEXT NOT NULL,
    category   TEXT,
    url        TEXT,
    fetched_at TEXT,
    PRIMARY KEY (stock_code, ann_date, title)
);
CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(ann_date DESC);

CREATE TABLE IF NOT EXISTS consensus (
    stock_code   TEXT NOT NULL,
    fiscal_year  TEXT NOT NULL,
    metric       TEXT NOT NULL,
    broker       TEXT NOT NULL DEFAULT 'CONSENSUS',
    value        REAL,
    unit         TEXT,
    rating       TEXT,
    target_price REAL,
    as_of        TEXT,
    source       TEXT,
    PRIMARY KEY (stock_code, fiscal_year, metric, broker, as_of)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,
    source      TEXT,
    status      TEXT,
    items       INTEGER DEFAULT 0,
    message     TEXT,
    duration_ms INTEGER
);
"""


@dataclass
class FetchResult:
    source: str
    status: str  # ok | partial | error | skipped
    items: int = 0
    message: str = ""
    duration_ms: int = 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


__all__ = [
    "DB_NAME",
    "SCHEMA",
    "FetchResult",
    "init_db",
    "ensure_schema",
    "record_obs",
    "record_many",
    "latest",
    "latest_map",
    "series",
    "previous",
    "window",
    "all_obs",
    "obs_dates",
    "cap_future",
    "upsert_announcements",
    "recent_announcements",
    "upsert_consensus",
    "consensus_for",
    "log_fetch",
    "last_fetch_log",
]


def init_db() -> None:
    with get_db(DB_NAME, write=True) as conn:
        ensure_schema(conn)


def _as_float(value: Any) -> float | None:
    """宽松数值转换：脏数据写库不如不写，但也不应阻断整批写入。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── obs ────────────────────────────────────────────────────────────────
def record_obs(
    conn: sqlite3.Connection,
    indicator_id: str,
    obs_date: str | date | None,
    value: float | None,
    *,
    unit: str = "",
    source: str = "",
    source_url: str = "",
    confidence: str = "parsed",
    note: str = "",
    value_text: str | None = None,
) -> None:
    """写入一条观测（同 indicator/date/source 覆盖）。"""
    if isinstance(obs_date, date):
        obs_date = obs_date.isoformat()
    obs_date = obs_date or date.today().isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO obs
            (indicator_id, obs_date, value, value_text, unit, source,
             source_url, confidence, note, ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            indicator_id,
            obs_date,
            _as_float(value),
            value_text,
            unit,
            source,
            source_url,
            confidence,
            note,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def record_many(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    n = 0
    for row in rows:
        record_obs(conn, **row)
        n += 1
    return n


def _as_of_date(as_of: str | date | None) -> str:
    """统一的"截止日期"语义：None = 今天；用于历史复现（回测/复盘）。"""
    if as_of is None:
        return date.today().isoformat()
    if isinstance(as_of, date):
        return as_of.isoformat()
    return str(as_of)[:10]


def cap_future(day: str | date | None) -> str:
    """把未来日期压到今天。

    月频数据习惯用「月末」标注观测日，但当月还在进行中——若写成未来的月末，
    信号引擎（as_of 上界=今天）会看不到它。采集器写入前调用本函数即可。
    """
    iso = _as_of_date(day)
    today = date.today().isoformat()
    return iso if iso <= today else today


def latest(
    conn: sqlite3.Connection, indicator_id: str, as_of: str | date | None = None
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM obs WHERE indicator_id = ? AND obs_date <= ?
        ORDER BY obs_date DESC, ingested_at DESC LIMIT 1
        """,
        (indicator_id, _as_of_date(as_of)),
    ).fetchone()


def latest_map(
    conn: sqlite3.Connection, as_of: str | date | None = None
) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT o.* FROM obs o
        JOIN (
            SELECT indicator_id, MAX(obs_date) AS d FROM obs
            WHERE obs_date <= ? GROUP BY indicator_id
        ) m
          ON o.indicator_id = m.indicator_id AND o.obs_date = m.d
        """,
        (_as_of_date(as_of),),
    ).fetchall()
    return {r["indicator_id"]: r for r in rows}


def series(
    conn: sqlite3.Connection,
    indicator_id: str,
    days: int = 90,
    as_of: str | date | None = None,
) -> list[sqlite3.Row]:
    end = _as_of_date(as_of)
    since = (
        date.fromisoformat(end) - timedelta(days=days)
    ).isoformat()
    return conn.execute(
        """
        SELECT * FROM obs WHERE indicator_id = ? AND obs_date >= ? AND obs_date <= ?
        ORDER BY obs_date ASC
        """,
        (indicator_id, since, end),
    ).fetchall()


def previous(
    conn: sqlite3.Connection, indicator_id: str, as_of: str | date | None = None
) -> sqlite3.Row | None:
    rows = conn.execute(
        """
        SELECT * FROM obs WHERE indicator_id = ? AND obs_date <= ?
        ORDER BY obs_date DESC, ingested_at DESC LIMIT 2
        """,
        (indicator_id, _as_of_date(as_of)),
    ).fetchall()
    return rows[1] if len(rows) > 1 else None


def window(
    conn: sqlite3.Connection,
    indicator_id: str,
    window_days: int,
    as_of: str | date | None = None,
) -> list[sqlite3.Row]:
    return series(conn, indicator_id, days=window_days, as_of=as_of)


def all_obs(
    conn: sqlite3.Connection, indicator_id: str, source: str | None = None
) -> list[sqlite3.Row]:
    """全历史（回测用），可按 source 过滤。"""
    return conn.execute(
        """
        SELECT * FROM obs WHERE indicator_id = ? AND (? IS NULL OR source = ?)
        ORDER BY obs_date ASC
        """,
        (indicator_id, source, source),
    ).fetchall()


def obs_dates(conn: sqlite3.Connection, indicator_id: str) -> list[str]:
    """该指标全部有观测的日期（升序），回测的复现时点。"""
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT obs_date FROM obs WHERE indicator_id = ? ORDER BY obs_date",
            (indicator_id,),
        ).fetchall()
    ]


# ── announcements ──────────────────────────────────────────────────────
def upsert_announcements(conn: sqlite3.Connection, rows: Sequence[dict[str, Any]]) -> int:
    n = 0
    for row in rows:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO announcements
                (stock_code, ann_date, title, category, url, fetched_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                row.get("stock_code", ""),
                row.get("ann_date", ""),
                row.get("title", ""),
                row.get("category", ""),
                row.get("url", ""),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        n += cur.rowcount
    return n


def recent_announcements(
    conn: sqlite3.Connection, days: int = 90, stock_code: str | None = None
) -> list[sqlite3.Row]:
    since = (date.today() - timedelta(days=days)).isoformat()
    # 静态 SQL + 可选参数（避免字符串拼接）
    return conn.execute(
        """
        SELECT * FROM announcements
        WHERE ann_date >= ? AND (? IS NULL OR stock_code = ?)
        ORDER BY ann_date DESC
        """,
        (since, stock_code, stock_code),
    ).fetchall()


# ── consensus ──────────────────────────────────────────────────────────
def upsert_consensus(conn: sqlite3.Connection, rows: Sequence[dict[str, Any]]) -> int:
    n = 0
    for row in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO consensus
                (stock_code, fiscal_year, metric, broker, value, unit,
                 rating, target_price, as_of, source)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row.get("stock_code", ""),
                row.get("fiscal_year", ""),
                row.get("metric", "net_profit"),
                row.get("broker", "CONSENSUS"),
                row.get("value"),
                row.get("unit", "百万元人民币"),
                row.get("rating"),
                row.get("target_price"),
                row.get("as_of", date.today().isoformat()),
                row.get("source", "etnet"),
            ),
        )
        n += 1
    return n


def consensus_for(
    conn: sqlite3.Connection, stock_code: str, fiscal_year: str | None = None
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM consensus
        WHERE stock_code = ? AND (? IS NULL OR fiscal_year = ?)
        ORDER BY fiscal_year, broker
        """,
        (stock_code, fiscal_year, fiscal_year),
    ).fetchall()


# ── fetch_log ──────────────────────────────────────────────────────────
def log_fetch(conn: sqlite3.Connection, result: FetchResult) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log (ts, source, status, items, message, duration_ms)
        VALUES (?,?,?,?,?,?)
        """,
        (
            datetime.now().isoformat(timespec="seconds"),
            result.source,
            result.status,
            result.items,
            result.message,
            result.duration_ms,
        ),
    )


def last_fetch_log(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM fetch_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
