"""Data assert — Freshness + Integrity (Qlib check_data_health subset).

Usage:
  python -m nous.data.quality.data_assert
  python -m nous.data.quality.data_assert --consumer recommend
  python -m nous.data.quality.data_assert --domain capital --json
  nous data assert
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from nous.data.quality.sla_registry import (
    ASSETS,
    CONSUMERS,
    DOMAIN_KEYS,
    Priority,
    AssetSLA,
    asset_by_key,
    expand_path,
    factor_dir,
    model_dir,
)
from nous.data.quality.trading_calendar import (
    previous_trading_day,
    trading_day_lag,
)

DB_PATH = Path.home() / "nous-data" / "screener.db"
REPORT_ROOT = Path(__file__).resolve().parents[4] / "docs" / "data" / "freshness"


@dataclass
class CheckResult:
    key: str
    label: str
    domain: str
    priority: str
    ok: bool
    track: str  # freshness | integrity | existence
    detail: str
    latest: str | None = None
    lag_trading_days: int | None = None
    soft_fail: bool = False  # P2/P3 don't fail overall for consumer gates


@dataclass
class AssertReport:
    as_of: str
    last_trade_date: str
    ok: bool  # True iff no P0 failures (and no P1 if strict)
    p0_ok: bool
    p1_ok: bool
    checks: list[CheckResult] = field(default_factory=list)
    consumer: str = "all"
    domain: str = "all"
    degraded: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "last_trade_date": self.last_trade_date,
            "ok": self.ok,
            "p0_ok": self.p0_ok,
            "p1_ok": self.p1_ok,
            "consumer": self.consumer,
            "domain": self.domain,
            "degraded": self.degraded,
            "elapsed_s": self.elapsed_s,
            "checks": [asdict(c) for c in self.checks],
        }


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"screener.db not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return bool(r)


def _date_col_for(conn: sqlite3.Connection, table: str, preferred: str) -> str | None:
    if not preferred:
        return None
    cols = {c[1] for c in conn.execute(f"PRAGMA table_info([{table}])").fetchall()}
    if preferred in cols:
        return preferred
    # screen_results legacy
    for alt in ("screen_date", "trade_date", "snapshot_date", "scan_date", "date"):
        if alt in cols:
            return alt
    return None


def _check_table(conn: sqlite3.Connection, asset: AssetSLA, last_td: str) -> CheckResult:
    label = asset.label or asset.key
    if not _table_exists(conn, asset.table):
        return CheckResult(
            asset.key, label, asset.domain, asset.priority.value,
            False, "existence", f"表不存在: {asset.table}",
        )

    if not asset.date_col:
        n = conn.execute(f"SELECT COUNT(*) FROM [{asset.table}]").fetchone()[0]
        ok = n >= (asset.min_rows or 1)
        return CheckResult(
            asset.key, label, asset.domain, asset.priority.value,
            ok, "existence", f"行数={n}", latest=None,
        )

    col = _date_col_for(conn, asset.table, asset.date_col)
    if not col:
        return CheckResult(
            asset.key, label, asset.domain, asset.priority.value,
            False, "integrity", f"无日期列({asset.date_col})",
        )

    row = conn.execute(f"SELECT MAX([{col}]) FROM [{asset.table}]").fetchone()
    latest = row[0] if row else None
    if not latest:
        return CheckResult(
            asset.key, label, asset.domain, asset.priority.value,
            False, "freshness", "无数据",
        )

    lag = trading_day_lag(latest, last_td)
    ok = lag <= asset.max_lag_trading_days
    detail = f"最新={latest}, 交易日滞后={lag} (阈值≤{asset.max_lag_trading_days})"

    if asset.min_coverage_pct is not None and ok:
        try:
            count = conn.execute(
                f"SELECT COUNT(*) FROM [{asset.table}] WHERE [{col}]=?", (latest,)
            ).fetchone()[0]
            prev = previous_trading_day(latest, n=2)
            prev_count = conn.execute(
                f"SELECT COUNT(*) FROM [{asset.table}] WHERE [{col}]=?", (prev,)
            ).fetchone()[0]
            if prev_count > 0:
                pct = 100.0 * count / prev_count
                cov_ok = pct >= asset.min_coverage_pct
                ok = ok and cov_ok
                detail += f", 覆盖={pct:.0f}%/{asset.min_coverage_pct:.0f}%"
        except Exception:
            pass

    return CheckResult(
        asset.key, label, asset.domain, asset.priority.value,
        ok, "freshness", detail, latest=str(latest), lag_trading_days=lag,
    )


def _check_factor_files(asset: AssetSLA, last_td: str) -> CheckResult:
    label = asset.label or asset.key
    fdir = factor_dir()
    if asset.key == "factors_snapshot":
        # Prefer snapshots/{date}.parquet then factors_{date}.parquet
        candidates = [
            fdir / "snapshots" / f"{last_td}.parquet",
            fdir / "snapshots" / f"factors_{last_td}.parquet",
            fdir / "snapshots" / f"a_factors_{last_td}.parquet",
        ]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            snap_dir = fdir / "snapshots"
            if snap_dir.exists():
                files = sorted(snap_dir.glob("*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
                path = files[0] if files else None
        if path is None:
            return CheckResult(
                asset.key, label, asset.domain, asset.priority.value,
                False, "freshness", f"无 dated snapshot (期望 {last_td})",
            )
        # Derive as_of from filename when possible
        stem = path.stem.replace("factors_", "").replace("a_factors_", "").replace("hk_", "")
        as_of_hint = stem if len(stem) == 10 and stem[4] == "-" else None
        r = _check_parquet(asset, path, last_td)
        if as_of_hint and r.ok:
            lag = trading_day_lag(as_of_hint, last_td)
            if lag > asset.max_lag_trading_days:
                r.ok = False
                r.detail += f"; snapshot日期={as_of_hint} 交易日滞后={lag}"
                r.lag_trading_days = lag
        return r

    path = expand_path(asset.path) if asset.path else fdir / "latest.parquet"
    if not path.exists():
        alt = fdir / "a_latest.parquet"
        path = alt if alt.exists() else path
    if not path.exists():
        return CheckResult(
            asset.key, label, asset.domain, asset.priority.value,
            False, "freshness", f"文件不存在: {path}",
        )
    return _check_parquet(asset, path, last_td)


def _check_parquet(asset: AssetSLA, path: Path, last_td: str) -> CheckResult:
    label = asset.label or asset.key
    try:
        import pandas as pd

        df = pd.read_parquet(path)
    except Exception as e:
        return CheckResult(
            asset.key, label, asset.domain, asset.priority.value,
            False, "integrity", f"读取失败: {e}",
        )

    n = len(df)
    min_rows = asset.min_rows or 500
    ok = n >= min_rows
    detail = f"{path.name} 行数={n} (阈值≥{min_rows})"

    # as_of alignment via trade_date max or meta file
    as_of = None
    meta = path.with_suffix(".meta.json")
    if not meta.exists() and path.name == "latest.parquet":
        meta = path.parent / "latest.meta.json"
    if meta.exists():
        try:
            as_of = json.loads(meta.read_text(encoding="utf-8")).get("as_of")
        except Exception:
            pass
    if as_of is None and "trade_date" in df.columns:
        try:
            as_of = str(df["trade_date"].max())[:10]
        except Exception:
            pass

    if asset.require_as_of_match and as_of:
        lag = trading_day_lag(as_of, last_td)
        detail += f", as_of={as_of}, 交易日滞后={lag}"
        if lag > asset.max_lag_trading_days:
            ok = False
    elif asset.require_as_of_match:
        # fall back to mtime age in trading days approx via calendar days / 1
        age_days = (time.time() - path.stat().st_mtime) / 86400
        detail += f", mtime年龄={age_days:.1f}d (无 as_of)"
        if age_days > max(asset.max_lag_trading_days + 2, 5):
            ok = False

    # integrity: need at least one K* column
    kcols = [c for c in df.columns if str(c).startswith("K")]
    if not kcols:
        ok = False
        detail += "; 缺 K* 因子列"
        track = "integrity"
    else:
        detail += f", K列={len(kcols)}"
        track = "freshness" if ok else "freshness"

    return CheckResult(
        asset.key, label, asset.domain, asset.priority.value,
        ok, track, detail, latest=as_of, lag_trading_days=None,
    )


def _check_models(asset: AssetSLA) -> CheckResult:
    label = asset.label or asset.key
    pattern = expand_path(asset.path_glob) if asset.path_glob else model_dir() / "lgb_*.pkl"
    parent = pattern.parent
    glob_pat = pattern.name
    files = sorted(parent.glob(glob_pat), key=lambda p: p.stat().st_mtime, reverse=True) if parent.exists() else []
    if not files:
        return CheckResult(
            asset.key, label, asset.domain, asset.priority.value,
            False, "existence", "无 lgb_*.pkl（将 DEGRADED/coarse-only）",
            soft_fail=True,
        )
    newest = files[0]
    age = (time.time() - newest.stat().st_mtime) / 86400
    max_age = asset.max_age_calendar_days or 14
    ok = age <= max_age
    return CheckResult(
        asset.key, label, asset.domain, asset.priority.value,
        ok, "freshness",
        f"{newest.name} 年龄={age:.1f}d (阈值≤{max_age}d), 共{len(files)}个",
        soft_fail=not ok,  # model miss → degraded not hard P0
    )


def _integrity_ohlcv_sample(conn: sqlite3.Connection, last_td: str) -> CheckResult:
    """Qlib-style: sample latest session for null OHLCV / extreme jumps."""
    if not _table_exists(conn, "stock_daily"):
        return CheckResult(
            "integrity_ohlcv", "日线完整性抽样", "micro", "P0",
            False, "integrity", "stock_daily 不存在",
        )
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE trade_date=?", (last_td,)
        ).fetchone()[0]
        nulls = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE trade_date=? "
            "AND (close IS NULL OR close<=0 OR open IS NULL OR high IS NULL OR low IS NULL)",
            (last_td,),
        ).fetchone()[0]
        ok = n > 0 and (nulls / max(n, 1)) < 0.02
        return CheckResult(
            "integrity_ohlcv", "日线完整性抽样", "micro", "P0",
            ok, "integrity",
            f"{last_td}: 行={n}, 无效OHLC={nulls} ({100*nulls/max(n,1):.1f}%)",
            latest=last_td,
        )
    except Exception as e:
        return CheckResult(
            "integrity_ohlcv", "日线完整性抽样", "micro", "P0",
            False, "integrity", str(e),
        )


def select_assets(
    domain: str = "all",
    consumer: str = "all",
) -> list[AssetSLA]:
    if consumer and consumer != "all":
        c = CONSUMERS.get(consumer)
        if not c:
            raise ValueError(f"未知 consumer: {consumer}. 可用: {list(CONSUMERS)}")
        keys = set(c.required) | set(c.optional)
        return [a for a in ASSETS if a.key in keys]
    keys = set(DOMAIN_KEYS.get(domain, DOMAIN_KEYS["all"]))
    return [a for a in ASSETS if a.key in keys]


def run_assert(
    domain: str = "all",
    consumer: str = "all",
    db_path: Path | None = None,
    include_integrity: bool = True,
) -> AssertReport:
    t0 = time.time()
    as_of = date.today().isoformat()
    last_td = previous_trading_day(as_of, n=1)
    conn = _connect(db_path)
    checks: list[CheckResult] = []
    degraded: list[str] = []

    optional_keys: set[str] = set()
    if consumer and consumer != "all":
        c = CONSUMERS.get(consumer)
        if c:
            optional_keys = set(c.optional)

    try:
        assets = select_assets(domain=domain, consumer=consumer)
        for asset in assets:
            if asset.kind == "table":
                r = _check_table(conn, asset, last_td)
            elif asset.key in ("factors_latest", "factors_snapshot") or asset.kind == "file":
                r = _check_factor_files(asset, last_td)
            elif asset.kind == "glob":
                r = _check_models(asset)
            else:
                continue
            # Optional contract deps → soft fail (Lean: missing subscription degrades)
            if not r.ok and asset.key in optional_keys:
                r.soft_fail = True
                degraded.append(r.key)
            if not r.ok and asset.priority in (Priority.P1,) and asset.domain == "model":
                r.soft_fail = True
                if r.key not in degraded:
                    degraded.append(r.key)
            checks.append(r)

        if include_integrity and (domain in ("all", "micro") or consumer in ("all", "recommend", "backtest")):
            checks.append(_integrity_ohlcv_sample(conn, last_td))
    finally:
        conn.close()

    p0_fail = [c for c in checks if not c.ok and c.priority == "P0" and not c.soft_fail]
    p1_fail = [c for c in checks if not c.ok and c.priority == "P1" and not c.soft_fail]
    p0_ok = len(p0_fail) == 0
    p1_ok = len(p1_fail) == 0
    if consumer in ("recommend", "trl", "all"):
        ok = p0_ok and p1_ok
    elif consumer == "backtest":
        ok = p0_ok
    else:
        ok = p0_ok

    for c in checks:
        if not c.ok and c.soft_fail and c.key not in degraded:
            degraded.append(c.key)

    return AssertReport(
        as_of=as_of,
        last_trade_date=last_td,
        ok=ok,
        p0_ok=p0_ok,
        p1_ok=p1_ok,
        checks=checks,
        consumer=consumer,
        domain=domain,
        degraded=degraded,
        elapsed_s=round(time.time() - t0, 2),
    )


def write_report(report: AssertReport, out_dir: Path | None = None) -> Path:
    out = out_dir or (REPORT_ROOT / date.today().isoformat())
    out.mkdir(parents=True, exist_ok=True)
    (out / "assert.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"# 数据鲜度断言报告 — {report.as_of}",
        "",
        f"**综合裁决：{'通过' if report.ok else '未通过'}**  ",
        f"上一交易日：`{report.last_trade_date}`  |  consumer=`{report.consumer}`  |  domain=`{report.domain}`  ",
        f"P0={'通过' if report.p0_ok else '未通过'}  P1={'通过' if report.p1_ok else '未通过'}  ",
        f"DEGRADED: {', '.join(report.degraded) if report.degraded else '无'}  ",
        f"耗时：{report.elapsed_s}s",
        "",
        "| 优先级 | 资产 | 轨道 | 裁决 | 详情 |",
        "|--------|------|------|------|------|",
    ]
    for c in report.checks:
        mark = "通过" if c.ok else ("降级" if c.soft_fail else "未通过")
        lines.append(
            f"| {c.priority} | {c.label} (`{c.key}`) | {c.track} | {mark} | {c.detail} |"
        )
    lines.extend(
        [
            "",
            "## 读数",
            "",
            "- P0 失败 → 阻断荐股/筛选/交易相关消费。",
            "- P1 失败 → 短池/ML 降级；模型缺失标记 DEGRADED。",
            "- P2 失败 → 信号可中性降级，报告标黄。",
            "",
            "对标：Qlib CalendarProvider + check_data_health；Lean Consumer Contract。",
            "",
        ]
    )
    path = out / "FRESHNESS_REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Nous data assert")
    p.add_argument("--domain", default="all", help="micro|macro|capital|factor|recommend|all")
    p.add_argument("--consumer", default="all", help="recommend|trl|review|backtest|all")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-report", action="store_true")
    p.add_argument("--db", default=None)
    args = p.parse_args(argv)

    report = run_assert(
        domain=args.domain,
        consumer=args.consumer,
        db_path=Path(args.db) if args.db else None,
    )
    if not args.no_report:
        path = write_report(report)
        if not args.json:
            print(f"[assert] report → {path}")
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        status = "PASS" if report.ok else "FAIL"
        print(f"[assert] {status} p0={report.p0_ok} p1={report.p1_ok} checks={len(report.checks)}")
        for c in report.checks:
            if not c.ok:
                print(f"  ✗ [{c.priority}] {c.label}: {c.detail}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
