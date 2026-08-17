"""Cold-start data for a clean install — enough for screen / recommend.

Does not download the full 5000-name 10-year archive (hours). It:
  1. Creates schema under ~/nous-data
  2. Fills stock_basic + stock_fundamental from one Eastmoney spot pull
  3. Backfills ~1y daily bars for the most liquid names
  4. Pulls major index history
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger("nous.bootstrap")


def run_bootstrap(
    universe: int = 800,
    lookback_calendar_days: int = 400,
    workers: int = 4,
) -> dict[str, Any]:
    from nous.data.collectors.unified import _fetch_a_spot_em, collect_index_daily
    from nous.data.storage import get_db

    t0 = time.time()
    conn = get_db(write=True)
    try:
        n_basic = _seed_universe(conn, universe)
        n_daily = _backfill_daily(conn, universe, lookback_calendar_days, workers)
    finally:
        conn.close()

    idx = collect_index_daily()
    elapsed = round(time.time() - t0, 1)
    ok = n_basic > 0 and n_daily > 0
    return {
        "ok": ok,
        "stock_basic": n_basic,
        "stock_daily_rows": n_daily,
        "index": idx,
        "elapsed_s": elapsed,
        "message": (
            f"basic={n_basic} daily_rows={n_daily} "
            f"index={idx.get('status')} ({elapsed}s)"
        ),
    }


def _num(v):
    try:
        if v is None or v in ("-", ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _seed_universe(conn, universe: int) -> int:
    from nous.data.collectors.unified import _fetch_a_spot_em

    rows = _fetch_a_spot_em()
    if not rows:
        raise RuntimeError("Eastmoney spot empty — check network")

    as_of = date.today().isoformat()
    ranked: list[tuple[float, str, str, float | None, float | None, float | None]] = []
    for row in rows:
        symbol = str(row.get("f12") or "").zfill(6)
        name = str(row.get("f14") or symbol)
        if not symbol or len(symbol) > 6:
            continue
        mv = _num(row.get("f20")) or 0.0
        ranked.append(
            (mv, symbol, name, _num(row.get("f9")), _num(row.get("f23")), mv or None)
        )
    ranked.sort(key=lambda x: x[0], reverse=True)

    for _mv, symbol, name, pe, pb, mv in ranked:
        conn.execute(
            "INSERT OR REPLACE INTO stock_basic(symbol, name, market) VALUES(?,?,?)",
            (symbol, name, "a"),
        )
        conn.execute(
            """INSERT OR REPLACE INTO stock_fundamental
               (symbol, pe, pb, total_mv, pe_dynamic, snapshot_date, updated_at)
               VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (symbol, pe, pb, mv, pe, as_of),
        )
    conn.commit()
    logger.info("seeded %s A-shares (will backfill top %s by MV)", len(ranked), universe)
    return len(ranked)


def _backfill_one(symbol: str, start: str, end: str) -> list[tuple]:
    import akshare as ak
    from nous.data.collectors.unified import _clear_proxies

    _clear_proxies()
    df = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start,
        end_date=end,
        adjust="qfq",
    )
    if df is None or df.empty:
        return []
    out = []
    for _, row in df.iterrows():
        out.append(
            (
                symbol,
                str(row.get("日期") or row.get("date") or "")[:10],
                row.get("开盘", row.get("open")),
                row.get("最高", row.get("high")),
                row.get("最低", row.get("low")),
                row.get("收盘", row.get("close")),
                row.get("成交量", row.get("volume")),
                row.get("成交额", row.get("amount", 0)),
            )
        )
    return out


def _backfill_daily(conn, universe: int, lookback_calendar_days: int, workers: int) -> int:
    symbols = [
        r[0]
        for r in conn.execute(
            """SELECT sb.symbol FROM stock_basic sb
               LEFT JOIN stock_fundamental sf ON sb.symbol = sf.symbol
               WHERE sb.market='a'
               ORDER BY COALESCE(sf.total_mv, 0) DESC
               LIMIT ?""",
            (universe,),
        ).fetchall()
    ]
    end = date.today()
    start = (end - timedelta(days=lookback_calendar_days)).strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    total_rows = 0
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(_backfill_one, s, start, end_s): s for s in symbols}
        for fut in as_completed(futs):
            symbol = futs[fut]
            try:
                rows = fut.result()
            except Exception as e:
                logger.warning("daily %s failed: %s", symbol, e)
                rows = []
            if rows:
                conn.executemany(
                    """INSERT OR REPLACE INTO stock_daily
                       (symbol, trade_date, open, high, low, close, volume, amount)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    rows,
                )
                total_rows += len(rows)
            done += 1
            if done % 50 == 0:
                conn.commit()
                rate = done / max(time.time() - t0, 0.1)
                logger.info("daily %s/%s (%.1f/s) rows=%s", done, len(symbols), rate, total_rows)
    conn.commit()
    return total_rows
