"""Point-in-Time data handler — enforces temporal integrity.

Every query automatically filters to ``as_of_date`` or earlier.
No future data can leak through this layer.

Usage:
    dh = PointInTimeDataHandler("2024-03-15")
    df = dh.get_daily("000001", days=60)      # only data ≤ 2024-03-15
    pe = dh.get_fundamental("000001")["pe"]    # last reported PE before 2024-03-15
    universe = dh.get_universe("a")            # stocks that existed on 2024-03-15
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import sqlite3


class PointInTimeDataHandler:
    """Time-gated data access. All methods only see data ≤ as_of_date."""

    def __init__(self, as_of_date: str, db_path: str = ""):
        self.as_of = as_of_date
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            from nous.data.storage import get_db
            self._conn = get_db(write=False)
        return self._conn

    # ── Daily price data ──────────────────────────────────────────────

    def _daily_rel(self, lookback_days: int = 400) -> str:
        from nous.data.storage.daily_bars import (
            approx_start_for_lookback,
            daily_relation_sql,
        )

        start = approx_start_for_lookback(self.as_of, lookback_days)
        return daily_relation_sql(start, self.as_of, conn=self.conn)

    def get_daily(self, symbol: str, days: int = 120) -> pd.DataFrame:
        """Get daily OHLCV for ``symbol``, up to ``as_of_date``."""
        rel = self._daily_rel(max(days * 2, 120))
        rows = self.conn.execute(
            f"SELECT trade_date, open, high, low, close, volume, amount "
            f"FROM {rel} WHERE symbol=? AND trade_date <= ? "
            f"ORDER BY trade_date DESC LIMIT ?",
            (symbol, self.as_of, days),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume", "amount"])
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df.sort_values("trade_date").reset_index(drop=True)

    def get_close(self, symbol: str) -> float | None:
        """Get latest close price ≤ as_of_date."""
        rel = self._daily_rel(30)
        r = self.conn.execute(
            f"SELECT close FROM {rel} WHERE symbol=? AND trade_date <= ? "
            f"ORDER BY trade_date DESC LIMIT 1",
            (symbol, self.as_of),
        ).fetchone()
        return r[0] if r else None

    def get_batch_close(self, symbols: list[str]) -> dict[str, float | None]:
        """Get latest close for multiple symbols."""
        result = {}
        for sym in symbols:
            result[sym] = self.get_close(sym)
        return result

    # ── Fundamentals (PIT) ────────────────────────────────────────────

    def get_fundamental(self, symbol: str) -> dict[str, Any]:
        """Get latest fundamental data reported ≤ as_of_date.

        This is the KEY anti-lookahead mechanism: PE from Q4 2023
        reported in March 2024 cannot be used for January 2024 decisions.
        """
        r = self.conn.execute(
            "SELECT pe, pb, roe, dividend_yield, debt_ratio, total_mv, snapshot_date "
            "FROM stock_fundamental WHERE symbol=? AND snapshot_date <= ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (symbol, self.as_of),
        ).fetchone()
        if not r:
            return {"pe": None, "pb": None, "roe": None, "dividend_yield": None,
                    "debt_ratio": None, "total_mv": None, "snapshot_date": None,
                    "available": False}
        return {
            "pe": r["pe"], "pb": r["pb"], "roe": r["roe"],
            "dividend_yield": r["dividend_yield"], "debt_ratio": r["debt_ratio"],
            "total_mv": r["total_mv"], "snapshot_date": r["snapshot_date"],
            "available": True,
        }

    def get_fundamental_batch(self, symbols: list[str]) -> dict[str, dict]:
        """Get fundamentals for multiple symbols."""
        placeholders = ",".join("?" * len(symbols))
        rows = self.conn.execute(
            f"SELECT symbol, pe, pb, roe, dividend_yield, debt_ratio, total_mv, snapshot_date "
            f"FROM stock_fundamental WHERE symbol IN ({placeholders}) AND snapshot_date <= ? "
            f"ORDER BY snapshot_date DESC",
            (*symbols, self.as_of),
        ).fetchall()

        result = {}
        for r in rows:
            sym = r["symbol"]
            if sym not in result:  # first = latest snapshot_date
                result[sym] = {
                    "pe": r["pe"], "pb": r["pb"], "roe": r["roe"],
                    "dividend_yield": r["dividend_yield"],
                    "debt_ratio": r["debt_ratio"], "total_mv": r["total_mv"],
                    "snapshot_date": r["snapshot_date"], "available": True,
                }
        for sym in symbols:
            if sym not in result:
                result[sym] = {"pe": None, "pb": None, "roe": None, "available": False}
        return result

    # ── Universe (survivorship-free) ──────────────────────────────────

    def get_universe(self, market: str = "a") -> list[str]:
        """Get stocks that existed on as_of_date (survivorship-free)."""
        from nous.data.storage.daily_bars import daily_table_for

        # Find the latest trading day ≤ as_of_date (partition-aware)
        rel = self._daily_rel(10)
        latest_day = self.conn.execute(
            f"SELECT MAX(trade_date) FROM {rel} WHERE trade_date <= ?",
            (self.as_of,),
        ).fetchone()[0]

        if not latest_day:
            return []

        tbl = daily_table_for(latest_day)
        try:
            rows = self.conn.execute(
                f"SELECT DISTINCT sd.symbol FROM {tbl} sd "
                f"JOIN stock_basic sb ON sd.symbol = sb.symbol "
                f"WHERE sd.trade_date = ? AND sb.market = ?",
                (latest_day, market),
            ).fetchall()
        except Exception:
            rows = self.conn.execute(
                f"SELECT DISTINCT sd.symbol FROM {rel} sd "
                f"JOIN stock_basic sb ON sd.symbol = sb.symbol "
                f"WHERE sd.trade_date = ? AND sb.market = ?",
                (latest_day, market),
            ).fetchall()
        symbols = [r[0] for r in rows]
        if market == "a":
            # 海鹰等 A 股策略排除北交所（8/4/920 开头），避免缺价连环强平污染净值
            symbols = [
                s for s in symbols
                if not (s.startswith(("8", "4")) or s.startswith("920"))
            ]
        return symbols

    def get_universe_count(self, market: str = "a") -> int:
        """Count stocks in universe at as_of_date."""
        return len(self.get_universe(market))

    # ── Index data ────────────────────────────────────────────────────

    def get_index_daily(self, index_code: str, days: int = 120) -> pd.DataFrame:
        """Get index daily data ≤ as_of_date."""
        rows = self.conn.execute(
            "SELECT trade_date, open, high, low, close, volume "
            "FROM index_daily WHERE symbol=? AND trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT ?",
            (index_code, self.as_of, days),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
        return df.sort_values("trade_date").reset_index(drop=True)

    # ── HSGT flow ─────────────────────────────────────────────────────

    def get_hsgt_flow(self, days: int = 20) -> pd.DataFrame:
        """Get northbound/southbound flow ≤ as_of_date."""
        rows = self.conn.execute(
            "SELECT trade_date, direction, net_flow FROM hsgt_market_daily "
            "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            (self.as_of, days * 2),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=["trade_date", "direction", "net_flow"])

    # ── Margin data ───────────────────────────────────────────────────

    def get_margin(self, days: int = 10) -> pd.DataFrame:
        """Get margin trading data ≤ as_of_date."""
        rows = self.conn.execute(
            "SELECT trade_date, SUM(margin_balance) as total "
            "FROM margin_daily WHERE trade_date <= ? "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?",
            (self.as_of, days),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=["trade_date", "total_margin"])

    # ── Trading days ──────────────────────────────────────────────────

    def get_trading_days(self, start: str | None = None, end: str | None = None) -> list[str]:
        """Get trading days between start and end (or up to as_of_date)."""
        from nous.data.storage.daily_bars import daily_relation_sql

        s = start or "2015-01-01"
        e = end or self.as_of
        rel = daily_relation_sql(s, e, conn=self.conn)
        rows = self.conn.execute(
            f"SELECT DISTINCT trade_date FROM {rel} "
            f"WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (s, e),
        ).fetchall()
        return [r[0] for r in rows]

    def nth_previous_trading_day(self, n: int) -> str | None:
        """Get the nth previous trading day from as_of_date."""
        rel = self._daily_rel(max(n * 3, 30))
        rows = self.conn.execute(
            f"SELECT DISTINCT trade_date FROM {rel} "
            f"WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            (self.as_of, n + 1),
        ).fetchall()
        return rows[-1][0] if len(rows) > n else None

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
