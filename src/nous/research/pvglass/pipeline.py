"""周度例行流水线 — 采集 → 基线 → 信号 → 周报。

一个实现，两个入口：
    nous pv weekly            （人工/手动）
    scheduler/jobs/research/pvglass_weekly.py （调度器）
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from nous.core.db import get_db
from nous.core.notify import NotifyResult, deliverable_channels, send
from nous.core.paths import repo_root
from nous.research.pvglass import digest as digest_mod
from nous.research.pvglass import signals as sig_engine
from nous.research.pvglass import store
from nous.research.pvglass.registry import Registry, load_registry
from nous.research.pvglass.sources import DEFAULT_ORDER, collect_one

DEFAULT_SEED_FILE = "config/pvglass_seed.yaml"


@dataclass
class WeeklyOutcome:
    ran_at: str
    fetches: list[store.FetchResult] = field(default_factory=list)
    seed_observations: int = 0
    seed_events: int = 0
    verdict_level: str = ""
    verdict_text: str = ""
    signal_rows: list[dict[str, Any]] = field(default_factory=list)
    report_path: Path | None = None
    notifications: list[NotifyResult] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [f"光伏玻璃周度跟踪 {self.ran_at[:10]}"]
        ok = [f for f in self.fetches if f.status == "ok"]
        bad = [f for f in self.fetches if f.status not in ("ok", "skipped")]
        lines.append(f"采集: {len(ok)}/{len(self.fetches)} 正常")
        for f in bad:
            lines.append(f"  ! {f.source} {f.status}: {f.message[:60]}")
        if self.seed_observations or self.seed_events:
            lines.append(f"基线: {self.seed_observations} 观测 / {self.seed_events} 事件")
        lines.append(f"信号: {self.verdict_level.upper()} — {self.verdict_text}")
        for row in self.signal_rows:
            lines.append(f"  {row['id']} {row['name']}: {row['status']} ({row['passed']}/{row['total']})")
        if self.report_path:
            lines.append(f"周报: {self.report_path}")
        for note in self.notifications:
            lines.append(f"推送 {note.channel}: {note.status} {note.detail}"[:120])
        return lines

    # ── 推送内容（markdown，手机端友好）──────────────────────────
    _ICON = {
        "triggered": "✅",
        "not_triggered": "❌",
        "pending": "🟡",
        "insufficient_data": "⚪",
    }

    def push_title(self) -> str:
        return f"光伏玻璃周度跟踪 {self.ran_at[:10]}"

    def push_body(self) -> str:
        bad = [f for f in self.fetches if f.status not in ("ok", "skipped")]
        lines = [
            f"**{self.verdict_level.upper()}** — {self.verdict_text}",
            "",
        ]
        for row in self.signal_rows:
            icon = self._ICON.get(row["status"], "?")
            lines.append(f"{icon} {row['id']} {row['name']} {row['passed']}/{row['total']}")
        lines.append("")
        lines.append(f"采集 {len(self.fetches) - len(bad)}/{len(self.fetches)} 正常")
        for f in bad:
            lines.append(f"❗{f.source} {f.status}: {f.message[:50]}")
        if self.report_path:
            lines.append(f"\n报告: {self.report_path}")
        return "\n".join(lines)


def import_seed(
    conn: sqlite3.Connection,
    registry: Registry,
    path: str | Path | None = None,
    *,
    prune: bool = True,
) -> tuple[int, int, list[str]]:
    """导入事实基线。返回 (观测数, 事件数, 警告列表)。

    幂等语义：prune=True 时**先清空全部 source='seed' 的行**再写入——
    即“seed 文件是 seed 层的唯一真相”。这样在 YAML 里删掉/修正一条后重跑
    就能同步（否则删掉的旧值会残留在库里）；自动采集的行不受影响。
    多文件分片维护时可传 prune=False。
    """
    seed_path = Path(path).expanduser() if path else repo_root() / DEFAULT_SEED_FILE
    warnings: list[str] = []
    if not seed_path.exists():
        return 0, 0, [f"基线文件不存在: {seed_path}"]
    with seed_path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    observations = list(doc.get("observations") or [])
    events = list(doc.get("events") or [])

    if prune:
        conn.execute("DELETE FROM obs WHERE source = 'seed'")

    n_obs = n_evt = 0
    for row in observations:
        iid = str(row.get("indicator"))
        if iid not in registry.indicators:
            warnings.append(f"跳过未知指标 {iid}")
            continue
        ind = registry.indicators[iid]
        store.record_obs(
            conn,
            iid,
            str(row.get("date", "")),
            row.get("value"),
            unit=ind.unit,
            source="seed",
            source_url=str(row.get("url", "")),
            confidence="curated",
            note=str(row.get("note", "")),
        )
        n_obs += 1
    for row in events:
        iid = str(row.get("indicator"))
        if iid not in registry.indicators:
            warnings.append(f"跳过未知事件指标 {iid}")
            continue
        store.record_obs(
            conn,
            iid,
            str(row.get("date", "")),
            None,
            source="seed",
            source_url=str(row.get("url", "")),
            confidence="event",
            value_text=str(row.get("text", "")),
            note="研究基线事件",
        )
        n_evt += 1
    conn.commit()
    return n_obs, n_evt, warnings


def run_weekly(
    *,
    days: int = 180,
    sources: list[str] | None = None,
    registry: Registry | None = None,
    seed_path: str | Path | None = None,
    out: str | Path | None = None,
    model_path: str | Path | None = None,
    skip_fetch: bool = False,
    skip_seed: bool = False,
    skip_digest: bool = False,
    push: bool | None = None,
    push_channels: list[str] | None = None,
) -> WeeklyOutcome:
    """完整跑一轮周度跟踪。任何单源失败都不会中断流水线。

    push=None（默认）: 仅在配置了**真实通道**（非 stdout）时自动推送；
    push=True 强制推送（此时 stdout 也会输出），push=False 关闭。
    """
    reg = registry or load_registry()
    outcome = WeeklyOutcome(ran_at=datetime.now().isoformat(timespec="seconds"))

    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        if not skip_fetch:
            for sid in sources or list(DEFAULT_ORDER):
                result = collect_one(sid, conn, reg, days=days)
                store.log_fetch(conn, result)
                conn.commit()
                outcome.fetches.append(result)

        if not skip_seed:
            n_obs, n_evt, warnings = import_seed(conn, reg, seed_path)
            outcome.seed_observations = n_obs
            outcome.seed_events = n_evt
            if warnings:
                outcome.fetches.append(
                    store.FetchResult("seed", "partial", 0, "; ".join(warnings[:3]), 0)
                )

        results = sig_engine.evaluate(conn, reg)
        outcome.verdict_level, outcome.verdict_text = sig_engine.verdict(results)
        outcome.signal_rows = [
            {
                "id": r.signal.id,
                "name": r.signal.name,
                "status": r.status,
                "passed": r.passed,
                "total": len(r.conditions),
            }
            for r in results
        ]

        if not skip_digest:
            outcome.report_path = digest_mod.write_report(
                conn, reg, out=out, days=days, model_path=model_path
            )

    should_push = bool(deliverable_channels()) if push is None else push
    if should_push:
        outcome.notifications = send(
            outcome.push_title(), outcome.push_body(), channels=push_channels
        )

    return outcome
