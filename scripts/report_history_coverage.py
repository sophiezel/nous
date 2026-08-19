#!/usr/bin/env python3
"""History coverage report for year partitions + hot + stock_daily_all readability."""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nous.core.paths import screener_db

DB = screener_db()


def year_stats(conn: sqlite3.Connection, table: str) -> dict:
    try:
        mn, mx, n, nd = conn.execute(
            f"SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT trade_date) FROM {table}"
        ).fetchone()
    except sqlite3.OperationalError:
        return {"table": table, "exists": False}
    if not nd:
        return {
            "table": table,
            "exists": True,
            "min": mn,
            "max": mx,
            "rows": n,
            "days": 0,
            "min_syms": 0,
            "p50_syms": 0,
            "max_syms": 0,
            "thin_days_lt1000": 0,
        }
    counts = [
        r[0]
        for r in conn.execute(f"SELECT COUNT(*) FROM {table} GROUP BY trade_date").fetchall()
    ]
    thin = sum(1 for c in counts if c < 1000)
    return {
        "table": table,
        "exists": True,
        "min": mn,
        "max": mx,
        "rows": n,
        "days": nd,
        "min_syms": min(counts),
        "p50_syms": int(statistics.median(counts)),
        "max_syms": max(counts),
        "thin_days_lt1000": thin,
    }


def sample_engine_reads(conn: sqlite3.Connection) -> list[str]:
    """Verify partitioned helper / view can read historical prices."""
    lines = []
    from nous.data.storage.daily_bars import daily_relation_sql

    probes = [
        ("2014-06-30", "000001"),
        ("2018-06-29", "600519"),
        ("2022-01-04", "000001"),
        ("2025-03-03", "600519"),
        ("2026-06-30", "000001"),
    ]
    for d, sym in probes:
        rel = daily_relation_sql(d, d, conn=conn)
        row = conn.execute(
            f"SELECT close FROM {rel} WHERE symbol=? AND trade_date=?", (sym, d)
        ).fetchone()
        # also via view if present
        try:
            row2 = conn.execute(
                "SELECT close FROM stock_daily_all WHERE symbol=? AND trade_date=? LIMIT 1",
                (sym, d),
            ).fetchone()
        except sqlite3.OperationalError as e:
            row2 = None
            lines.append(f"- view read {sym}@{d}: ERROR {e}")
        ok = row is not None and row[0] is not None
        ok2 = row2 is not None and row2[0] is not None
        lines.append(
            f"- {sym}@{d}: relation={'OK '+str(row[0]) if ok else 'MISS'} | "
            f"all={'OK '+str(row2[0]) if ok2 else 'MISS'}"
        )
    return lines


def calendar_holes(conn: sqlite3.Connection, year: int) -> list[str]:
    """Trading days in index_daily missing from year partition (density>100)."""
    idx = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT trade_date FROM index_daily "
            "WHERE symbol IN ('IDX_000001','000001.SH','sh000001') "
            "AND trade_date >= ? AND trade_date <= ? ORDER BY 1",
            (f"{year}-01-01", f"{year}-12-31"),
        ).fetchall()
    ]
    if not idx:
        return []
    tbl = f"stock_daily_{year}"
    try:
        dense = {
            r[0]
            for r in conn.execute(
                f"SELECT trade_date FROM {tbl} GROUP BY trade_date HAVING COUNT(*) >= 500"
            ).fetchall()
        }
    except sqlite3.OperationalError:
        return idx[:20]
    holes = [d for d in idx if d not in dense]
    return holes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB))
    years = list(range(2014, 2027))
    stats = [year_stats(conn, f"stock_daily_{y}") for y in years]
    hot = year_stats(conn, "stock_daily")

    # view definition presence
    view = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='stock_daily_all'"
    ).fetchone()
    view_has_2014 = bool(view and "stock_daily_2014" in (view[0] or ""))

    holes = {y: calendar_holes(conn, y) for y in years}
    samples = sample_engine_reads(conn)

    # pass criteria sketch
    ok_2015_2024 = all(
        s.get("p50_syms", 0) >= 1500 for s in stats if s["table"].endswith(tuple(str(y) for y in range(2015, 2025)))
    )
    s2014 = next(s for s in stats if s["table"] == "stock_daily_2014")
    s2026 = next(s for s in stats if s["table"] == "stock_daily_2026")
    ok_2026 = (s2026.get("max") or "") >= "2026-07-01"
    ok_2014 = s2014.get("p50_syms", 0) >= 1500 and s2014.get("thin_days_lt1000", 999) < 30

    lines = [
        "# A股日线历史覆盖报告",
        "",
        f"> 生成时间：本地 DB `{DB}`",
        "",
        "## 总判",
        "",
        f"- `stock_daily_all` 含 2014：{'是' if view_has_2014 else '否'}",
        f"- 2015–2024 密度（p50≥1500）：{'达标' if ok_2015_2024 else '未达标/待查'}",
        f"- 2026 分表 max≥2026-07：{'达标' if ok_2026 else '未达标'}（max={s2026.get('max')}）",
        f"- 2014 全市场密度：{'达标' if ok_2014 else '进行中/未达标'}（p50={s2014.get('p50_syms')}, thin<{1000}天={s2014.get('thin_days_lt1000')}）",
        f"- 热表：{hot.get('min')} → {hot.get('max')} rows={hot.get('rows')}",
        "",
        "## 按年 min / p50 / max 日股票数",
        "",
        "| 表 | min日 | max日 | 交易日 | rows | min只 | p50只 | max只 | 薄日(<1000) |",
        "|----|--------|--------|--------|------|-------|-------|-------|-------------|",
    ]
    for s in [hot] + stats:
        if not s.get("exists"):
            lines.append(f"| {s['table']} | — | — | — | — | — | — | — | 缺失 |")
            continue
        lines.append(
            f"| {s['table']} | {s.get('min')} | {s.get('max')} | {s.get('days')} | {s.get('rows')} | "
            f"{s.get('min_syms')} | {s.get('p50_syms')} | {s.get('max_syms')} | {s.get('thin_days_lt1000')} |"
        )

    lines += ["", "## 相对指数日历的空洞日（日股票数<500）", ""]
    for y, hs in holes.items():
        if not hs:
            lines.append(f"- **{y}**: 无（或指数日历不可用）")
        else:
            preview = ", ".join(hs[:15])
            more = f" …共{len(hs)}天" if len(hs) > 15 else ""
            lines.append(f"- **{y}**: {preview}{more}")

    lines += ["", "## 引擎抽样读价（daily_relation + stock_daily_all）", ""] + samples

    lines += [
        "",
        "## 读路径说明",
        "",
        "- Helper: `nous.data.storage.daily_bars`（`daily_relation_sql` / `daily_table_for`）",
        "- 视图: `stock_daily_all` = 年分表 UNION + 热表尾（去重）",
        "- 回测/因子/日历关键路径已改用 helper 或 all；鲜度 assert 仍看热表",
        "",
        "## 因子重算（若未跑完）",
        "",
        "```bash",
        "cd ~/code/nous",
        "PYTHONPATH=src nohup .venv/bin/python -m nous.engine.ml.factor_compute save \\",
        "  --start 2015-01-01 --engine pandas \\",
        "  > ~/nous-data/logs/factor_recompute_2015plus.log 2>&1 &",
        "```",
        "",
        "## 回补续跑",
        "",
        "```bash",
        "PYTHONPATH=src python scripts/backfill_year_partition.py --year 2014 --workers 2",
        "PYTHONPATH=src python scripts/backfill_year_partition.py --year 2025 \\",
        "  --start 2025-01-01 --end 2025-05-18 --thin-only --workers 2",
        "PYTHONPATH=src python scripts/report_history_coverage.py \\",
        "  --out docs/data/freshness/2026-07-17/HISTORY_COVERAGE.md",
        "```",
        "",
    ]

    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    json_path = out.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {"hot": hot, "years": stats, "holes": {str(k): v for k, v in holes.items()},
             "view_has_2014": view_has_2014, "ok_2026": ok_2026, "ok_2014": ok_2014},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out}")
    print(f"wrote {json_path}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
