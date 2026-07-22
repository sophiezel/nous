"""DuckDB 查询抽象层 — ATTACH SQLite 直接读取日线数据

核心函数:
- get_daily_df(symbol, days=120) → pd.DataFrame  — 替代 storage.get_daily()
- get_multi_daily_df(symbols, days=120) → pd.DataFrame — 批量查询
- init_engine() → duckdb.DuckDBPyConnection — 初始化全局连接(单例模式)

实现: DuckDB ATTACH 'screener.db' AS hot (TYPE SQLITE, READ_ONLY)
连接复用: 全局单例 duckdb.connect(), 不要每次查询新建连接
"""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── 路径 ──────────────────────────────────────────────
DB_DIR = Path(__file__).resolve().parents[3] / "data"
SQLITE_PATH = DB_DIR / "screener.db"
ANALYTICS_PATH = DB_DIR / "analytics.db"

# ── 全局单例 ──────────────────────────────────────────
_conn: Optional["duckdb.DuckDBPyConnection"] = None  # type: ignore[name-defined]


def init_engine() -> "duckdb.DuckDBPyConnection":
    """初始化全局 DuckDB 连接（单例），ATTACH SQLite 为只读。

    返回: duckdb.DuckDBPyConnection
    """
    global _conn
    if _conn is not None:
        return _conn

    import duckdb

    # 连接到 analytics.db（保留已有的分析库）
    ANALYTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = duckdb.connect(str(ANALYTICS_PATH))

    # ATTACH SQLite 只读
    _conn.execute(
        f"ATTACH IF NOT EXISTS '{SQLITE_PATH}' AS hot (TYPE SQLITE, READ_ONLY)"
    )
    logger.info(
        "DuckDB engine initialised: attached SQLite at %s", SQLITE_PATH
    )
    return _conn


def close_engine() -> None:
    """关闭全局 DuckDB 连接"""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


def get_conn() -> "duckdb.DuckDBPyConnection":
    """获取或初始化全局连接"""
    if _conn is None:
        return init_engine()
    return _conn


# ── 日线查询 ──────────────────────────────────────────


def get_daily_df(symbol: str, days: int = 120) -> pd.DataFrame:
    """获取单只股票日线 DataFrame，按 trade_date 升序。

    Args:
        symbol: 股票代码 (如 '600519')
        days:   返回最近 N 条日线

    Returns:
        pd.DataFrame with columns: symbol, trade_date, open, high, low,
        close, volume, amount (与 storage.get_daily() 返回一致)
        空 DataFrame 若无数据
    """
    conn = get_conn()
    try:
        df = conn.execute(
            """
            SELECT symbol, trade_date, open, high, low, close, volume, amount
            FROM hot.stock_daily
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            [symbol, days],
        ).fetchdf()
    except Exception as e:
        logger.warning("DuckDB query failed for %s: %s", symbol, e)
        return pd.DataFrame()

    if df.empty:
        return df

    # 转升序
    df = df.sort_values("trade_date").reset_index(drop=True)
    # 类型保证
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def get_multi_daily_df(
    symbols: list[str], days: int = 120
) -> pd.DataFrame:
    """批量获取多只股票日线 DataFrame，按 (symbol, trade_date) 升序。

    Args:
        symbols: 股票代码列表
        days:    每只股票返回最近 N 条日线

    Returns:
        pd.DataFrame with columns: symbol, trade_date, open, high, low,
        close, volume, amount
    """
    if not symbols:
        return pd.DataFrame()

    conn = get_conn()
    placeholders = ",".join(["?"] * len(symbols))

    try:
        # DuckDB 的 QUALIFY + ROW_NUMBER 做每组 TOP N
        df = conn.execute(
            f"""
            SELECT symbol, trade_date, open, high, low, close, volume, amount
            FROM (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol ORDER BY trade_date DESC
                    ) AS rn
                FROM hot.stock_daily
                WHERE symbol IN ({placeholders})
            ) sub
            WHERE rn <= ?
            ORDER BY symbol, trade_date ASC
            """,
            [*symbols, days],
        ).fetchdf()
    except Exception as e:
        logger.warning(
            "DuckDB multi-query failed (%d symbols): %s", len(symbols), e
        )
        return pd.DataFrame()

    if df.empty:
        return df

    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def get_fundamentals_df(symbol: str) -> Optional[dict]:
    """获取单只股票基本面快照（兼容 value.py）"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM hot.stock_fundamental WHERE symbol = ?",
            [symbol],
        ).fetchone()
    except Exception as e:
        logger.warning("Fundamental query failed for %s: %s", symbol, e)
        return None

    if row is None:
        return None

    # duckdb row → dict
    desc = [d[0] for d in conn.description]
    return dict(zip(desc, row))


def get_basic_info(symbol: str) -> Optional[dict]:
    """获取股票基本信息"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT symbol, name, market FROM hot.stock_basic WHERE symbol = ?",
            [symbol],
        ).fetchone()
    except Exception as e:
        logger.warning("Basic info query failed for %s: %s", symbol, e)
        return None

    if row is None:
        return None
    desc = [d[0] for d in conn.description]
    return dict(zip(desc, row))
