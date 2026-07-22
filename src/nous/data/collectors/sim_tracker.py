"""sim_tracker — 09:25 运行，检测新入池/出池标的

对比今日 screen_results 与 sim_position 持仓，检测：
- 新入池: screen_results 有但 sim_position 没有 → 待买入
- 出池: screen_results 没有但 sim_position 有 → 待卖出
- 维持在池: 两者都有 → 继续持有
"""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

DB_PATH = Path("~/code/stock-screener/data/screener.db")

DDL_TRADES = """
CREATE TABLE IF NOT EXISTS sim_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    name        TEXT,
    action      TEXT NOT NULL CHECK(action IN ('buy','sell')),
    trade_time  TEXT NOT NULL,
    slot        INTEGER DEFAULT 0,
    shares      INTEGER DEFAULT 0,
    price       REAL DEFAULT 0,
    amount      REAL DEFAULT 0,
    pnl_pct     REAL,
    pnl_amount  REAL
);
"""

DDL_POSITION = """
CREATE TABLE IF NOT EXISTS sim_position (
    symbol      TEXT NOT NULL,
    slot        INTEGER NOT NULL DEFAULT 0,
    shares      INTEGER DEFAULT 0,
    entry_price REAL DEFAULT 0,
    entry_date  TEXT,
    PRIMARY KEY (symbol, slot)
);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL_TRADES)
    conn.executescript(DDL_POSITION)
    return conn


def _get_today_screen_symbols(conn: sqlite3.Connection) -> set[str]:
    """获取当日筛选结果中的所有代码"""
    row = conn.execute("SELECT MAX(screen_date) as dt FROM screen_results").fetchone()
    if not row or not row["dt"]:
        return set()
    rows = conn.execute(
        "SELECT symbol FROM screen_results WHERE screen_date=?",
        (row["dt"],),
    ).fetchall()
    return {r["symbol"] for r in rows}


def _get_position_symbols(conn: sqlite3.Connection) -> set[str]:
    """获取当前持仓中的所有代码"""
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM sim_position WHERE shares > 0"
    ).fetchall()
    return {r["symbol"] for r in rows}


def _get_position_details(conn: sqlite3.Connection, symbols: set[str]) -> list[dict]:
    """获取持仓详情"""
    if not symbols:
        return []
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"SELECT * FROM sim_position WHERE symbol IN ({placeholders}) AND shares > 0",
        list(symbols),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_name(conn: sqlite3.Connection, symbol: str) -> str:
    """从 screen_results 或 stock_basic 获取股票名称"""
    row = conn.execute(
        "SELECT name FROM stock_basic WHERE symbol=?", (symbol,)
    ).fetchone()
    if row and row["name"]:
        return row["name"]
    return symbol


def track_pool_changes() -> dict:
    """跟踪池变化
    
    Returns:
        {
            "new_entries": [{"symbol": "...", "name": "..."}, ...],
            "removed": [{"symbol": "...", "name": "...", "entry_price": ..., "shares": ...}, ...],
            "continued": [{"symbol": "...", "name": "..."}, ...],
        }
    """
    today = date.today()
    print("=" * 50)
    print(f"sim_tracker — {today}")
    print("=" * 50)
    
    conn = get_db()
    try:
        today_symbols = _get_today_screen_symbols(conn)
        position_symbols = _get_position_symbols(conn)
        
        print(f"  [sim_tracker] 今日筛选结果: {len(today_symbols)} 只")
        print(f"  [sim_tracker] 当前持仓: {len(position_symbols)} 只")
        
        # 新入池: screen 有但持仓没有
        new_entries = []
        for sym in sorted(today_symbols - position_symbols):
            name = _get_name(conn, sym)
            new_entries.append({"symbol": sym, "name": name})
        
        # 出池: 持仓有但 screen 没有
        removed = []
        removed_details = _get_position_details(conn, position_symbols - today_symbols)
        for pos in removed_details:
            name = _get_name(conn, pos["symbol"])
            removed.append({
                "symbol": pos["symbol"],
                "name": name,
                "entry_price": pos["entry_price"],
                "shares": pos["shares"],
                "slot": pos["slot"],
            })
        
        # 维持: 两者都有
        continued = []
        for sym in sorted(today_symbols & position_symbols):
            name = _get_name(conn, sym)
            continued.append({"symbol": sym, "name": name})
        
        print(f"\n  [sim_tracker] 新入池: {len(new_entries)} 只")
        for e in new_entries:
            print(f"    + {e['symbol']} {e['name']}")
        
        print(f"  [sim_tracker] 出池: {len(removed)} 只")
        for r in removed:
            print(f"    - {r['symbol']} {r['name']} (成本 {r['entry_price']}, {r['shares']} 股)")
        
        print(f"  [sim_tracker] 维持: {len(continued)} 只")
        for c in continued[:5]:
            print(f"    = {c['symbol']} {c['name']}")
        if len(continued) > 5:
            print(f"    ... 共 {len(continued)} 只")
        
        return {
            "new_entries": new_entries,
            "removed": removed,
            "continued": continued,
        }
    finally:
        conn.close()


def main():
    """独立运行入口"""
    result = track_pool_changes()
    print(f"\nsim_tracker 完成")
    print(f"  新入池: {len(result['new_entries'])}")
    print(f"  出池: {len(result['removed'])}")
    print(f"  维持: {len(result['continued'])}")


if __name__ == "__main__":
    main()
