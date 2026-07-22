"""幸存者偏差消除模块

从分区日线（年分表 + 热表）获取历史某日的真实可交易股票池。
不预先剔除退市/ST股票——它们在退市前的交易日仍然可交易。

核心思想:
- 用年分表/热表路由近似: 某日有数据的股票 = 该日可交易股票
- 不依赖 stock_basic 的上市/退市日期 (因为没有精确字段)
- 回测时获取整个区间的完整股票池 (含退市)
"""

import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / "nous-data" / "screener.db"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def get_tradeable_universe(trade_date: str) -> list[str]:
    """获取某交易日可交易的全部股票代码列表。"""
    from nous.data.storage.daily_bars import daily_relation_sql, daily_table_for

    conn = _conn()
    try:
        try:
            tbl = daily_table_for(trade_date)
            rows = conn.execute(
                f"SELECT DISTINCT symbol FROM {tbl} WHERE trade_date = ?",
                (trade_date,),
            ).fetchall()
        except sqlite3.OperationalError:
            rel = daily_relation_sql(trade_date, trade_date, conn=conn)
            rows = conn.execute(
                f"SELECT DISTINCT symbol FROM {rel} WHERE trade_date = ?",
                (trade_date,),
            ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_survivorship_free_universe(
    start_date: str,
    end_date: str,
    min_days: int = 1,
) -> list[str]:
    """获取回测区间内可交易的全部股票池（含期间退市的股票）。"""
    from nous.data.storage.daily_bars import daily_relation_sql

    conn = _conn()
    try:
        rel = daily_relation_sql(start_date, end_date, conn=conn)
        rows = conn.execute(
            f"""
            SELECT symbol, COUNT(DISTINCT trade_date) as cnt
            FROM {rel}
            WHERE trade_date >= ? AND trade_date <= ?
            GROUP BY symbol
            HAVING cnt >= ?
            """,
            (start_date, end_date, min_days),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_symbol_date_range(symbol: str) -> Optional[dict]:
    """获取某只股票在数据库中的日期范围。"""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT MIN(trade_date), MAX(trade_date) FROM stock_daily_all WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        if row and row[0] and row[1]:
            return {"first_date": row[0], "last_date": row[1]}
        return None
    finally:
        conn.close()


def filter_symbols_in_date_range(
    symbols: list[str], trade_date: str
) -> list[str]:
    """从符号列表中过滤出在 trade_date 有数据的股票。"""
    if not symbols:
        return []
    from nous.data.storage.daily_bars import daily_relation_sql, daily_table_for

    conn = _conn()
    try:
        placeholders = ",".join(["?"] * len(symbols))
        try:
            tbl = daily_table_for(trade_date)
            rows = conn.execute(
                f"SELECT DISTINCT symbol FROM {tbl} "
                f"WHERE trade_date = ? AND symbol IN ({placeholders})",
                (trade_date, *symbols),
            ).fetchall()
        except sqlite3.OperationalError:
            rel = daily_relation_sql(trade_date, trade_date, conn=conn)
            rows = conn.execute(
                f"SELECT DISTINCT symbol FROM {rel} "
                f"WHERE trade_date = ? AND symbol IN ({placeholders})",
                (trade_date, *symbols),
            ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()
