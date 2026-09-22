"""Right-side event backtest + acceptance (precision-first, panel-accelerated).

stock_daily depth may be ~1y: dual-window splits available span.
If sessions < 400 or OOS floors fail → trade_enabled=false.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional
import json
from pathlib import Path

import numpy as np
import pandas as pd

from nous.engine.screening.right_side import (
    check_abs_momentum_gate,
    detect_rs1,
    detect_rs2,
    load_config,
    _get_conn,
    _is_st,
    _capital_stats,
    _quality_stats,
)


@dataclass
class TradeEvent:
    trade_date: str
    symbol: str
    name: str
    setup: str
    entry: float
    exit_date: str
    exit_px: float
    ret: float
    hold_days: int
    exit_reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _trade_dates(conn, start: str, end: str) -> list[str]:
    from nous.data.storage.daily_bars import daily_relation_sql
    rel = daily_relation_sql(start, end, conn=conn)
    rows = conn.execute(
        f"""
        SELECT DISTINCT trade_date FROM {rel}
        WHERE trade_date>=? AND trade_date<=?
        ORDER BY trade_date
        """,
        (start, end),
    ).fetchall()
    return [r[0] for r in rows]


def _metrics(trades: list[TradeEvent]) -> dict[str, Any]:
    if not trades:
        return {"n": 0, "win_rate": None, "profit_factor": None, "avg_ret": None, "median_ret": None, "max_dd_proxy": None}
    rets = np.array([t.ret for t in trades], dtype=float)
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    wr = float((rets > 0).mean())
    gain = float(wins.sum()) if len(wins) else 0.0
    loss = float(abs(losses.sum())) if len(losses) else 0.0
    pf = (gain / loss) if loss > 1e-12 else (float("inf") if gain > 0 else None)
    eq = np.cumprod(1.0 + rets)
    peak = np.maximum.accumulate(eq)
    dd = float((eq / peak - 1.0).min()) if len(eq) else 0.0
    return {
        "n": int(len(trades)),
        "win_rate": round(wr, 4),
        "profit_factor": None if pf is None or pf == float("inf") else round(pf, 4),
        "avg_ret": round(float(rets.mean()), 4),
        "median_ret": round(float(np.median(rets)), 4),
        "max_dd_proxy": round(dd, 4),
    }


def _simulate_exit(df: pd.DataFrame, setup: str, stop_hint: float, cfg: dict) -> tuple[int, float, str, int, float]:
    """df starts at signal day (iloc0). Entry next open. Returns exit_i, exit_px, reason, hold, entry_px."""
    if len(df) < 3:
        return 0, float(df.iloc[0]["close"]), "no_next_bar", 0, float(df.iloc[0]["close"])
    entry_i = 1
    entry_px = float(df.iloc[entry_i]["open"])
    max_hold = 20 if setup == "RS1" else 12
    exit_m = int((cfg.get("rs1") or {}).get("exit_m") or 20)
    for j in range(entry_i + 1, min(len(df), entry_i + 1 + max_hold)):
        row = df.iloc[j]
        low = float(row["low"])
        close = float(row["close"])
        if low <= stop_hint:
            return j, float(min(float(row["open"]), stop_hint)), "stop", j - entry_i, entry_px
        if setup == "RS1":
            if j >= exit_m:
                ll = float(df["low"].astype(float).iloc[j - exit_m : j].min())
                if close < ll:
                    return j, close, "donchian_exit", j - entry_i, entry_px
        else:
            closes = df["close"].astype(float)
            ma20 = float(closes.iloc[max(0, j - 19) : j + 1].mean())
            if close < ma20:
                return j, close, "ma20_break", j - entry_i, entry_px
    j = min(len(df) - 1, entry_i + max_hold)
    return j, float(df.iloc[j]["close"]), "time", j - entry_i, entry_px


def _load_panel(conn, start: str, end: str) -> tuple[pd.DataFrame, dict[str, str]]:
    """Panel from partitioned daily (year tables + hot), not hot-only."""
    from nous.data.storage.daily_bars import daily_relation_sql
    pad_start = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    pad_end = (pd.Timestamp(end) + pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    rel = daily_relation_sql(pad_start, pad_end, conn=conn)
    df = pd.read_sql_query(
        f"""
        SELECT d.symbol, d.trade_date, d.open, d.high, d.low, d.close, d.volume, d.amount,
               COALESCE(b.name,'') AS name
        FROM {rel} d
        LEFT JOIN stock_basic b ON b.symbol=d.symbol
        WHERE d.trade_date>=? AND d.trade_date<=?
        ORDER BY d.symbol, d.trade_date
        """,
        conn,
        params=(pad_start, pad_end),
    )
    names = {}
    if not df.empty:
        names = df.groupby("symbol")["name"].last().to_dict()
    return df, names


def run_event_backtest(
    start: str,
    end: str,
    stride: int = 5,
    max_symbols_per_day: int = 0,
    config_name: str = "right_side.yaml",
    apply_gate: bool = True,
) -> dict[str, Any]:
    """Event backtest on stride days using partitioned daily + precision filters."""
    from nous.data.storage.daily_bars import daily_relation_sql
    from nous.engine.screening.right_side import _load_bars

    cfg = load_config(config_name)
    pol = cfg.get("precision_policy") or {}
    min_amt = float((cfg.get("liquidity") or {}).get("min_amount_20d") or 2e8)
    min_bars = int((cfg.get("liquidity") or {}).get("min_bars") or 160)
    lookback = int((cfg.get("capital") or {}).get("main_net_lookback") or 5)
    min_net = float((cfg.get("capital") or {}).get("min_main_net_sum") or 0)
    min_conf = int(pol.get("min_confluence") or 3)
    min_score = float(pol.get("min_score") or 72)
    max_out = int(pol.get("max_outputs") or 8)
    w = cfg.get("score_weights") or {}
    wt, wc, wq = float(w.get("tech", 0.45)), float(w.get("capital", 0.30)), float(w.get("quality", 0.25))

    trades: list[TradeEvent] = []
    days_scanned = 0
    days_gate_pass = 0
    signal_days = 0

    with _get_conn() as conn:
        dates = _trade_dates(conn, start, end)
        if len(dates) < 15:
            return {"error": "insufficient_trade_dates", "start": start, "end": end, "n_dates": len(dates), "trades": [], "metrics": _metrics([])}
        use_dates = dates[:: max(1, stride)]
        names = {
            r[0]: (r[1] or "")
            for r in conn.execute("SELECT symbol, name FROM stock_basic").fetchall()
        }

        for as_of in use_dates:
            days_scanned += 1
            if apply_gate:
                ok, _ = check_abs_momentum_gate(conn, cfg, as_of)
                if not ok:
                    continue
            days_gate_pass += 1

            # liquid universe on as_of from partitions
            rel = daily_relation_sql(
                (pd.Timestamp(as_of) - pd.Timedelta(days=40)).strftime("%Y-%m-%d"),
                as_of,
                conn=conn,
            )
            uni = conn.execute(
                f"""
                SELECT symbol FROM (
                  SELECT symbol, AVG(amount) a
                  FROM {rel}
                  WHERE trade_date<=? AND trade_date>=date(?, '-40 days')
                  GROUP BY symbol
                  HAVING AVG(amount) >= ?
                )
                """,
                (as_of, as_of, min_amt),
            ).fetchall()
            symbols = [r[0] for r in uni]
            if max_symbols_per_day:
                symbols = symbols[:max_symbols_per_day]

            day_hits = []
            for symbol in symbols:
                name = names.get(symbol, "")
                if _is_st(name):
                    continue
                df = _load_bars(conn, symbol, as_of, limit=300)
                if len(df) < min_bars:
                    continue
                if str(df.iloc[-1]["trade_date"]) != as_of:
                    continue
                hits = []
                for det in (detect_rs1, detect_rs2):
                    h = det(df, cfg)
                    if h:
                        hits.append(h)
                if not hits:
                    continue
                hit = max(hits, key=lambda x: x["tech_score"])
                if int(hit.get("n_confirms") or 0) < min_conf:
                    continue
                cap_score, cap_sum = _capital_stats(conn, symbol, as_of, lookback)
                if cap_sum != cap_sum or cap_sum < min_net:
                    continue
                qual_score, qual_ok, qual_reason = _quality_stats(conn, symbol, cfg)
                if not qual_ok:
                    continue
                n_conf = int(hit.get("n_confirms") or 0) + 1 + (1 if qual_reason == "ok" else 0)
                if n_conf < min_conf:
                    continue
                score = wt * hit["tech_score"] + wc * cap_score + wq * qual_score
                if score < min_score:
                    continue
                day_hits.append((score, symbol, name, hit))

            if not day_hits:
                continue
            signal_days += 1
            day_hits.sort(key=lambda x: -x[0])
            for score, symbol, name, hit in day_hits[:max_out]:
                # forward path from partitions
                fut_end = (pd.Timestamp(as_of) + pd.Timedelta(days=60)).strftime("%Y-%m-%d")
                rel_f = daily_relation_sql(as_of, fut_end, conn=conn)
                fut = pd.read_sql_query(
                    f"""
                    SELECT trade_date, open, high, low, close, volume, amount
                    FROM {rel_f}
                    WHERE symbol=? AND trade_date>=?
                    ORDER BY trade_date LIMIT 80
                    """,
                    conn,
                    params=(symbol, as_of),
                )
                if len(fut) < 4:
                    continue
                exit_i, exit_px, reason, hold, entry_px = _simulate_exit(
                    fut, hit["setup"], float(hit["stop_hint"]), cfg
                )
                if entry_px <= 0:
                    continue
                ret = exit_px / entry_px - 1.0
                trades.append(
                    TradeEvent(
                        trade_date=as_of,
                        symbol=symbol,
                        name=name,
                        setup=hit["setup"],
                        entry=round(entry_px, 2),
                        exit_date=str(fut.iloc[exit_i]["trade_date"]),
                        exit_px=round(exit_px, 2),
                        ret=round(float(ret), 4),
                        hold_days=int(hold),
                        exit_reason=reason,
                    )
                )

    return {
        "start": start,
        "end": end,
        "stride": stride,
        "days_scanned": days_scanned,
        "days_gate_pass": days_gate_pass,
        "signal_days": signal_days,
        "metrics": _metrics(trades),
        "trades": [t.to_dict() for t in trades],
    }


def split_windows(conn) -> dict[str, Any]:
    """Use 2020+ partitioned history for acceptance windows (money-mode needs depth)."""
    # Prefer 2020-01-01 → hot max for dual window
    hot_max = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
    start = "2020-01-01"
    end = hot_max
    dates = _trade_dates(conn, start, end)
    if len(dates) < 80:
        return {"start": start, "end": end, "cal_start": start, "cal_end": end, "oos_start": start, "oos_end": end, "too_short": True, "n_dates": len(dates)}
    # cal 2020-2023, oos 2024+ when long enough; else half-split
    cal_end_target = "2023-12-31"
    if any(d <= cal_end_target for d in dates) and any(d > cal_end_target for d in dates):
        cal_start, cal_end = start, cal_end_target
        oos_start, oos_end = "2024-01-01", end
    else:
        mid = dates[len(dates) // 2]
        cal_start, cal_end = start, mid
        oos_start, oos_end = mid, end
    return {
        "start": start,
        "end": end,
        "cal_start": cal_start,
        "cal_end": cal_end,
        "oos_start": oos_start,
        "oos_end": oos_end,
        "too_short": False,
        "n_dates": len(dates),
    }


def evaluate_acceptance(cal: dict, oos: dict, cfg: dict | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    acc = cfg.get("acceptance") or {}
    min_n = int(acc.get("min_oos_trades") or 40)
    min_wr = float(acc.get("min_win_rate") or 0.52)
    min_pf = float(acc.get("min_profit_factor") or 1.3)
    max_dd = float(acc.get("max_drawdown") or 0.20)
    require_dual = bool(acc.get("require_dual_window", True))
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if cal.get("error") or oos.get("error"):
        add("data", False, f"cal={cal.get('error')} oos={oos.get('error')}")
    m_cal, m_oos = cal.get("metrics") or {}, oos.get("metrics") or {}
    add("oos_n", (m_oos.get("n") or 0) >= min_n, f"oos_n={m_oos.get('n')} need>={min_n}")
    wr = m_oos.get("win_rate")
    add("oos_wr", wr is not None and wr >= min_wr, f"oos_wr={wr} need>={min_wr}")
    pf = m_oos.get("profit_factor")
    add("oos_pf", pf is not None and pf >= min_pf, f"oos_pf={pf} need>={min_pf}")
    dd = m_oos.get("max_dd_proxy")
    add("oos_dd", dd is not None and dd >= -max_dd, f"oos_dd={dd} need>=-{max_dd}")
    if require_dual:
        cal_ok = (m_cal.get("n") or 0) >= max(10, min_n // 2) and (m_cal.get("avg_ret") or -1) > 0
        oos_ok = (m_oos.get("avg_ret") or -1) > 0
        add("dual_window_expectancy", cal_ok and oos_ok, f"cal_avg={m_cal.get('avg_ret')} oos_avg={m_oos.get('avg_ret')} cal_n={m_cal.get('n')}")
    passed = all(c["ok"] for c in checks)
    return {"passed": passed, "trade_enabled": bool(passed), "checks": checks, "cal_metrics": m_cal, "oos_metrics": m_oos}


def _persist_trade_lock(enabled: bool, payload: dict) -> None:
    for p in Path(__file__).resolve().parents:
        if (p / "config" / "right_side.yaml").exists():
            path = p / "config" / "right_side_runtime.json"
            path.write_text(
                json.dumps({
                    "trade_enabled": enabled,
                    "updated": pd.Timestamp.now().isoformat(),
                    "passed": payload.get("passed"),
                    "reason": payload.get("reason"),
                    "checks": (payload.get("acceptance") or {}).get("checks"),
                }, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return


def run_acceptance(stride: int = 5, max_symbols_per_day: int = 0) -> dict[str, Any]:
    cfg = load_config()
    with _get_conn() as conn:
        wins = split_windows(conn)
        from nous.data.storage.daily_bars import daily_relation_sql
        rel = daily_relation_sql("2020-01-01", wins.get("end"), conn=conn)
        n = conn.execute(
            f"SELECT COUNT(DISTINCT trade_date) FROM {rel} WHERE trade_date>='2020-01-01'"
        ).fetchone()[0]
    cal = run_event_backtest(wins["cal_start"], wins["cal_end"], stride=stride, max_symbols_per_day=max_symbols_per_day)
    oos = run_event_backtest(wins["oos_start"], wins["oos_end"], stride=stride, max_symbols_per_day=max_symbols_per_day)
    acc = evaluate_acceptance(cal, oos, cfg)
    if n < 400:
        acc["passed"] = False
        acc["trade_enabled"] = False
        acc["checks"].append({
            "name": "history_sessions",
            "ok": False,
            "detail": f"distinct_sessions={n} need>=400 for money-mode",
        })
    out = {
        "windows": wins,
        "cal": {k: v for k, v in cal.items() if k != "trades"},
        "oos": {k: v for k, v in oos.items() if k != "trades"},
        "cal_trades_n": len(cal.get("trades") or []),
        "oos_trades_n": len(oos.get("trades") or []),
        "oos_trades_sample": (oos.get("trades") or [])[:20],
        "cal_trades_sample": (cal.get("trades") or [])[:10],
        "acceptance": acc,
        "passed": acc["passed"],
        "trade_enabled": acc["trade_enabled"],
        "history_sessions": n,
    }
    _persist_trade_lock(bool(acc["trade_enabled"]), out)
    return out


def render_acceptance_md(res: dict) -> str:
    acc = res.get("acceptance") or {}
    lines = [
        "# 右侧交易验收报告",
        "",
        f"- 结论: **{'通过' if res.get('passed') else '未通过'}**",
        f"- trade_enabled: **{res.get('trade_enabled')}**",
        f"- history_sessions: {res.get('history_sessions')}",
        f"- 窗口: `{res.get('windows')}`",
        "",
        "## Checks",
    ]
    for c in acc.get("checks") or []:
        mark = "PASS" if c.get("ok") else "FAIL"
        lines.append(f"- [{mark}] {c.get('name')}: {c.get('detail')}")
    lines += [
        "", "## Cal metrics", "```json", json.dumps(acc.get("cal_metrics"), ensure_ascii=False, indent=2), "```",
        "", "## OOS metrics", "```json", json.dumps(acc.get("oos_metrics"), ensure_ascii=False, indent=2), "```",
        "", "> 未通过前仅为研究观察，不可当交易指令（宁可错过，不可做错）。",
    ]
    return "\n".join(lines)
