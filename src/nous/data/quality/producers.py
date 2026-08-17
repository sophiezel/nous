"""Asset producers — callable units referenced by sla_registry PRODUCERS.

Each producer returns a small status dict: {ok, message, ...}.
Failures are soft at P1/P2 collection level; DAG decides hard-stop on assert.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger("nous.producers")

Status = dict[str, Any]


def _ok(msg: str = "", **extra) -> Status:
    return {"ok": True, "message": msg, **extra}


def _fail(msg: str, **extra) -> Status:
    return {"ok": False, "message": msg, **extra}


def _wrap_collect(name: str, fn: Callable[[], dict]) -> Status:
    t0 = time.time()
    try:
        r = fn()
        status = r.get("status", "ok")
        ok = status in ("ok", "skip", "empty")
        return {
            "ok": ok,
            "message": r.get("message", status),
            "count": r.get("count", 0),
            "elapsed_s": round(time.time() - t0, 2),
            "collector": name,
        }
    except Exception as e:
        logger.exception("producer %s failed", name)
        return _fail(f"{type(e).__name__}: {e}", collector=name, elapsed_s=round(time.time() - t0, 2))


def produce_stock_daily() -> Status:
    from nous.data.collectors.unified import collect_stock_daily
    return _wrap_collect("daily", collect_stock_daily)


def produce_fundamentals() -> Status:
    from nous.data.collectors.unified import collect_fundamentals
    return _wrap_collect("fundamental", collect_fundamentals)


def produce_index_daily() -> Status:
    from nous.data.collectors.unified import collect_index_daily
    return _wrap_collect("index", collect_index_daily)


def produce_global_index() -> Status:
    from nous.data.collectors.unified import collect_global_index
    return _wrap_collect("global-index", collect_global_index)


def produce_futures() -> Status:
    from nous.data.collectors.unified import collect_futures
    return _wrap_collect("futures", collect_futures)


def produce_futures_basis() -> Status:
    t0 = time.time()
    try:
        from nous.data.collectors.futures_basis import collect
        ok = bool(collect())
        # empty on holiday is acceptable for P2
        return _ok("basis collected" if ok else "no basis rows", elapsed_s=round(time.time() - t0, 2))
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}", elapsed_s=round(time.time() - t0, 2))


def produce_sentiment() -> Status:
    from nous.data.collectors.unified import collect_sentiment
    return _wrap_collect("sentiment", collect_sentiment)


def produce_hsgt() -> Status:
    from nous.data.collectors.unified import collect_hsgt
    return _wrap_collect("hsgt", collect_hsgt)


def produce_fund_flow() -> Status:
    from nous.data.collectors.unified import collect_fund_flow
    return _wrap_collect("fund-flow", collect_fund_flow)


def produce_margin() -> Status:
    from nous.data.collectors.unified import collect_margin
    return _wrap_collect("margin", collect_margin)


def produce_etf_flow() -> Status:
    from nous.data.collectors.unified import collect_etf_flow
    return _wrap_collect("etf-flow", collect_etf_flow)


def produce_block_trade() -> Status:
    from nous.data.collectors.unified import collect_block_trade
    return _wrap_collect("block-trade", collect_block_trade)


def produce_lhb() -> Status:
    from nous.data.collectors.unified import collect_lhb
    return _wrap_collect("lhb", collect_lhb)


def produce_factors_daily() -> Status:
    """Incremental factor refresh (lookback window + merge into latest)."""
    t0 = time.time()
    try:
        from nous.engine.ml.factor_compute import daily_factor_update
        path = daily_factor_update(lookback_calendar_days=150, market="a")
        return _ok(f"saved {path}", elapsed_s=round(time.time() - t0, 2))
    except Exception as e:
        logger.exception("factor daily update failed")
        return _fail(f"{type(e).__name__}: {e}", elapsed_s=round(time.time() - t0, 2))


def produce_features_batch() -> Status:
    """Run all feature collectors once (S1)."""
    from nous.data.collectors.unified import collect_all

    t0 = time.time()
    sources = [
        "daily", "fundamental", "index", "global-index",
        "margin", "hsgt", "lhb", "fund-flow", "etf-flow",
        "futures", "sentiment", "block-trade",
    ]
    results = collect_all(sources)
    ok_n = sum(1 for r in results.values() if r.get("status") in ("ok", "skip", "empty"))
    # also try basis (separate module)
    basis = produce_futures_basis()
    results["futures-basis"] = {
        "status": "ok" if basis["ok"] else "error",
        "count": 0,
        "message": basis.get("message", ""),
    }
    if basis["ok"]:
        ok_n += 1
    total = len(results)
    return {
        "ok": ok_n >= max(1, total // 2),  # majority; assert is the hard gate
        "message": f"{ok_n}/{total} collectors ok",
        "elapsed_s": round(time.time() - t0, 2),
        "details": {k: v.get("status") for k, v in results.items()},
    }


def produce_cross_validate() -> Status:
    t0 = time.time()
    try:
        from nous.data.quality.validators import cross
        r = cross()
        ok = True
        if isinstance(r, dict) and r.get("ok") is False:
            ok = False
        return {
            "ok": ok,
            "message": "cross-validate done",
            "elapsed_s": round(time.time() - t0, 2),
            "result": r if isinstance(r, dict) else {},
        }
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}", elapsed_s=round(time.time() - t0, 2))


def produce_gap_detect() -> Status:
    t0 = time.time()
    try:
        from nous.data.quality.gap_detector import run_all
        run_all(json_output=True)
        return _ok("gap-detector done", elapsed_s=round(time.time() - t0, 2))
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}", elapsed_s=round(time.time() - t0, 2))


def produce_recommend() -> Status:
    t0 = time.time()
    try:
        from nous.engine.pipelines.daily_recommendation_pipeline import run_pipeline
        result = run_pipeline()
        ok = result.get("status") == "ok"
        return {
            "ok": ok,
            "message": "daily-recommend done" if ok else str(result.get("issues", result.get("status"))),
            "elapsed_s": round(time.time() - t0, 2),
            "result": {k: result.get(k) for k in ("status", "elapsed", "summary") if k in result},
        }
    except Exception as e:
        logger.exception("recommend failed")
        return _fail(f"{type(e).__name__}: {e}", elapsed_s=round(time.time() - t0, 2))


def produce_review() -> Status:
    t0 = time.time()
    try:
        from nous.data.storage import get_db
        from nous.engine.signals.crocodile_signals import evaluate_crocodile_signals

        conn = get_db(write=False)
        try:
            report_date = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
            result = evaluate_crocodile_signals(conn, trade_date=report_date)
        finally:
            conn.close()
        return {
            "ok": True,
            "message": f"review {report_date} score={result.get('total_score')}",
            "elapsed_s": round(time.time() - t0, 2),
            "verdict": result.get("verdict"),
        }
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}", elapsed_s=round(time.time() - t0, 2))


def produce_health_dashboard() -> Status:
    t0 = time.time()
    try:
        import subprocess, sys
        from pathlib import Path
        py = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python3"
        if not py.exists():
            py = Path(sys.executable)
        r = subprocess.run(
            [str(py), "-m", "nous.data.quality.health_dashboard", "--json"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return _fail(f"health exit={r.returncode}", elapsed_s=round(time.time() - t0, 2))
        return _ok("health-dashboard done", elapsed_s=round(time.time() - t0, 2))
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}", elapsed_s=round(time.time() - t0, 2))


def produce_quality_report() -> Status:
    t0 = time.time()
    try:
        import subprocess, sys
        from pathlib import Path
        py = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python3"
        if not py.exists():
            py = Path(sys.executable)
        r = subprocess.run(
            [str(py), "-m", "nous.data.collectors.daily_quality_report"],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode != 0:
            return _fail(f"quality-report exit={r.returncode}", elapsed_s=round(time.time() - t0, 2))
        return _ok("quality-report done", elapsed_s=round(time.time() - t0, 2))
    except Exception as e:
        return _fail(f"{type(e).__name__}: {e}", elapsed_s=round(time.time() - t0, 2))


# key → callable (used by DAG remediation + registry)
PRODUCER_FNS: dict[str, Callable[[], Status]] = {
    "stock_daily_a": produce_stock_daily,
    "stock_fundamental": produce_fundamentals,
    "index_daily": produce_index_daily,
    "index_global_daily": produce_global_index,
    "futures_daily": produce_futures,
    "futures_basis": produce_futures_basis,
    "sentiment_cache": produce_sentiment,
    "hsgt_market_daily": produce_hsgt,
    "hsgt_stock_daily": produce_hsgt,
    "fund_flow_stock": produce_fund_flow,
    "margin_daily": produce_margin,
    "etf_flow_daily": produce_etf_flow,
    "block_trades": produce_block_trade,
    "lhb_daily": produce_lhb,
    "factors_latest": produce_factors_daily,
    "factors_snapshot": produce_factors_daily,
    "screen_results": produce_recommend,
}
