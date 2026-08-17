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
    print("  拉取全市场快照…", flush=True)
    conn = get_db(write=True)
    try:
        n_basic = _seed_universe(conn, universe)
        print(f"  股票列表 {n_basic} 只，回填市值前 {universe} 只日线…", flush=True)
        n_daily = _backfill_daily(conn, universe, lookback_calendar_days, workers)
    finally:
        conn.close()

    print("  拉取指数…", flush=True)

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


def _secid(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return f"1.{symbol}"
    return f"0.{symbol}"


def _backfill_one(symbol: str, start: str, end: str) -> list[tuple]:
    """Eastmoney kline via curl_cffi — akshare/requests hit a broken local proxy."""
    from nous.data.collectors.unified import _em_get

    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={_secid(symbol)}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&beg={start}&end={end}"
    )
    data = (_em_get(url, timeout=20).json() or {}).get("data") or {}
    klines = data.get("klines") or []
    out = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        dt, open_, close, high, low, vol = (
            parts[0][:10], parts[1], parts[2], parts[3], parts[4], parts[5]
        )
        amount = parts[6] if len(parts) > 6 else 0
        out.append(
            (symbol, dt, float(open_), float(high), float(low), float(close), float(vol or 0), float(amount or 0))
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
                print(
                    f"  日线 {done}/{len(symbols)} ({rate:.1f}/s) rows={total_rows}",
                    flush=True,
                )
    conn.commit()
    return total_rows
