#!/usr/bin/env python3
"""End-to-end acceptance for Nous V2 backtest + recommend fixes.

Writes artifacts under docs/acceptance/<date>/ :
  - unit_tests.json
  - f3_backtest.json / trl_backtest.json  (双引擎 WF)
  - pool_score_check.json
  - ACCEPTANCE_REPORT.md
"""
from __future__ import annotations

import json
import subprocess
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "acceptance" / date.today().isoformat()
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Dual-engine gate: both must pass the same CLI WF path
ACCEPT_STRATEGIES = ("海鹰F3", "龙脉TRL")


def _artifact_slug(strategy: str) -> str:
    return {"海鹰F3": "f3", "龙脉TRL": "trl"}.get(strategy, strategy.replace(" ", "_"))


def _run(cmd: list[str], timeout: int = 300) -> dict:
    t0 = datetime.now()
    try:
        p = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": str(ROOT / "src")},
        )
        return {
            "cmd": cmd,
            "returncode": p.returncode,
            "stdout": p.stdout[-8000:] if p.stdout else "",
            "stderr": p.stderr[-8000:] if p.stderr else "",
            "elapsed_s": round((datetime.now() - t0).total_seconds(), 2),
            "ok": p.returncode == 0,
        }
    except Exception as e:
        return {
            "cmd": cmd,
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "elapsed_s": round((datetime.now() - t0).total_seconds(), 2),
            "ok": False,
        }


def run_unit_tests() -> dict:
    r = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/engine/backtest/test_position_and_mtm.py",
            "tests/engine/backtest/test_walk_forward_folds.py",
            "tests/engine/ml/test_cs_norm_ic.py",
            "tests/data/quality/test_sla_and_calendar.py",
            "-q",
            "--tb=line",
        ],
        timeout=120,
    )
    passed = failed = 0
    out = r["stdout"] + "\n" + r["stderr"]
    import re

    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", out)
    if m:
        failed = int(m.group(1))
    r["passed"] = passed
    r["failed"] = failed
    r["gate"] = r["ok"] and failed == 0 and passed >= 10
    path = OUT_DIR / "unit_tests.json"
    path.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    return r


def run_strategy_wf_backtest(strategy: str) -> dict:
    """Must exercise the SAME path as `nous backtest` (WF on by default)."""
    code = rf"""
import json, logging
logging.basicConfig(level=logging.ERROR)
from nous.engine.backtest.engine import BacktestEngine
from nous.engine.backtest.walk_forward import PurgedWalkForward
from nous.engine.backtest.data_handler import PointInTimeDataHandler

STRATEGY = {strategy!r}

dh = PointInTimeDataHandler("2026-07-10")
days = dh.get_trading_days("2025-11-01", "2026-07-10")
dh.close()
wf = PurgedWalkForward(n_splits=5, embargo_days=2, min_train_years=0.15)
folds = wf.split("2025-11-01", "2026-07-10", days)
windows = [(f.test_start, f.test_end) for f in folds]
unique = len(windows) == len(set(windows))
non_overlap = all(a.test_end < b.test_start for a, b in zip(folds, folds[1:])) if len(folds) > 1 else True

e = BacktestEngine(
    STRATEGY,
    do_walk_forward=True,
    wf_folds=5,
    start_date="2025-11-01",
    end_date="2026-07-10",
    initial_capital=1_000_000,
)
try:
    r = e.run()
    eqs = [p["equity"] for p in (r.equity_curve or [])]
    fold_windows = [(fd.get("start"), fd.get("end")) for fd in (r.fold_details or [])]
    fold_unique = len(fold_windows) == len(set(fold_windows)) if fold_windows else False
    payload = {{
        "strategy": STRATEGY,
        "label": r.label,
        "mode": "walk_forward",
        "total_return": r.total_return,
        "annual_return": r.annual_return,
        "max_drawdown": r.max_drawdown,
        "sharpe_ratio": r.sharpe_ratio,
        "sharpe_winsorized": r.sharpe_winsorized,
        "sortino_ratio": r.sortino_ratio,
        "min_daily_return": r.min_daily_return,
        "max_daily_return": r.max_daily_return,
        "n_return_spikes": r.n_return_spikes,
        "integrity_flags": r.integrity_flags,
        "total_trades": r.total_trades,
        "n_trading_days": r.n_trading_days,
        "win_rate": r.win_rate,
        "profit_factor": r.profit_factor if r.profit_factor != float("inf") else "inf",
        "equity_start": eqs[0] if eqs else None,
        "equity_end": eqs[-1] if eqs else None,
        "equity_min": min(eqs) if eqs else None,
        "equity_max": max(eqs) if eqs else None,
        "fold_details": r.fold_details,
        "fold_windows": fold_windows,
    }}
    trusted = bool((r.integrity_flags or {{}}).get("TRUSTED"))
    no_fallback = not bool((r.integrity_flags or {{}}).get("FALLBACK_MOMENTUM"))
    no_hard_spike = abs(r.min_daily_return or 0) < 0.15 and abs(r.max_daily_return or 0) < 0.15
    no_soft_spike = (r.n_return_spikes or 0) == 0
    payload["gates"] = {{
        "TRUSTED": trusted,
        "no_FALLBACK_MOMENTUM": no_fallback,
        "no_daily_spike_gt_15pct": no_hard_spike,
        "n_spikes_eq_0": no_soft_spike,
        "wf_fold_windows_unique_pre": unique,
        "wf_fold_non_overlapping_pre": non_overlap,
        "wf_fold_windows_unique_runtime": fold_unique,
        "n_folds_ge_2": len(fold_windows) >= 2,
    }}
    payload["ok"] = all(payload["gates"].values())
    payload["integrity_flags"] = r.integrity_flags
    print(json.dumps(payload, ensure_ascii=False, default=str))
finally:
    e.close()
"""
    r = _run([sys.executable, "-c", code], timeout=300)
    payload: dict = {"runner": r, "ok": False, "strategy": strategy}
    try:
        for line in reversed((r["stdout"] or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                payload = json.loads(line)
                break
        if not payload.get("ok") and r.get("stderr"):
            payload["stderr_tail"] = (r["stderr"] or "")[-1500:]
    except Exception as e:
        payload["parse_error"] = str(e)
        payload["stdout_tail"] = (r["stdout"] or "")[-2000:]
    path = OUT_DIR / f"{_artifact_slug(strategy)}_backtest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def run_pool_score_check() -> dict:
    code = r"""
import json
from nous.engine.pipelines.daily_recommendation_pipeline import _pool_display_score
from nous.engine.backtest.strategies import PortfolioSpec, HARD_MAX_SINGLE_WEIGHT, get_strategy
from nous.engine.portfolio.optimizer import apply_constraints
from nous.data.quality.validators import morning, preflight

checks = []

s0 = _pool_display_score(0)
s10 = _pool_display_score(10)
s40 = _pool_display_score(40)
checks.append({"name": "pool_rank0_is_9", "ok": s0 == 9.0, "value": s0})
checks.append({"name": "pool_rank10_is_8_5", "ok": abs(s10 - 8.5) < 1e-9, "value": s10})
checks.append({"name": "pool_deep_rank_floor_7", "ok": abs(s40 - 7.0) < 1e-9, "value": s40})
checks.append({"name": "merged_score_passthrough", "ok": _pool_display_score(5, 8.55) == 8.55, "value": 8.55})

raw = {"A": 0.9, "B": 0.05, "C": 0.05}
c = apply_constraints(raw, max_single=0.12)
checks.append({"name": "max_single_sticky", "ok": max(c.values()) <= 0.12 + 1e-6, "value": max(c.values())})

ps = PortfolioSpec(max_single_weight=0.5)
checks.append({"name": "hard_ceiling_30", "ok": ps.effective_max_single() == HARD_MAX_SINGLE_WEIGHT, "value": ps.effective_max_single()})

f3 = get_strategy("海鹰F3")
checks.append({"name": "f3_hrp_drop3", "ok": f3.portfolio.method == "hrp" and f3.portfolio.drop_n == 3,
               "value": {"method": f3.portfolio.method, "drop_n": f3.portfolio.drop_n}})

trl = get_strategy("龙脉TRL")
checks.append({"name": "trl_equal_weight", "ok": trl.portfolio.method == "equal_weight" and trl.portfolio.max_positions == 25,
               "value": {"method": trl.portfolio.method, "max_positions": trl.portfolio.max_positions}})

from nous.engine.backtest.data_handler import PointInTimeDataHandler
dh = PointInTimeDataHandler("2026-06-01")
try:
    uni = dh.get_universe("a")
    bj = [s for s in uni if s.startswith(("8", "4")) or s.startswith("920")]
    checks.append({"name": "a_universe_excludes_bj", "ok": len(bj) == 0, "value": {"n": len(uni), "bj_sample": bj[:5]}})
finally:
    dh.close()

try:
    m = morning()
    checks.append({"name": "validators_morning", "ok": isinstance(m, dict), "value": {"ok": m.get("ok"), "date": (m.get("coverage") or m).get("date") if isinstance(m.get("coverage") or m, dict) else None}})
except Exception as e:
    checks.append({"name": "validators_morning", "ok": False, "value": str(e)})

payload = {"checks": checks, "ok": all(c["ok"] for c in checks)}
print(json.dumps(payload, ensure_ascii=False, default=str))
"""
    r = _run([sys.executable, "-c", code], timeout=60)
    payload = {"runner": r, "ok": False}
    try:
        for line in reversed((r["stdout"] or "").splitlines()):
            if line.strip().startswith("{"):
                payload = json.loads(line.strip())
                break
    except Exception as e:
        payload["parse_error"] = str(e)
        payload["stderr"] = r.get("stderr")
    path = OUT_DIR / "pool_score_check.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def _pct(x, digits: int = 2) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(x)


def _money(x) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


def _credibility(result: dict) -> str:
    flags = result.get("integrity_flags") or {}
    trusted = bool(flags.get("TRUSTED"))
    suspicious = bool(flags.get("SUSPICIOUS"))
    if trusted and suspicious:
        return "基本可信（夏普对截尾敏感，需复核）"
    if trusted and not suspicious:
        return "可信"
    if trusted:
        return "存疑"
    return "不可信"


def _strategy_section(result: dict) -> list[str]:
    name = result.get("strategy") or result.get("label") or "—"
    credibility = _credibility(result)
    trusted = bool((result.get("integrity_flags") or {}).get("TRUSTED"))
    lines = [
        f"## {name} Walk-Forward 回测（与 `nous backtest` 同路径）",
        "",
        f"**样本区间：** 2025-11-01 → 2026-07-10  \n"
        f"**回测模式：** {result.get('mode') or '—'}  \n"
        f"**回测标签：** {result.get('label') or '—'}  \n"
        f"**净值可信度：** {credibility}（TRUSTED={trusted}）  \n"
        f"**折窗：** {result.get('fold_windows') or '—'}",
        "",
        "| 绩效指标 | 数值 | 投研含义 |",
        "|----------|------|----------|",
        f"| 区间总收益 | {_pct(result.get('total_return'))} | 样本期内账户盈亏幅度 |",
        f"| 年化收益 | {_pct(result.get('annual_return'))} | 按交易日折算 |",
        f"| 最大回撤 | {_pct(result.get('max_drawdown'))} | 风控核心 |",
        f"| 夏普比率 | {result.get('sharpe_ratio')} | 对尖刺敏感 |",
        f"| 截尾夏普 | {result.get('sharpe_winsorized')} | 识别虚高 |",
        f"| 索提诺比率 | {result.get('sortino_ratio')} | 只惩罚下行波动 |",
        f"| 单日收益极值 | {_pct(result.get('min_daily_return'))} / {_pct(result.get('max_daily_return'))} | 尖刺排查 |",
        f"| 收益尖刺次数（|r|>10%） | {result.get('n_return_spikes')} | 理想为 0 |",
        f"| 期初 → 期末净值 | {_money(result.get('equity_start'))} → {_money(result.get('equity_end'))} | 权益端点 |",
        f"| 净值最低 / 最高 | {_money(result.get('equity_min'))} / {_money(result.get('equity_max'))} | 波动区间 |",
        f"| 成交笔数 / 交易日 | {result.get('total_trades')} / {result.get('n_trading_days')} | 换手与样本厚度 |",
        f"| 胜率 | {_pct(result.get('win_rate'))} | 盈利笔数占比 |",
        f"| 盈亏比 | {result.get('profit_factor')} | 总盈利 / |总亏损| |",
        "",
        "### 子门禁明细",
        "",
        "| 子门禁 | 结果 |",
        "|--------|------|",
    ]
    for k, v in (result.get("gates") or {}).items():
        lines.append(f"| `{k}` | {'通过' if v else '未通过'} |")
    lines.append("")
    return lines


def write_report(
    unit: dict,
    strategy_results: dict[str, dict],
    pool: dict,
    data_assert: dict | None = None,
) -> Path:
    data_assert = data_assert or {}
    all_strat_ok = all(bool(r.get("ok")) for r in strategy_results.values())
    overall = (
        bool(unit.get("gate"))
        and bool(data_assert.get("ok", True))
        and all_strat_ok
        and bool(pool.get("ok"))
    )
    verdict = "通过" if overall else "未通过"

    unit_verdict = "通过" if unit.get("gate") else "未通过"
    pool_verdict = "通过" if pool.get("ok") else "未通过"
    data_verdict = "通过" if data_assert.get("ok") else "未通过"

    lines = [
        f"# Nous V2 投研验收报告 — {date.today().isoformat()}",
        "",
        f"**综合裁决：{verdict}**",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"产物目录：`{OUT_DIR.resolve().relative_to(ROOT.resolve())}/`",
        "",
        "## 一、验收门禁",
        "",
        "双引擎（海鹰F3 + 龙脉TRL）必须各自走与 CLI 默认一致的 WF 回测并通过可信度门禁；数据鲜度 P0 必须通过。",
        "",
        "| 门禁项 | 裁决 | 说明 |",
        "|--------|------|------|",
        f"| 引擎回归用例 | {unit_verdict} | 通过 {unit.get('passed')} / 失败 {unit.get('failed')}；含 SLA/日历单测 |",
        f"| 数据鲜度断言 | {data_verdict} | consumer=backtest；P0 日线+完整性 |",
    ]
    for name, r in strategy_results.items():
        v = "通过" if r.get("ok") else "未通过"
        lines.append(
            f"| {name} WF 回测（同 CLI） | {v} | TRUSTED；|日收益|<15%；尖刺=0；折窗唯一且不重叠 |"
        )
    lines.append(
        f"| 荐股与约束一致性 | {pool_verdict} | 按池打分、单票硬顶 30%、海鹰 HRP+Drop=3、龙脉等权、A 股剔 BJ |"
    )
    lines.append("")

    for name in ACCEPT_STRATEGIES:
        if name in strategy_results:
            lines.extend(_strategy_section(strategy_results[name]))

    lines.extend(
        [
            "### 读数要点",
            "",
            "- **先看可信度，再看收益。** TRUSTED=否 或出现单日极端尖刺时，任何夏普/年化一律视为不可用。",
            "- **双引擎同闸。** 海鹰与龙脉共用同一套 WF/尖刺/TRUSTED 门禁，避免只验一条线。",
            "- **验收必须走 CLI 默认路径。** 历史盲区：只测 `do_walk_forward=False` 会漏掉折窗塌缩与 BJ 缺价尖刺。",
            "",
            "## 三、方案存档",
            "",
            "- 设计说明：`docs/superpowers/specs/2026-07-17-backtest-recommend-fix-design.md`",
            "",
            "## 四、本目录产物清单",
            "",
            "| 文件 | 用途 |",
            "|------|------|",
            "| `ACCEPTANCE_REPORT.md` | 本报告（中文投研口径） |",
            "| `summary.json` | 机器可读总裁决 |",
            "| `unit_tests.json` | 引擎回归原始输出 |",
            "| `f3_backtest.json` | 海鹰F3 WF 绩效与子门禁 |",
            "| `trl_backtest.json` | 龙脉TRL WF 绩效与子门禁 |",
            "| `pool_score_check.json` | 打分/仓位/校验门禁明细 |",
            "",
        ]
    )
    path = OUT_DIR / "ACCEPTANCE_REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    summary = {
        "综合裁决": verdict,
        "overall": "PASS" if overall else "FAIL",
        "引擎回归": unit_verdict,
        "数据鲜度": data_verdict,
        "荐股约束": pool_verdict,
        "unit_gate": unit.get("gate"),
        "data_assert_ok": data_assert.get("ok"),
        "pool_ok": pool.get("ok"),
        "strategies": {
            name: {
                "ok": bool(r.get("ok")),
                "TRUSTED": bool((r.get("integrity_flags") or {}).get("TRUSTED")),
                "FALLBACK_MOMENTUM": bool((r.get("integrity_flags") or {}).get("FALLBACK_MOMENTUM")),
                "credibility": _credibility(r),
                "total_return": r.get("total_return"),
                "n_return_spikes": r.get("n_return_spikes"),
            }
            for name, r in strategy_results.items()
        },
        "报告路径": str(path.relative_to(ROOT)),
        "report": str(path.relative_to(ROOT)),
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def run_data_assert() -> dict:
    """Gate: backtest consumer P0 freshness (stock_daily + integrity)."""
    code = r"""
import json
from nous.data.quality.data_assert import run_assert, write_report
report = run_assert(consumer="backtest", include_integrity=True)
path = write_report(report)
payload = report.to_dict()
payload["report_path"] = str(path)
# Accept: P0 must pass; factor optional for backtest (soft)
payload["ok"] = report.p0_ok
print(json.dumps(payload, ensure_ascii=False, default=str))
"""
    r = _run([sys.executable, "-c", code], timeout=120)
    payload: dict = {"runner": r, "ok": False}
    try:
        for line in reversed((r["stdout"] or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                payload = json.loads(line)
                break
    except Exception as e:
        payload["parse_error"] = str(e)
    path = OUT_DIR / "data_assert.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> int:
    print(f"[acceptance] writing to {OUT_DIR}")
    unit = run_unit_tests()
    print(f"[acceptance] unit: gate={unit.get('gate')} passed={unit.get('passed')}")

    data = run_data_assert()
    print(f"[acceptance] data_assert: ok={data.get('ok')} p0={data.get('p0_ok')}")

    strategy_results: dict[str, dict] = {}
    for name in ACCEPT_STRATEGIES:
        r = run_strategy_wf_backtest(name)
        strategy_results[name] = r
        print(
            f"[acceptance] {name}: ok={r.get('ok')} "
            f"TRUSTED={(r.get('integrity_flags') or {}).get('TRUSTED')} "
            f"FALLBACK={(r.get('integrity_flags') or {}).get('FALLBACK_MOMENTUM')}"
        )

    pool = run_pool_score_check()
    print(f"[acceptance] pool: ok={pool.get('ok')}")
    report = write_report(unit, strategy_results, pool, data_assert=data)
    print(f"[acceptance] report: {report}")
    overall = (
        bool(unit.get("gate"))
        and bool(data.get("ok"))
        and all(bool(r.get("ok")) for r in strategy_results.values())
        and bool(pool.get("ok"))
    )
    return 0 if overall else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
