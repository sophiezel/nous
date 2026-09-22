"""right_side.py — A股右侧交易 Phase1（RS1 Donchian慢突破 + RS2 多头回踩）

高精度模式：宁可错过，不可做错。与 rebound 超跌族严格隔离。规格:
  docs/superpowers/specs/2026-09-18-right-side-trading-distillation-design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yaml


def _repo_root() -> Path:
    env = __import__("os").environ.get("NOUS_CONFIG_DIR")
    if env and (Path(env) / "right_side.yaml").exists():
        return Path(env).parent if Path(env).name == "config" else Path(env)
    cur = Path(__file__).resolve()
    for p in [cur.parent, *cur.parents]:
        if (p / "config" / "right_side.yaml").exists():
            return p
    return Path.cwd()


def load_config(name: str = "right_side.yaml") -> dict:
    path = _repo_root() / "config" / name
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("right_side", raw)


def _get_conn():
    from nous.core.db import get_db
    return get_db()


@dataclass
class RightSideSignal:
    symbol: str
    name: str
    setup: str  # RS1 | RS2
    trade_date: str
    close: float
    score: float
    trigger: str
    stop_hint: float
    detail: str = ""
    factors: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RightSideResult:
    as_of: str
    gate_ok: bool
    gate_detail: str
    signals: list[RightSideSignal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    scanned: int = 0

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "gate_ok": self.gate_ok,
            "gate_detail": self.gate_detail,
            "scanned": self.scanned,
            "n_signals": len(self.signals),
            "signals": [s.to_dict() for s in self.signals],
            "notes": self.notes,
        }


def _latest_trade_date(conn) -> str:
    return conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]


def _approx_start(as_of: str, bars: int = 320) -> str:
    """Calendar pad (~1.6x trading days) for history fetch."""
    d = pd.Timestamp(as_of) - pd.Timedelta(days=int(bars * 1.7) + 30)
    return d.strftime("%Y-%m-%d")


def _load_bars(conn, symbol: str, as_of: str, limit: int = 280) -> pd.DataFrame:
    """Load OHLCV from year partitions + hot tail (not hot-only)."""
    from nous.data.storage.daily_bars import daily_relation_sql

    start = _approx_start(as_of, limit)
    rel = daily_relation_sql(start, as_of, conn=conn)
    df = pd.read_sql_query(
        f"""
        SELECT trade_date, open, high, low, close, volume, amount
        FROM {rel}
        WHERE symbol=? AND trade_date<=?
        ORDER BY trade_date DESC LIMIT ?
        """,
        conn,
        params=(symbol, as_of, limit),
    )
    if df.empty:
        return df
    return df.sort_values("trade_date").reset_index(drop=True)


def _is_st(name: str) -> bool:
    return "ST" in (name or "").upper()


def _volume_ratio(vol: pd.Series) -> float:
    if len(vol) < 6:
        return 0.0
    base = float(vol.iloc[-6:-1].mean())
    if base <= 0:
        return 0.0
    return float(vol.iloc[-1] / base)


def _atr(df: pd.DataFrame, n: int = 20) -> float:
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


def _load_index_bars(conn, symbol: str, as_of: str, limit: int = 320) -> pd.DataFrame:
    """Load from index_daily; accepts 000300 or IDX_000300."""
    cands = [symbol]
    if not symbol.startswith("IDX_"):
        cands.append(f"IDX_{symbol}")
    for sym in cands:
        df = pd.read_sql_query(
            """
            SELECT trade_date, open, high, low, close, volume, amount
            FROM index_daily
            WHERE symbol=? AND trade_date<=?
            ORDER BY trade_date DESC LIMIT ?
            """,
            conn,
            params=(sym, as_of, limit),
        )
        if not df.empty:
            return df.sort_values("trade_date").reset_index(drop=True), sym
    return pd.DataFrame(), symbol


def check_abs_momentum_gate(conn, cfg: dict, as_of: str) -> tuple[bool, str]:
    """Index absolute momentum + optional MA200 filter (Setup E style)."""
    g = cfg.get("gate") or {}
    sym = g.get("index_symbol") or "000300"
    days = int(g.get("abs_mom_days") or 252)
    df, sym = _load_index_bars(conn, sym, as_of, limit=max(days + 40, 320))
    if df.empty or len(df) < days + 5:
        # fallback stock_daily if someone stores index there
        df2 = _load_bars(conn, sym.replace("IDX_", ""), as_of, limit=max(days + 40, 320))
        if not df2.empty and len(df2) >= days + 5:
            df = df2
        else:
            return True, f"指数{sym}数据不足，跳过绝对动量闸门"
    closes = df["close"].astype(float)
    ret = float(closes.iloc[-1] / closes.iloc[-(days + 1)] - 1)
    ma200 = float(closes.iloc[-200:].mean()) if len(closes) >= 200 else float(closes.mean())
    above = float(closes.iloc[-1]) >= ma200
    ok = ret > 0
    if g.get("require_index_above_ma200", True):
        ok = ok and above
    detail = f"{sym} {days}d收益{ret:+.1%} close{closes.iloc[-1]:.2f} MA200={ma200:.2f} above={above}"
    return ok, detail


def detect_rs1(df: pd.DataFrame, cfg: dict) -> Optional[dict]:
    """Donchian slow breakout: close > max(high of prior entry_n bars)."""
    rs = cfg.get("rs1") or {}
    n = int(rs.get("entry_n") or 55)
    min_vr = float(rs.get("min_volume_ratio") or 1.8)
    max_ext = float(rs.get("max_extension_ma20") or 1.10)
    if len(df) < n + 5:
        return None
    highs = df["high"].astype(float)
    closes = df["close"].astype(float)
    prior_high = float(highs.iloc[-(n + 1):-1].max())
    last = float(closes.iloc[-1])
    # 收盘确认突破（不只盘中刺破）
    if last <= prior_high:
        return None
    if rs.get("require_close_confirm", True) and float(df["close"].iloc[-1]) <= prior_high:
        return None
    vr = _volume_ratio(df["volume"].astype(float))
    if vr < min_vr:
        return None
    ma20 = float(closes.iloc[-20:].mean())
    ma60 = float(closes.iloc[-60:].mean()) if len(closes) >= 60 else ma20
    if ma20 <= 0 or last / ma20 > max_ext:
        return None
    if rs.get("require_above_ma60", True) and last < ma60:
        return None
    atr = _atr(df, 20)
    stop = max(float(closes.iloc[-int(rs.get("exit_m") or 20):].min()), last - 2 * atr)
    confirms = ["donchian", "volume", "extension"]
    if last >= ma60:
        confirms.append("ma60")
    return {
        "setup": "RS1",
        "trigger": f"Donchian{n}突破{prior_high:.2f}",
        "stop_hint": round(stop, 2),
        "detail": f"量比{vr:.2f}; 延伸{last/ma20:.2f}xMA20; ATR20={atr:.2f}",
        "factors": {
            "prior_high": prior_high,
            "volume_ratio": round(vr, 2),
            "ext_ma20": round(last / ma20, 3),
            "confirms": confirms,
        },
        "tech_score": min(100.0, 60 + 12 * min(vr / min_vr, 2) + 8 * min((last / prior_high - 1) * 100, 2)),
        "n_confirms": len(confirms),
    }


def detect_rs2(df: pd.DataFrame, cfg: dict) -> Optional[dict]:
    """MA stack + pullback reclaim of pullback_ma (precision)."""
    rs = cfg.get("rs2") or {}
    stack = list(rs.get("ma_stack") or [5, 10, 20, 60, 120])
    pb = int(rs.get("pullback_ma") or 10)
    protect = int(rs.get("protect_ma") or 20)
    min_vr = float(rs.get("min_volume_ratio_on_reclaim") or 1.0)
    max_depth = float(rs.get("max_pullback_depth_pct") or 0.08)
    need = max(stack) + 5
    if len(df) < need:
        return None
    closes = df["close"].astype(float)
    lows = df["low"].astype(float)
    vols = df["volume"].astype(float)
    mas = {w: closes.rolling(w).mean() for w in stack}
    vals = [float(mas[w].iloc[-1]) for w in stack]
    if any(np.isnan(v) for v in vals):
        return None
    if not all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)):
        return None
    ma_pb = mas[pb]
    if float(ma_pb.iloc[-1]) <= float(ma_pb.iloc[-2]):
        return None
    ma_pb_y = float(ma_pb.iloc[-2])
    ma_pb_t = float(ma_pb.iloc[-1])
    if not (float(lows.iloc[-2]) < ma_pb_y and float(closes.iloc[-1]) > ma_pb_t):
        return None
    ma_prot = float(mas[protect].iloc[-1])
    if float(closes.iloc[-1]) < ma_prot:
        return None
    # 回踩深度不过深（相对近20高）
    recent_high = float(closes.iloc[-21:-1].max())
    depth = 1.0 - float(lows.iloc[-2]) / recent_high if recent_high > 0 else 1.0
    if depth > max_depth:
        return None
    if rs.get("require_shrink_on_pullback", True):
        if float(vols.iloc[-2]) >= float(vols.iloc[-3]) * 0.95:
            # 回踩日未缩量 → 疑似转弱，拒绝
            return None
    vr = _volume_ratio(vols)
    if vr < min_vr:
        return None
    last = float(closes.iloc[-1])
    atr = _atr(df, 14)
    stop = min(ma_prot * 0.985, last - 1.8 * atr)
    confirms = ["ma_stack", "pullback_reclaim", "protect_ma"]
    if depth <= max_depth:
        confirms.append("depth_ok")
    if vr >= min_vr:
        confirms.append("volume")
    return {
        "setup": "RS2",
        "trigger": f"多头回踩MA{pb}收回",
        "stop_hint": round(float(stop), 2),
        "detail": (
            f"堆叠{'/'.join(map(str, stack))}; 量比{vr:.2f}; "
            f"护线MA{protect}={ma_prot:.2f}; 回踩深{depth:.1%}"
        ),
        "factors": {
            "volume_ratio": round(vr, 2),
            "ma_protect": round(ma_prot, 2),
            "pullback_depth": round(depth, 4),
            "confirms": confirms,
        },
        "tech_score": min(100.0, 55 + 15 + 10 * min(vr, 2)),
        "n_confirms": len(confirms),
    }


def _capital_stats(conn, symbol: str, as_of: str, lookback: int = 5) -> tuple[float, float]:
    """Return (score_0_100, net_sum). Prefer fund_flow; else LHB net_amount proxy.

    Missing both → (50, nan). Live precision may reject nan; history uses LHB.
    """
    try:
        rows = conn.execute(
            """
            SELECT main_net FROM fund_flow_stock
            WHERE symbol=? AND trade_date<=?
            ORDER BY trade_date DESC LIMIT ?
            """,
            (symbol, as_of, lookback),
        ).fetchall()
    except Exception:
        rows = []
    if rows:
        s = sum(float(r[0] or 0) for r in rows)
        src = "fund_flow"
    else:
        try:
            rows = conn.execute(
                """
                SELECT net_amount FROM lhb_daily
                WHERE symbol=? AND trade_date<=?
                ORDER BY trade_date DESC LIMIT ?
                """,
                (symbol, as_of, lookback),
            ).fetchall()
        except Exception:
            rows = []
        if not rows:
            return 50.0, float("nan")
        s = sum(float(r[0] or 0) for r in rows)
        src = "lhb"
    if s > 5e7:
        score = 85.0
    elif s >= 3e7:
        score = 75.0
    elif s >= 0:
        score = 65.0
    elif s > -5e7:
        score = 35.0
    else:
        score = 15.0
    # tag via unused hundredths when debugging not needed
    return score, s


def _quality_stats(conn, symbol: str, cfg: dict) -> tuple[float, bool, str]:
    """Return (score, pass?, reason)."""
    hf = cfg.get("hard_filters") or {}
    try:
        row = conn.execute(
            "SELECT pe, pb, total_mv FROM stock_fundamental WHERE symbol=?",
            (symbol,),
        ).fetchone()
    except Exception:
        return 50.0, True, "no_fundamental"
    if not row:
        return 50.0, True, "no_fundamental"
    pe, pb, mv = row[0], row[1], row[2]
    if hf.get("exclude_negative_pe", True) and pe is not None and pe < 0:
        return 0.0, False, "neg_pe"
    max_pe = hf.get("max_pe")
    if max_pe is not None and pe is not None and pe > float(max_pe):
        return 10.0, False, "pe_too_high"
    min_mv = hf.get("min_mv")
    if min_mv is not None and (mv is None or float(mv) < float(min_mv)):
        return 10.0, False, "mv_too_small"
    score = 50.0
    if pe is not None and pe > 0:
        if pe <= 40:
            score += 25
        elif pe <= 80:
            score += 10
        elif pe > 120:
            score -= 10
    if mv is not None and mv >= 2e10:
        score += 15
    elif mv is not None and mv >= 1e10:
        score += 8
    return float(np.clip(score, 0, 100)), True, "ok"


def _trade_enabled_flag() -> tuple[bool, str]:
    """Sidecar written by acceptance; default False (宁可错过)."""
    import json
    for p in Path(__file__).resolve().parents:
        path = p / "config" / "right_side_runtime.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return bool(data.get("trade_enabled")), str(path)
            except Exception:
                return False, str(path)
    return False, "missing runtime lock"


def scan_right_side(
    as_of: str = "",
    top_n: int = 30,
    config_name: str = "right_side.yaml",
    max_symbols: int = 0,
    research: bool = False,
) -> RightSideResult:
    cfg = load_config(config_name)
    notes: list[str] = []
    enabled, lock_path = _trade_enabled_flag()
    if not enabled and not research:
        notes.append(f"trade_enabled=false（验收未通过或未跑验收）— 仅研究观察；lock={lock_path}")
    with _get_conn() as conn:
        as_of = as_of or _latest_trade_date(conn)
        gate_ok, gate_detail = check_abs_momentum_gate(conn, cfg, as_of)
        pol = cfg.get("precision_policy") or {}
        precision = (cfg.get("mode") or "precision") == "precision"
        empty_on_gate = bool(pol.get("empty_when_gate_fails", True)) if precision else False
        if not gate_ok:
            notes.append("绝对动量闸门未通过：高精度模式下不输出交易信号")
            if empty_on_gate:
                return RightSideResult(
                    as_of=as_of,
                    gate_ok=False,
                    gate_detail=gate_detail,
                    signals=[],
                    notes=notes,
                    scanned=0,
                )
        rows = conn.execute(
            """
            SELECT d.symbol, COALESCE(b.name,'') AS name
            FROM stock_daily d
            LEFT JOIN stock_basic b ON b.symbol=d.symbol
            WHERE d.trade_date=?
            """,
            (as_of,),
        ).fetchall()
        symbols = [(r[0], r[1]) for r in rows]
        if max_symbols and max_symbols > 0:
            symbols = symbols[:max_symbols]
        min_amt = float((cfg.get("liquidity") or {}).get("min_amount_20d") or 2e8)
        min_bars = int((cfg.get("liquidity") or {}).get("min_bars") or 160)
        w = cfg.get("score_weights") or {}
        wt, wc, wq = float(w.get("tech", 0.45)), float(w.get("capital", 0.30)), float(w.get("quality", 0.25))
        lookback = int((cfg.get("capital") or {}).get("main_net_lookback") or 5)
        min_net = float((cfg.get("capital") or {}).get("min_main_net_sum") or 0)
        require_cap = bool(pol.get("require_capital_confirm", True)) if precision else False
        require_qual = bool(pol.get("require_quality_pass", True)) if precision else False
        min_conf = int(pol.get("min_confluence") or 3)
        min_score = float(pol.get("min_score") or 72)
        max_out = int(pol.get("max_outputs") or (cfg.get("output") or {}).get("top_n") or 8)
        if top_n:
            max_out = min(max_out, top_n)
        signals: list[RightSideSignal] = []
        scanned = 0
        for symbol, name in symbols:
            if (cfg.get("hard_filters") or {}).get("exclude_st", True) and _is_st(name):
                continue
            df = _load_bars(conn, symbol, as_of, limit=300)
            if len(df) < min_bars:
                continue
            if float(df["amount"].astype(float).iloc[-20:].mean()) < min_amt:
                continue
            scanned += 1
            if (cfg.get("hard_filters") or {}).get("exclude_limit_up_one_word", True):
                o, h, l, c = map(float, df.iloc[-1][["open", "high", "low", "close"]])
                if o == h == l == c and c > float(df.iloc[-2]["close"]) * 1.09:
                    continue
            hits = []
            for det in (detect_rs1, detect_rs2):
                hh = det(df, cfg)
                if hh:
                    hits.append(hh)
            if not hits:
                continue
            hit = max(hits, key=lambda x: x["tech_score"])
            if int(hit.get("n_confirms") or 0) < min_conf:
                continue
            cap_score, cap_sum = _capital_stats(conn, symbol, as_of, lookback)
            if require_cap:
                if cap_sum != cap_sum:  # nan
                    continue  # 无资金数据 = 不做（宁可错过）
                if cap_sum < min_net:
                    continue
            qual_score, qual_ok, qual_reason = _quality_stats(conn, symbol, cfg)
            if require_qual and not qual_ok:
                continue
            # confluence: tech confirms + capital + quality
            n_conf = int(hit.get("n_confirms") or 0)
            if cap_sum == cap_sum and cap_sum >= min_net:
                n_conf += 1
            if qual_ok and qual_reason == "ok":
                n_conf += 1
            if n_conf < min_conf:
                continue
            score = wt * hit["tech_score"] + wc * cap_score + wq * qual_score
            if score < min_score:
                continue
            signals.append(
                RightSideSignal(
                    symbol=symbol,
                    name=name,
                    setup=hit["setup"],
                    trade_date=as_of,
                    close=round(float(df.iloc[-1]["close"]), 2),
                    score=round(float(score), 1),
                    trigger=hit["trigger"],
                    stop_hint=float(hit["stop_hint"]),
                    detail=hit["detail"],
                    factors={
                        **hit.get("factors", {}),
                        "capital_score": cap_score,
                        "main_net_sum": None if cap_sum != cap_sum else round(cap_sum, 0),
                        "quality": qual_score,
                        "quality_reason": qual_reason,
                        "confluence": n_conf,
                        "gate_ok": gate_ok,
                    },
                )
            )
        signals.sort(key=lambda s: -s.score)
        return RightSideResult(
            as_of=as_of,
            gate_ok=gate_ok,
            gate_detail=gate_detail,
            signals=signals[:max_out],
            notes=notes,
            scanned=scanned,
        )


def render_markdown(res: RightSideResult) -> str:
    lines = [
        f"# 右侧选股 Phase1 — {res.as_of}",
        "",
        f"- 闸门: {'通过' if res.gate_ok else '未通过'} — {res.gate_detail}",
        f"- 扫描流动性合格: {res.scanned} | 信号: {len(res.signals)}",
        "",
        "| 代码 | 名称 | Setup | 得分 | 收盘 | 触发 | 止损参考 | 说明 |",
        "|------|------|-------|------|------|------|----------|------|",
    ]
    for s in res.signals:
        lines.append(
            f"| {s.symbol} | {s.name} | {s.setup} | {s.score:.1f} | {s.close} | {s.trigger} | {s.stop_hint} | {s.detail} |"
        )
    for n in res.notes:
        lines.append(f"\n> {n}")
    lines.append("\n*研究信号，非投资建议。与 rebound 超跌族互斥使用。*")
    return "\n".join(lines)
