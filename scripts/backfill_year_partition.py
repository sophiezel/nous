#!/usr/bin/env python3
"""安全年分表日线回补 — Hermes 拦截器 + 令牌桶 + 主备多源 + checkpoint。

硬性约束（见 docs/superpowers/specs/2026-07-17-stock-daily-history-backfill-design.md §4）:
  - 禁止裸高并发打单源；workers 默认 1、上限 2
  - 主: baostock → 备1: akshare hist → 备2: sina daily
  - 抽样交叉: 腾讯 hist_tx + reconcile_pair
  - resilient_fetch / CircuitBreaker / heartbeat / rate_limiter

用法:
  PYTHONPATH=src python scripts/backfill_year_partition.py --year 2014 --workers 1
  PYTHONPATH=src python scripts/backfill_year_partition.py --year 2025 \\
      --start 2025-01-01 --end 2025-05-18 --thin-only --workers 1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

# ── Hermes HTTP 拦截器（akshare/requests 自动限流/熔断）────────────────
_HERMES_SCRIPTS = Path.home() / ".hermes" / "scripts"
if _HERMES_SCRIPTS.is_dir():
    sys.path.insert(0, str(_HERMES_SCRIPTS))
    try:
        import hermes_http_interceptor as _hhi

        _hhi.install()
        print("[hermes] http interceptor installed", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[hermes] interceptor install skipped: {e}", flush=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nous.data.collectors import (  # noqa: E402
    CircuitBreaker,
    heartbeat,
    resilient_fetch,
)
from nous.data.collectors.rate_limiter import acquire_with_multiplier  # noqa: E402
from nous.data.storage.daily_bars import daily_table_for  # noqa: E402
from nous.core.paths import checkpoint_dir, screener_db  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_year")

DB = screener_db()
CKPT_DIR = checkpoint_dir()
COLUMNS = ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"]

# §4.3 defaults
MAX_WORKERS = 2
DEFAULT_WORKERS = 1
JITTER_OK = (0.2, 0.8)
CROSS_CHECK_EVERY = 200
COMMIT_EVERY = 20

# Dedicated breakers (baostock not in DEFAULT_BREAKERS)
_BS_CB = CircuitBreaker("baostock", failure_threshold=5, cooldown_seconds=180)
_AK_CB = CircuitBreaker("akshare", failure_threshold=5, cooldown_seconds=120)


def _install_breaker(name: str, cb: CircuitBreaker) -> None:
    from nous.data.collectors import DEFAULT_BREAKERS

    DEFAULT_BREAKERS[name] = cb


_install_breaker("baostock", _BS_CB)
_install_breaker("akshare_hist", _AK_CB)


def _ckpt_path(year: int) -> Path:
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    return CKPT_DIR / f"stock_daily_{year}.json"


def load_checkpoint(year: int) -> dict:
    p = _ckpt_path(year)
    if not p.exists():
        return {"done": [], "failed": [], "updated_at": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"done": [], "failed": [], "updated_at": None}


def save_checkpoint(year: int, local_done: set, local_failed: set) -> None:
    """Merge into on-disk checkpoint under flock (multi-process safe)."""
    import fcntl

    p = _ckpt_path(year)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.seek(0)
        raw = fh.read()
        try:
            ckpt = json.loads(raw) if raw.strip() else {"done": [], "failed": []}
        except Exception:  # noqa: BLE001
            ckpt = {"done": [], "failed": []}
        done = set(ckpt.get("done") or []) | set(local_done)
        failed = (set(ckpt.get("failed") or []) | set(local_failed)) - done
        ckpt = {
            "done": sorted(done),
            "failed": sorted(failed),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(ckpt, ensure_ascii=False, indent=2))
        fh.flush()
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _bs_code(symbol: str) -> str | None:
    if symbol.startswith(("8", "4")) or symbol.startswith("920"):
        return None  # BJ — baostock 常不支持
    if symbol.startswith("6"):
        return f"sh.{symbol}"
    return f"sz.{symbol}"


def fetch_baostock(symbol: str, start: str, end: str):
    """主源：baostock（非 HTTP，显式令牌桶）。"""
    import baostock as bs
    import pandas as pd

    code = _bs_code(symbol)
    if not code:
        raise RuntimeError("bj_skip")

    if not acquire_with_multiplier("baostock", 1, timeout=60):
        raise RuntimeError("baostock_rate_limited")

    def _do():
        # login per call is heavy; reuse module-level session via login once
        rs = bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume,amount",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="2",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if rs.error_code != "0" and not rows:
            raise RuntimeError(f"baostock:{rs.error_msg}")
        if not rows:
            return pd.DataFrame(columns=COLUMNS[1:])
        df = pd.DataFrame(
            rows, columns=["trade_date", "open", "high", "low", "close", "volume", "amount"]
        )
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["close"])

    result, status = resilient_fetch("baostock", _do, max_retries=3, base_delay=1.0)
    if not status.get("success") or result is None:
        raise RuntimeError(status.get("error") or "baostock_failed")
    return result, "baostock"


def fetch_akshare_hist(symbol: str, start: str, end: str):
    """备2：东财 hist（经 interceptor；仅在 sina 也失败时使用，单次重试）。"""
    import akshare as ak
    import pandas as pd

    if not acquire_with_multiplier("akshare", 1, timeout=60):
        raise RuntimeError("akshare_rate_limited")

    start_c = start.replace("-", "")
    end_c = end.replace("-", "")

    def _do():
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_c,
            end_date=end_c,
            adjust="qfq",
        )
        if df is None or df.empty:
            raise RuntimeError("empty_hist")
        col_map = {
            "日期": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        df["trade_date"] = df["trade_date"].astype(str).str[:10]
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            else:
                df[c] = 0.0
        return df[["trade_date", "open", "high", "low", "close", "volume", "amount"]].dropna(
            subset=["close"]
        )

    result, status = resilient_fetch("akshare_hist", _do, max_retries=1, base_delay=2.0)
    if not status.get("success") or result is None:
        raise RuntimeError(status.get("error") or "akshare_failed")
    return result, "akshare_hist"


def fetch_sina_daily(symbol: str, start: str, end: str):
    """备1：新浪日线全历史，再按区间裁剪。"""
    import akshare as ak
    import pandas as pd

    if not acquire_with_multiplier("sina", 1, timeout=60):
        raise RuntimeError("sina_rate_limited")

    prefix = "sh" if symbol.startswith("6") else ("bj" if symbol.startswith(("8", "4", "92")) else "sz")

    def _do():
        raw = ak.stock_zh_a_daily(symbol=f"{prefix}{symbol}", adjust="qfq")
        if raw is None or raw.empty:
            raise RuntimeError("empty_sina")
        df = raw.copy()
        if "date" in df.columns:
            df = df.rename(columns={"date": "trade_date"})
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c not in df.columns:
                df[c] = 0.0
            else:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df[["trade_date", "open", "high", "low", "close", "volume", "amount"]].dropna(
            subset=["close"]
        )

    result, status = resilient_fetch("sina", _do, max_retries=1, base_delay=1.0)
    if not status.get("success") or result is None:
        raise RuntimeError(status.get("error") or "sina_failed")
    return result, "sina"


def fetch_tx_sample(symbol: str, start: str, end: str):
    """抽样交叉：腾讯源。"""
    import akshare as ak
    import pandas as pd

    if not acquire_with_multiplier("tencent", 1, timeout=30):
        return None
    prefix = "sh" if symbol.startswith("6") else "sz"
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=f"{prefix}{symbol}",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return None
        if "date" in df.columns:
            df = df.rename(columns={"date": "trade_date", "close": "close"})
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df[["trade_date", "close"]].dropna()
    except Exception:  # noqa: BLE001
        return None


def fetch_symbol_multisource(symbol: str, start: str, end: str, do_cross: bool):
    """主备切换：baostock → sina → akshare hist；可选腾讯抽样交叉。"""
    errors: list[str] = []
    df = None
    source = None

    # Prefer baostock (non-HTTP, stable for year history). Fallbacks are slower / flakier.
    for fetcher in (fetch_baostock, fetch_sina_daily, fetch_akshare_hist):
        try:
            df, source = fetcher(symbol, start, end)
            if df is not None and len(df) > 0:
                break
            errors.append(f"{fetcher.__name__}:empty")
            df = None
        except Exception as e:  # noqa: BLE001
            errors.append(f"{fetcher.__name__}:{e}")
            df = None

    if df is None or df.empty:
        raise RuntimeError("; ".join(errors) or "all_sources_failed")

    cross = None
    if do_cross and source:
        tx = fetch_tx_sample(symbol, start, end)
        if tx is not None and not tx.empty:
            from nous.data.collectors.multi_source import reconcile_pair

            merged = df.merge(tx, on="trade_date", how="inner", suffixes=("", "_tx"))
            s2 = 0
            checked = 0
            for _, row in merged.head(5).iterrows():
                _, grade, _ = reconcile_pair(
                    float(row["close"]), float(row["close_tx"]), symbol, str(row["trade_date"])
                )
                checked += 1
                if grade == "S2":
                    s2 += 1
            cross = {"checked": checked, "s2": s2, "primary": source}

    return df, source, cross


def write_rows(conn: sqlite3.Connection, symbol: str, df, year: int) -> int:
    tbl = f"stock_daily_{year}"
    rows = []
    for _, r in df.iterrows():
        td = str(r["trade_date"])[:10]
        if not td.startswith(str(year)):
            continue
        rows.append(
            (
                symbol,
                td,
                float(r["open"]) if r["open"] == r["open"] else None,
                float(r["high"]) if r["high"] == r["high"] else None,
                float(r["low"]) if r["low"] == r["low"] else None,
                float(r["close"]) if r["close"] == r["close"] else None,
                float(r["volume"]) if r.get("volume") == r.get("volume") else 0.0,
                float(r["amount"]) if r.get("amount") == r.get("amount") else 0.0,
            )
        )
    if not rows:
        return 0
    conn.executemany(
        f"INSERT OR REPLACE INTO {tbl}({','.join(COLUMNS)}) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def list_symbols(
    conn: sqlite3.Connection,
    year: int,
    thin_only: bool,
    start: str,
    end: str,
    hole_fill: bool = False,
) -> list[str]:
    syms = [
        r[0]
        for r in conn.execute(
            "SELECT symbol FROM stock_basic WHERE market='a' "
            "AND symbol NOT LIKE '8%' AND symbol NOT LIKE '4%' AND symbol NOT LIKE '920%' "
            "ORDER BY symbol"
        ).fetchall()
    ]
    if hole_fill:
        # Missing entirely this year but present in ANY later year partition or hot.
        # Looking only at year+1 fails for multi-year gaps (000001/600519 missing
        # 2015–2019 while present from 2020+).
        present = {
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT symbol FROM stock_daily_{year}"
            ).fetchall()
        }
        later: set[str] = set()
        for y in range(year + 1, date.today().year + 1):
            try:
                later.update(
                    r[0]
                    for r in conn.execute(
                        f"SELECT DISTINCT symbol FROM stock_daily_{y}"
                    ).fetchall()
                )
            except sqlite3.OperationalError:
                continue
        try:
            later.update(
                r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_daily").fetchall()
            )
        except sqlite3.OperationalError:
            pass
        missing = later - present
        return [s for s in syms if s in missing]

    if not thin_only:
        return syms

    need = []
    for s in syms:
        cnt = conn.execute(
            f"SELECT COUNT(*) FROM stock_daily_{year} WHERE symbol=? AND trade_date BETWEEN ? AND ?",
            (s, start, end),
        ).fetchone()[0]
        if cnt < 40:
            need.append(s)
    return need


def run_worker(
    symbols: list[str],
    year: int,
    start: str,
    end: str,
    worker_id: int,
    enable_cross: bool = False,
) -> tuple[int, int, int]:
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        log.warning("[W%s] baostock login: %s", worker_id, lg.error_msg)

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")

    ckpt = load_checkpoint(year)
    done = set(ckpt.get("done") or [])
    failed = set(ckpt.get("failed") or [])

    ok = fail = skip = 0
    pending_commit = 0
    last_source = "-"

    for i, sym in enumerate(symbols):
        heartbeat(f"backfill_year_{year}")
        if sym in done:
            skip += 1
            continue

        do_cross = bool(enable_cross) and (i > 0) and (i % CROSS_CHECK_EVERY == 0)
        try:
            df, source, cross = fetch_symbol_multisource(sym, start, end, do_cross)
            last_source = source or "-"
            n = write_rows(conn, sym, df, year)
            if n == 0:
                failed.add(sym)
                fail += 1
            else:
                done.add(sym)
                failed.discard(sym)
                ok += 1
                pending_commit += 1
                if cross and cross.get("s2", 0) > 0:
                    log.info(
                        "[W%s] cross S2 %s primary=%s s2=%s/%s",
                        worker_id,
                        sym,
                        cross.get("primary"),
                        cross["s2"],
                        cross["checked"],
                    )
            time.sleep(random.uniform(*JITTER_OK))
        except Exception as e:  # noqa: BLE001
            failed.add(sym)
            fail += 1
            if fail % 20 == 1:
                log.warning("[W%s] fail %s: %s", worker_id, sym, e)
            time.sleep(random.uniform(1.0, 2.5))

        if pending_commit >= COMMIT_EVERY:
            conn.commit()
            save_checkpoint(year, done, failed)
            pending_commit = 0

        if (i + 1) % 50 == 0:
            conn.commit()
            save_checkpoint(year, done, failed)
            log.info(
                "[W%s] %s/%s ok=%s fail=%s skip=%s done_ckpt=%s source_last=%s",
                worker_id,
                i + 1,
                len(symbols),
                ok,
                fail,
                skip,
                len(done),
                last_source,
            )

    conn.commit()
    save_checkpoint(year, done, failed)
    conn.close()
    try:
        bs.logout()
    except Exception:  # noqa: BLE001
        pass
    return ok, fail, skip


def main() -> int:
    p = argparse.ArgumentParser(description="Safe year-partition daily backfill")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--thin-only", action="store_true")
    p.add_argument("--hole-fill", action="store_true",
                    help="Only symbols missing this year but present in any later year/hot")
    p.add_argument("--cross-check", action="store_true",
                    help="Enable sparse tencent cross-check (off by default — can hang interceptor)")
    p.add_argument("--limit", type=int, default=0, help="debug: only first N pending symbols")
    args = p.parse_args()

    year = args.year
    start = args.start or f"{year}-01-01"
    end = args.end or f"{year}-12-31"
    workers = max(1, min(int(args.workers), MAX_WORKERS))
    if args.workers > MAX_WORKERS:
        log.warning("workers capped at %s (requested %s)", MAX_WORKERS, args.workers)

    # ensure table exists
    conn = sqlite3.connect(str(DB))
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS stock_daily_{year} (
            symbol TEXT NOT NULL, trade_date DATE NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
            PRIMARY KEY (symbol, trade_date)
        )"""
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_sd_{year}_date ON stock_daily_{year}(trade_date)"
    )
    conn.commit()

    symbols = list_symbols(
        conn, year, args.thin_only, start, end, hole_fill=args.hole_fill
    )
    ckpt = load_checkpoint(year)
    done = set(ckpt.get("done") or [])
    pending = [s for s in symbols if s not in done]
    if args.limit > 0:
        pending = pending[: args.limit]
    conn.close()

    log.info(
        "year=%s %s→%s pending=%s/%s workers=%s thin_only=%s hole_fill=%s",
        year,
        start,
        end,
        len(pending),
        len(symbols),
        workers,
        args.thin_only,
        args.hole_fill,
    )
    if not pending:
        log.info("nothing to do")
        return 0

    t0 = time.time()
    enable_cross = bool(args.cross_check)
    if workers == 1:
        results = [run_worker(pending, year, start, end, 0, enable_cross)]
    else:
        import multiprocessing as mp

        chunk = (len(pending) + workers - 1) // workers
        chunks = [pending[i : i + chunk] for i in range(0, len(pending), chunk)]
        with mp.Pool(workers) as pool:
            results = pool.starmap(
                run_worker,
                [(ch, year, start, end, i, enable_cross) for i, ch in enumerate(chunks)],
            )

    ok = sum(r[0] for r in results)
    fail = sum(r[1] for r in results)
    skip = sum(r[2] for r in results)

    c = sqlite3.connect(str(DB))
    n_sym, n_row, mn, mx = c.execute(
        f"SELECT COUNT(DISTINCT symbol), COUNT(*), MIN(trade_date), MAX(trade_date) "
        f"FROM stock_daily_{year}"
    ).fetchone()
    avg = c.execute(
        f"SELECT ROUND(AVG(c),0) FROM (SELECT COUNT(*) c FROM stock_daily_{year} GROUP BY trade_date)"
    ).fetchone()[0]
    c.close()

    log.info(
        "DONE ok=%s fail=%s skip=%s | table sym=%s rows=%s %s→%s avg/day=%s | %.0fs",
        ok,
        fail,
        skip,
        n_sym,
        n_row,
        mn,
        mx,
        avg,
        time.time() - t0,
    )
    heartbeat(f"backfill_year_{year}")
    return 0 if fail == 0 or ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
