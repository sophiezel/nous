"""Provider DAG orchestrator — Update → Assert → Consume.

Stages:
  S1 features  — collect all feature producers + cross/gap
  S2 factors   — daily factor incremental update
  S3 assert    — data_assert --consumer recommend (hard gate)
  S4 consume   — recommend (+ review)
  S5 observe   — health dashboard / quality report

  S0 morning   — assert → remediate remediable failures once → re-assert

Usage:
  python -m nous.data.quality.pipeline_dag post-close
  python -m nous.data.quality.pipeline_dag morning
  python -m nous.data.quality.pipeline_dag --stage S2
  nous data chain run
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("nous.pipeline_dag")

STATUS_PATH = Path.home() / "nous-data" / "logs" / "chain_status.json"
REPORT_ROOT = Path(__file__).resolve().parents[4] / "docs" / "data" / "freshness"


@dataclass
class StageResult:
    name: str
    ok: bool
    message: str = ""
    elapsed_s: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChainResult:
    chain: str
    as_of: str
    ok: bool
    stages: list[StageResult] = field(default_factory=list)
    elapsed_s: float = 0.0
    stopped_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "chain": self.chain,
            "as_of": self.as_of,
            "ok": self.ok,
            "elapsed_s": self.elapsed_s,
            "stopped_at": self.stopped_at,
            "stages": [asdict(s) for s in self.stages],
        }


def _write_status(result: ChainResult) -> Path:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # also archive under freshness docs
    day = date.today().isoformat()
    out = REPORT_ROOT / day
    out.mkdir(parents=True, exist_ok=True)
    archive = out / "chain_status.json"
    archive.write_text(STATUS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return STATUS_PATH


def read_status() -> dict | None:
    if not STATUS_PATH.exists():
        return None
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_stage(name: str, fn) -> StageResult:
    t0 = time.time()
    logger.info("=== %s start ===", name)
    try:
        status = fn()
        if isinstance(status, StageResult):
            status.elapsed_s = status.elapsed_s or round(time.time() - t0, 2)
            logger.info("=== %s %s (%.1fs) ===", name, "OK" if status.ok else "FAIL", status.elapsed_s)
            return status
        ok = bool(status.get("ok", False)) if isinstance(status, dict) else bool(status)
        msg = ""
        details: dict = {}
        if isinstance(status, dict):
            msg = str(status.get("message", ""))
            details = {k: v for k, v in status.items() if k not in ("ok", "message")}
        elapsed = round(time.time() - t0, 2)
        logger.info("=== %s %s (%.1fs) %s ===", name, "OK" if ok else "FAIL", elapsed, msg)
        return StageResult(name=name, ok=ok, message=msg, elapsed_s=elapsed, details=details)
    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        logger.exception("%s crashed", name)
        return StageResult(name=name, ok=False, message=f"{type(e).__name__}: {e}", elapsed_s=elapsed)


def stage_s1_features() -> dict:
    from nous.data.quality.producers import (
        produce_cross_validate,
        produce_features_batch,
        produce_gap_detect,
    )

    batch = produce_features_batch()
    cross = produce_cross_validate()
    gap = produce_gap_detect()
    ok = bool(batch.get("ok"))
    # cross/gap soft — assert is hard gate
    return {
        "ok": ok,
        "message": batch.get("message", ""),
        "batch": batch,
        "cross": cross,
        "gap": gap,
    }


def stage_s2_factors() -> dict:
    from nous.data.quality.producers import produce_factors_daily
    return produce_factors_daily()


def stage_s3_assert(consumer: str = "recommend") -> dict:
    from nous.data.quality.data_assert import run_assert, write_report

    report = run_assert(consumer=consumer, include_integrity=True)
    path = write_report(report)
    return {
        "ok": bool(report.p0_ok),
        "message": f"P0={'ok' if report.p0_ok else 'fail'} P1={'ok' if report.p1_ok else 'fail'}",
        "p0_ok": report.p0_ok,
        "p1_ok": report.p1_ok,
        "degraded": report.degraded,
        "report": str(path),
        "failed": [
            {"key": c.key, "priority": c.priority, "detail": c.detail}
            for c in report.checks
            if not c.ok
        ],
    }


def stage_s4_consume() -> dict:
    from nous.data.quality.producers import produce_recommend, produce_review

    rec = produce_recommend()
    rev = produce_review()
    ok = bool(rec.get("ok"))
    return {
        "ok": ok,
        "message": f"recommend={'ok' if rec.get('ok') else 'fail'}; review={'ok' if rev.get('ok') else 'fail'}",
        "recommend": rec,
        "review": rev,
    }


def stage_s5_observe() -> dict:
    from nous.data.quality.producers import produce_health_dashboard, produce_quality_report

    health = produce_health_dashboard()
    quality = produce_quality_report()
    return {
        "ok": True,  # observe never blocks chain success if S3/S4 passed
        "message": "observe done",
        "health": health,
        "quality": quality,
    }


def _remediate_from_assert(assert_details: dict) -> dict:
    """Reproduce remediable failed assets once."""
    from nous.data.quality.producers import PRODUCER_FNS
    from nous.data.quality.sla_registry import asset_by_key

    failed = assert_details.get("failed") or []
    ran = []
    seen = set()
    for item in failed:
        key = item.get("key")
        if not key or key in seen:
            continue
        asset = asset_by_key(key)
        if asset is None or not asset.remediable:
            continue
        fn = PRODUCER_FNS.get(key)
        if fn is None:
            continue
        seen.add(key)
        # factors_latest + factors_snapshot share one producer
        if key in ("factors_latest", "factors_snapshot"):
            if "factors" in seen:
                continue
            seen.add("factors")
        logger.info("remediate %s ...", key)
        status = fn()
        ran.append({"key": key, **status})
    return {"ok": all(r.get("ok") for r in ran) if ran else False, "remediated": ran}


STAGES = {
    "S1": ("features", stage_s1_features),
    "S2": ("factors", stage_s2_factors),
    "S3": ("assert", stage_s3_assert),
    "S4": ("consume", stage_s4_consume),
    "S5": ("observe", stage_s5_observe),
}


def run_post_close(
    start_from: str = "S1",
    stop_after: Optional[str] = None,
) -> ChainResult:
    """S1→S5. Hard-stop if S3 P0 fails."""
    t0 = time.time()
    order = ["S1", "S2", "S3", "S4", "S5"]
    if start_from in order:
        order = order[order.index(start_from):]
    if stop_after in order:
        order = order[: order.index(stop_after) + 1]

    result = ChainResult(chain="post-close", as_of=date.today().isoformat(), ok=True)
    for sid in order:
        _label, fn = STAGES[sid]
        sr = _run_stage(sid, fn)
        result.stages.append(sr)
        if sid == "S3" and not sr.ok:
            result.ok = False
            result.stopped_at = sid
            break
        if sid == "S1" and not sr.ok:
            # features soft-fail: continue to factors; assert decides
            logger.warning("S1 features partial failure — continue to S2/S3")
        if sid == "S2" and not sr.ok:
            result.ok = False
            result.stopped_at = sid
            break
        if sid == "S4" and not sr.ok:
            result.ok = False
            result.stopped_at = sid
            break
    result.elapsed_s = round(time.time() - t0, 2)
    if result.stopped_at is None:
        result.ok = all(s.ok for s in result.stages if s.name in ("S2", "S3", "S4"))
    _write_status(result)
    return result


def run_morning() -> ChainResult:
    """S0: assert → remediate once → re-assert."""
    t0 = time.time()
    result = ChainResult(chain="morning", as_of=date.today().isoformat(), ok=True)

    s3 = _run_stage("S0a-assert", lambda: stage_s3_assert("recommend"))
    result.stages.append(s3)
    if s3.ok:
        result.elapsed_s = round(time.time() - t0, 2)
        _write_status(result)
        return result

    rem = _run_stage("S0b-remediate", lambda: _remediate_from_assert(s3.details))
    result.stages.append(rem)

    s3b = _run_stage("S0c-assert", lambda: stage_s3_assert("recommend"))
    result.stages.append(s3b)
    result.ok = s3b.ok
    if not result.ok:
        result.stopped_at = "S0c-assert"
    result.elapsed_s = round(time.time() - t0, 2)
    _write_status(result)
    return result


def run_stage(stage_id: str) -> ChainResult:
    stage_id = stage_id.upper()
    if stage_id not in STAGES:
        raise ValueError(f"unknown stage {stage_id}; expected {list(STAGES)}")
    t0 = time.time()
    _label, fn = STAGES[stage_id]
    sr = _run_stage(stage_id, fn)
    result = ChainResult(
        chain=f"stage-{stage_id}",
        as_of=date.today().isoformat(),
        ok=sr.ok,
        stages=[sr],
        elapsed_s=round(time.time() - t0, 2),
        stopped_at=None if sr.ok else stage_id,
    )
    _write_status(result)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    p = argparse.ArgumentParser(description="Nous Provider DAG")
    p.add_argument(
        "action",
        nargs="?",
        default="post-close",
        choices=["post-close", "morning", "status", "S1", "S2", "S3", "S4", "S5"],
    )
    p.add_argument("--from", dest="start_from", default="S1", help="post-close start stage")
    p.add_argument("--until", dest="stop_after", default=None, help="post-close stop stage")
    args = p.parse_args(argv)

    if args.action == "status":
        st = read_status()
        print(json.dumps(st or {"ok": False, "message": "no chain_status.json"}, ensure_ascii=False, indent=2))
        return 0 if (st or {}).get("ok") else 1

    if args.action == "morning":
        result = run_morning()
    elif args.action == "post-close":
        result = run_post_close(start_from=args.start_from, stop_after=args.stop_after)
    else:
        result = run_stage(args.action)

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
