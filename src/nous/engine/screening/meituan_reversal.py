"""美团(03690) 多层反转 / 趋势切换指标。

研究用概率信号：宏观·利率汇率·资金·相对强弱·技术·情绪·基本面·体制。
综合分 ∈ [-100, +100]；缺数层 skipped 并重归一权重。
"""

from __future__ import annotations

import math
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yaml

from nous.core.db import get_db

# ── paths ──────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "pyproject.toml").exists():
            return p
    return Path.cwd()


def _config_dir() -> Path:
    override = os.environ.get("NOUS_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return _repo_root() / "config"


def load_config(name: str = "meituan_reversal.yaml") -> dict:
    path = _config_dir() / name
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("meituan", raw)


# ── result types ───────────────────────────────────────────────────────


@dataclass
class LayerScore:
    name: str
    score: Optional[float]  # -100..100 or None if skipped
    weight: float
    detail: str = ""
    skipped_reason: str = ""

    @property
    def active(self) -> bool:
        return self.score is not None and not self.skipped_reason


@dataclass
class MeituanReversalResult:
    as_of: str
    symbol: str
    name: str
    last_close: Optional[float]
    composite: Optional[float]
    regime: str
    signal: str
    layers: list[LayerScore] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    disclaimer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "symbol": self.symbol,
            "name": self.name,
            "last_close": self.last_close,
            "composite": self.composite,
            "regime": self.regime,
            "signal": self.signal,
            "layers": [asdict(x) for x in self.layers],
            "notes": self.notes,
            "disclaimer": self.disclaimer,
        }


# ── math helpers ───────────────────────────────────────────────────────


def _clip(x: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _zscore(series: pd.Series, window: int) -> Optional[float]:
    s = series.dropna()
    if len(s) < max(10, window // 2):
        return None
    w = s.iloc[-window:] if len(s) >= window else s
    mu, sd = float(w.mean()), float(w.std(ddof=1))
    if sd < 1e-12:
        return 0.0
    return float((w.iloc[-1] - mu) / sd)


def _rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 2:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    v = rsi.iloc[-1]
    return None if pd.isna(v) else float(v)


def _macd_hist(closes: pd.Series, fast: int, slow: int, signal: int) -> Optional[float]:
    if len(closes) < slow + signal + 2:
        return None
    ema_f = closes.ewm(span=fast, adjust=False).mean()
    ema_s = closes.ewm(span=slow, adjust=False).mean()
    macd = ema_f - ema_s
    sig = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - sig
    v = hist.iloc[-1]
    return None if pd.isna(v) else float(v)


def _boll_pct(closes: pd.Series, period: int = 20) -> Optional[float]:
    if len(closes) < period:
        return None
    ma = closes.rolling(period).mean()
    sd = closes.rolling(period).std(ddof=1)
    upper = ma + 2 * sd
    lower = ma - 2 * sd
    width = upper - lower
    if float(width.iloc[-1] or 0) <= 0:
        return None
    return float((closes.iloc[-1] - lower.iloc[-1]) / width.iloc[-1])


# ── data loaders ───────────────────────────────────────────────────────


def _fetch(sql: str, args: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Read rows as dicts; an unavailable DB degrades to "no data".

    Layer scorers already skip when their table is empty, so an unmounted /
    missing data dir must read as empty rather than raise.
    """
    try:
        with get_db() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.Error:
        return []


def _load_ohlcv(symbol: str, as_of: str | None, bars: int) -> pd.DataFrame:
    sql = (
        "SELECT trade_date, open, high, low, close, volume, amount "
        "FROM stock_daily WHERE symbol=? "
    )
    args: list[Any] = [symbol]
    if as_of:
        sql += "AND trade_date<=? "
        args.append(as_of)
    sql += "ORDER BY trade_date DESC LIMIT ?"
    args.append(int(bars) + 5)
    rows = _fetch(sql, args)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.tail(bars).reset_index(drop=True)


def _load_index(symbol: str, as_of: str | None, bars: int) -> pd.DataFrame:
    sql = "SELECT trade_date, close FROM index_global_daily WHERE symbol=? "
    args: list[Any] = [symbol]
    if as_of:
        sql += "AND trade_date<=? "
        args.append(as_of)
    sql += "ORDER BY trade_date DESC LIMIT ?"
    args.append(int(bars) + 5)
    rows = _fetch(sql, args)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    # dedupe same-day duplicates by keeping last
    df = df.drop_duplicates("trade_date", keep="last")
    return df.tail(bars).reset_index(drop=True)


def _load_hsgt(symbol: str, as_of: str | None, n: int) -> pd.DataFrame:
    sql = (
        "SELECT trade_date, net_inflow, holding_pct FROM hsgt_stock_daily "
        "WHERE symbol=? "
    )
    args: list[Any] = [symbol]
    if as_of:
        sql += "AND trade_date<=? "
        args.append(as_of)
    sql += "ORDER BY trade_date DESC LIMIT ?"
    args.append(int(n) * 3)
    rows = _fetch(sql, args)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    df["net_inflow"] = pd.to_numeric(df["net_inflow"], errors="coerce")
    df["holding_pct"] = pd.to_numeric(df["holding_pct"], errors="coerce")
    # aggregate duplicate dates (multiple directions)
    df = df.groupby("trade_date", as_index=False).agg(
        net_inflow=("net_inflow", "sum"),
        holding_pct=("holding_pct", "last"),
    )
    return df.tail(n).reset_index(drop=True)


def _load_sentiment(as_of: str | None) -> Optional[float]:
    sql = "SELECT date, score FROM sentiment_cache "
    args: list[Any] = []
    if as_of:
        sql += "WHERE date<=? "
        args.append(as_of)
    sql += "ORDER BY date DESC LIMIT 1"
    rows = _fetch(sql, args)
    if not rows:
        return None
    return float(rows[0]["score"])


def _load_macro_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        r = _fetch(
            "SELECT TRADE_DATE, LPR1Y, LPR5Y FROM macro_lpr ORDER BY TRADE_DATE DESC LIMIT 2"
        )
        if r:
            cur, prev = r[0], r[1] if len(r) > 1 else r[0]
            out["lpr1y"] = float(cur["LPR1Y"])
            out["lpr_delta"] = float(cur["LPR1Y"]) - float(prev["LPR1Y"])
            out["lpr_date"] = cur["TRADE_DATE"]
    except Exception:
        pass
    try:
        r = _fetch('SELECT * FROM macro_pmi ORDER BY "月份" DESC LIMIT 1')
        if r:
            d = r[0]
            out["pmi_mfg"] = float(d.get("制造业-指数") or float("nan"))
            out["pmi_non"] = float(d.get("非制造业-指数") or float("nan"))
            out["pmi_month"] = d.get("月份")
    except Exception:
        pass
    try:
        r = _fetch('SELECT * FROM macro_m2 ORDER BY "月份" DESC LIMIT 1')
        if r:
            d = r[0]
            out["m2_yoy"] = float(d.get("货币和准货币(M2)-同比增长") or float("nan"))
            out["m1_yoy"] = float(d.get("货币(M1)-同比增长") or float("nan"))
            out["m2_month"] = d.get("月份")
    except Exception:
        pass
    return out


def _load_fundamentals_csv(cfg: dict) -> Optional[pd.DataFrame]:
    rel = cfg.get("fundamentals_csv") or "config/meituan_fundamentals.csv"
    path = Path(rel)
    if not path.is_absolute():
        path = _repo_root() / path
    if not path.exists():
        return None
    df = pd.read_csv(path)
    if df.empty:
        return None
    df = df.sort_values("as_of").reset_index(drop=True)
    df["as_of"] = df["as_of"].astype(str)
    return df


def fundamentals_row_as_of(df: pd.DataFrame, as_of: str | None) -> Optional[pd.Series]:
    """Point-in-time: use latest fundamentals with as_of <= trade date."""
    if df is None or df.empty:
        return None
    if not as_of:
        return df.iloc[-1]
    eligible = df[df["as_of"] <= as_of]
    if eligible.empty:
        return None
    return eligible.iloc[-1]


def _synthetic_ohlcv(n: int = 120, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, 0.02, size=n)
    # inject mild oversold recovery in last 15 bars
    rets[-15:-8] = rng.normal(-0.015, 0.01, size=7)
    rets[-8:] = rng.normal(0.012, 0.012, size=8)
    close = 80 * np.exp(np.cumsum(rets))
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    high = close * (1 + rng.uniform(0.002, 0.02, n))
    low = close * (1 - rng.uniform(0.002, 0.02, n))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    vol = rng.integers(15_000_000, 60_000_000, n).astype(float)
    vol[-5:] *= 2.5
    return pd.DataFrame(
        {
            "trade_date": [d.strftime("%Y-%m-%d") for d in dates],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "amount": vol * close,
        }
    )


# ── layer scorers ──────────────────────────────────────────────────────


def score_technical(df: pd.DataFrame, cfg: dict) -> LayerScore:
    w = float(cfg["weights"]["technical"])
    if df.empty or len(df) < 40:
        return LayerScore("technical", None, w, skipped_reason="价格序列不足")
    lb = cfg["lookbacks"]
    th = cfg["thresholds"]
    closes = df["close"]
    vols = df["volume"]
    rsi = _rsi(closes, int(lb["rsi"]))
    hist = _macd_hist(closes, int(lb["macd_fast"]), int(lb["macd_slow"]), int(lb["macd_signal"]))
    boll = _boll_pct(closes, int(lb["boll"]))
    ma_f = closes.rolling(int(lb["ma_fast"])).mean().iloc[-1]
    ma_s = closes.rolling(int(lb["ma_slow"])).mean().iloc[-1]
    vol_ma = vols.rolling(int(lb["vol_ma"])).mean().iloc[-1]
    vol_ratio = float(vols.iloc[-1] / vol_ma) if vol_ma and not pd.isna(vol_ma) else None
    ret_5 = float(closes.iloc[-1] / closes.iloc[-6] - 1) if len(closes) >= 6 else None

    parts: list[float] = []
    bits: list[str] = []
    if rsi is not None:
        # mean-reversion style: oversold → positive (bullish reversal bias)
        if rsi <= th["rsi_oversold"]:
            parts.append(_clip((th["rsi_oversold"] - rsi) * 4))
            bits.append(f"RSI超卖{rsi:.1f}")
        elif rsi >= th["rsi_overbought"]:
            parts.append(_clip((th["rsi_overbought"] - rsi) * 4))
            bits.append(f"RSI超买{rsi:.1f}")
        else:
            parts.append(_clip((50 - rsi) * 1.2))
            bits.append(f"RSI中性{rsi:.1f}")
    if hist is not None:
        # MACD hist expanding up after negative = bullish reverse impulse
        prev = _macd_hist(closes.iloc[:-1], int(lb["macd_fast"]), int(lb["macd_slow"]), int(lb["macd_signal"]))
        macd_score = _clip(hist / (abs(closes.iloc[-1]) * 0.001 + 1e-9) * 8)
        if prev is not None and prev < 0 <= hist:
            macd_score = _clip(macd_score + 25)
            bits.append("MACD柱翻红")
        elif prev is not None and prev > 0 >= hist:
            macd_score = _clip(macd_score - 25)
            bits.append("MACD柱翻绿")
        else:
            bits.append(f"MACD柱{hist:.3f}")
        parts.append(macd_score)
    if boll is not None:
        # near lower band → rebound bias
        parts.append(_clip((0.5 - boll) * 120))
        bits.append(f"布林位置{boll:.2f}")
    if not pd.isna(ma_f) and not pd.isna(ma_s):
        gap = float(ma_f / ma_s - 1)
        parts.append(_clip(gap * 800))
        bits.append(f"MA20/60乖离{gap:.2%}")
    if vol_ratio is not None:
        if vol_ratio >= th["volume_climax"] and ret_5 is not None and ret_5 < -0.03:
            parts.append(30)  # capitulation volume after drop
            bits.append(f"放量杀跌后量比{vol_ratio:.1f}")
        elif vol_ratio >= th["volume_climax"] and ret_5 is not None and ret_5 > 0.03:
            parts.append(-20)
            bits.append(f"放量冲高量比{vol_ratio:.1f}")
        else:
            bits.append(f"量比{vol_ratio:.1f}")

    if not parts:
        return LayerScore("technical", None, w, skipped_reason="技术指标计算失败")
    score = _clip(float(np.mean(parts)))
    return LayerScore("technical", score, w, detail="; ".join(bits))


def score_relative(df: pd.DataFrame, cfg: dict, as_of: str | None) -> LayerScore:
    w = float(cfg["weights"]["relative"])
    if df.empty:
        return LayerScore("relative", None, w, skipped_reason="无标的价格")
    n = int(cfg["lookbacks"]["relative"])
    if len(df) < n + 2:
        return LayerScore("relative", None, w, skipped_reason="相对强弱窗口不足")
    base_ret = float(df["close"].iloc[-1] / df["close"].iloc[-n - 1] - 1)
    peer_rets: list[float] = []
    bits = [f"美团{n}日{base_ret:.2%}"]
    for p in cfg.get("peers", []):
        pdf = _load_ohlcv(p["symbol"], as_of, n + 10)
        if len(pdf) < n + 1:
            continue
        pr = float(pdf["close"].iloc[-1] / pdf["close"].iloc[-n - 1] - 1)
        peer_rets.append(pr)
        bits.append(f"{p.get('name', p['symbol'])}{pr:.2%}")
    hsi = _load_index(cfg["indices"]["market"], as_of, n + 10)
    if len(hsi) >= n + 1:
        hr = float(hsi["close"].iloc[-1] / hsi["close"].iloc[-n - 1] - 1)
        peer_rets.append(hr)
        bits.append(f"恒指{hr:.2%}")
    if not peer_rets:
        return LayerScore("relative", None, w, skipped_reason="同业/指数数据不足")
    rel = base_ret - float(np.mean(peer_rets))
    # residual momentum: strong relative = trend continuation (positive)
    # deep underperformance after decline = mean-reversion candidate still slightly negative until stabilize
    score = _clip(rel * 600)
    return LayerScore("relative", score, w, detail="; ".join(bits) + f"; 相对超额{rel:.2%}")


def score_capital(cfg: dict, as_of: str | None) -> LayerScore:
    w = float(cfg["weights"]["capital"])
    n = int(cfg["lookbacks"]["hsgt"])
    df = _load_hsgt(cfg["symbol"], as_of, max(n, 20))
    if df.empty:
        return LayerScore("capital", None, w, skipped_reason="无港股通持股/流入数据")
    z = _zscore(df["net_inflow"], max(n, 15))
    hold = float(df["holding_pct"].dropna().iloc[-1]) if df["holding_pct"].notna().any() else None
    recent = float(df["net_inflow"].tail(n).sum())
    bits = [f"近{n}日净流入合计{recent/1e8:.2f}亿"]
    if hold is not None:
        bits.append(f"持股占比{hold:.2f}%")
    if z is None:
        score = _clip(np.sign(recent) * min(40, abs(recent) / 1e9 * 20))
    else:
        score = _clip(z * 35)
        bits.append(f"流入z={z:.2f}")
        # extreme outflow often precedes bounce (capitulation) — soften
        if z <= -cfg["thresholds"]["hsgt_z"] and hold is not None:
            score = _clip(score * 0.4 + 15)
            bits.append("极端流出部分计入抛压释放")
    return LayerScore("capital", score, w, detail="; ".join(bits))


def score_macro(cfg: dict) -> LayerScore:
    w = float(cfg["weights"]["macro"])
    snap = _load_macro_snapshot()
    if not snap:
        return LayerScore("macro", None, w, skipped_reason="宏观表为空")
    parts: list[float] = []
    bits: list[str] = []
    pmi = snap.get("pmi_non")
    if pmi is not None and not math.isnan(pmi):
        # services PMI: >50 supportive for local commerce
        parts.append(_clip((pmi - 50) * 18))
        bits.append(f"非制造PMI{pmi:.1f}({snap.get('pmi_month')})")
    m2 = snap.get("m2_yoy")
    m1 = snap.get("m1_yoy")
    if m2 is not None and not math.isnan(m2):
        parts.append(_clip((m2 - 8.0) * 12))
        bits.append(f"M2同比{m2:.1f}%")
    if m1 is not None and m2 is not None and not math.isnan(m1) and not math.isnan(m2):
        scissors = m1 - m2
        parts.append(_clip(scissors * 8))
        bits.append(f"M1-M2{scissors:.1f}pp")
    lpr_delta = snap.get("lpr_delta")
    if lpr_delta is not None:
        # rate cut (+) supportive
        parts.append(_clip(-lpr_delta * 200))
        bits.append(f"LPR1Y变动{lpr_delta:.2f}({snap.get('lpr_date')})")
    if not parts:
        return LayerScore("macro", None, w, skipped_reason="宏观字段不可解析")
    return LayerScore("macro", _clip(float(np.mean(parts))), w, detail="; ".join(bits))


def score_rates_fx(cfg: dict, as_of: str | None) -> LayerScore:
    w = float(cfg["weights"]["rates_fx"])
    idx = cfg["indices"]
    parts: list[float] = []
    bits: list[str] = []
    tnx = _load_index(idx["us10y"], as_of, 80)
    if len(tnx) >= 20:
        z = _zscore(tnx["close"], 40)
        chg = float(tnx["close"].iloc[-1] / tnx["close"].iloc[-21] - 1)
        # rising US yields → pressure on HK/growth (negative)
        if z is not None:
            parts.append(_clip(-z * 30))
            bits.append(f"美债10Y z={z:.2f}")
        parts.append(_clip(-chg * 400))
        bits.append(f"美债10Y月变{chg:.2%}")
    dxy = _load_index(idx["dxy"], as_of, 80)
    if len(dxy) >= 20:
        z = _zscore(dxy["close"], 40)
        if z is not None:
            parts.append(_clip(-z * 25))
            bits.append(f"美元指数z={z:.2f}")
    usdcny = _load_index(idx["usdcny"], as_of, 80)
    if len(usdcny) >= 20:
        z = _zscore(usdcny["close"], 40)
        if z is not None:
            parts.append(_clip(-z * 20))
            bits.append(f"USDCNY z={z:.2f}")
    vix = _load_index(idx["vix"], as_of, 80)
    if len(vix) >= 15:
        last = float(vix["close"].iloc[-1])
        parts.append(_clip((20 - last) * 3))
        bits.append(f"VIX{last:.1f}")
    if not parts:
        return LayerScore("rates_fx", None, w, skipped_reason="全球利率/汇率指数不足")
    return LayerScore("rates_fx", _clip(float(np.mean(parts))), w, detail="; ".join(bits))


def score_sentiment(cfg: dict, as_of: str | None) -> LayerScore:
    w = float(cfg["weights"]["sentiment_policy"])
    s = _load_sentiment(as_of)
    if s is None:
        return LayerScore("sentiment_policy", None, w, skipped_reason="无 sentiment_cache")
    # map 0..100 cache to -100..100 centered at 50; extreme fear can be contrarian soft boost
    centered = (s - 50) * 1.6
    if s <= 25:
        centered = centered * 0.3 + 20
        detail = f"市场情绪分{s:.0f}（恐慌区，部分逆向加权）"
    elif s >= 85:
        centered = centered * 0.5 - 10
        detail = f"市场情绪分{s:.0f}（过热，抑制追涨）"
    else:
        detail = f"市场情绪分{s:.0f}"
    return LayerScore("sentiment_policy", _clip(centered), w, detail=detail)


def score_fundamental(cfg: dict, as_of: str | None = None) -> LayerScore:
    w = float(cfg["weights"]["fundamental"])
    df = _load_fundamentals_csv(cfg)
    if df is None:
        return LayerScore(
            "fundamental",
            None,
            w,
            skipped_reason="缺少 config/meituan_fundamentals.csv（可复制 example）",
        )
    row = fundamentals_row_as_of(df, as_of)
    if row is None:
        return LayerScore(
            "fundamental",
            None,
            w,
            skipped_reason="基本面 as_of 晚于评估日（等待季报）",
        )
    parts: list[float] = []
    bits: list[str] = [f"as_of={row.get('as_of')}"]
    mapping = [
        ("delivery_gtv_yoy", 120, "外卖GTV同比"),
        ("core_local_commerce_yoy", 100, "核心本地商业同比"),
        ("revenue_yoy", 80, "营收同比"),
        ("adjusted_ebitda_margin", 200, "经调EBITDA利润率"),
        ("operating_cashflow_yoy", 60, "经营现金流同比"),
    ]
    for col, mult, label in mapping:
        if col in row and pd.notna(row[col]):
            v = float(row[col])
            parts.append(_clip(v * mult))
            bits.append(f"{label}{v:.2%}" if abs(v) < 5 else f"{label}{v:.2f}")
    if "take_rate_bps" in row and pd.notna(row["take_rate_bps"]):
        tr = float(row["take_rate_bps"])
        parts.append(_clip((tr - 140) * 1.5))
        bits.append(f"抽成{tr:.0f}bps")
    if not parts:
        return LayerScore("fundamental", None, w, skipped_reason="基本面CSV无有效字段")
    return LayerScore("fundamental", _clip(float(np.mean(parts))), w, detail="; ".join(bits))


def score_regime(df: pd.DataFrame, cfg: dict) -> LayerScore:
    w = float(cfg["weights"]["regime"])
    if df.empty or len(df) < 60:
        return LayerScore("regime", None, w, skipped_reason="不足以判定体制")
    closes = df["close"]
    ma20 = closes.rolling(20).mean()
    ma60 = closes.rolling(60).mean()
    vol = closes.pct_change().rolling(20).std()
    last = float(closes.iloc[-1])
    m20, m60 = float(ma20.iloc[-1]), float(ma60.iloc[-1])
    v = float(vol.iloc[-1]) if not pd.isna(vol.iloc[-1]) else 0.02
    if last > m20 > m60 and v < 0.035:
        regime = "TREND_UP"
        score = 25.0
    elif last < m20 < m60 and v < 0.035:
        regime = "TREND_DOWN"
        score = -25.0
    elif v >= 0.045:
        regime = "VOLATILE"
        score = -10.0
    else:
        regime = "MEAN_REVERT"
        # favor mild rebound setup when below ma20 but not cascading
        score = 10.0 if last < m20 else 0.0
    return LayerScore("regime", score, w, detail=f"体制={regime}; MA20={m20:.2f} MA60={m60:.2f} σ20={v:.2%}")


# ── composite ──────────────────────────────────────────────────────────


def _label_signal(composite: Optional[float], cfg: dict) -> str:
    if composite is None:
        return "数据不足"
    th = cfg["thresholds"]
    if composite >= th["strong_long"]:
        return "强多头反转/上行共振"
    if composite >= th["lean_long"]:
        return "偏多（观察确认）"
    if composite <= th["strong_short"]:
        return "强空头反转/下行共振"
    if composite <= th["lean_short"]:
        return "偏空（观察确认）"
    return "中性/高不确定"


def compose(layers: list[LayerScore]) -> Optional[float]:
    active = [x for x in layers if x.active]
    if not active:
        return None
    tw = sum(x.weight for x in active)
    if tw <= 0:
        return None
    return _clip(sum(float(x.score) * x.weight for x in active) / tw)


def run_meituan_reversal(
    as_of: str = "",
    demo: bool = False,
    config_name: str = "meituan_reversal.yaml",
) -> MeituanReversalResult:
    cfg = load_config(config_name)
    symbol = cfg["symbol"]
    name = cfg.get("name", symbol)
    as_of_n = as_of.strip() or None
    notes: list[str] = []

    if demo:
        df = _synthetic_ohlcv()
        notes.append("demo=1：使用合成OHLCV，宏观/资金等真实层仍尽量读取")
    else:
        df = _load_ohlcv(symbol, as_of_n, int(cfg["lookbacks"]["price_bars"]))
        if df.empty:
            notes.append("本地无美团日线，可加 --demo 做通路验证，或先同步港股行情")

    layers = [
        score_technical(df, cfg),
        score_relative(df, cfg, as_of_n) if not demo else LayerScore(
            "relative", None, float(cfg["weights"]["relative"]), skipped_reason="demo跳过相对强弱"
        ),
        score_capital(cfg, as_of_n),
        score_macro(cfg),
        score_rates_fx(cfg, as_of_n),
        score_sentiment(cfg, as_of_n),
        score_fundamental(cfg, as_of_n),
        score_regime(df, cfg),
    ]
    composite = compose(layers)
    regime_layer = next((x for x in layers if x.name == "regime"), None)
    regime = "UNKNOWN"
    if regime_layer and regime_layer.detail.startswith("体制="):
        regime = regime_layer.detail.split(";")[0].replace("体制=", "")

    as_of_out = as_of_n or (str(df["trade_date"].iloc[-1]) if not df.empty else datetime.now().strftime("%Y-%m-%d"))
    last_close = float(df["close"].iloc[-1]) if not df.empty else None
    return MeituanReversalResult(
        as_of=as_of_out,
        symbol=symbol,
        name=name,
        last_close=last_close,
        composite=composite,
        regime=regime,
        signal=_label_signal(composite, cfg),
        layers=layers,
        notes=notes,
        disclaimer=str(cfg.get("disclaimer", "")).strip(),
    )


def render_markdown(res: MeituanReversalResult) -> str:
    lines = [
        f"# 美团反转指标 {res.as_of}",
        "",
        f"- 标的: **{res.name}** (`{res.symbol}`)  收盘: **{res.last_close if res.last_close is not None else 'n/a'}**",
        f"- 综合分: **{res.composite if res.composite is not None else 'n/a'}** / [-100,+100]",
        f"- 信号: **{res.signal}**",
        f"- 体制: **{res.regime}**",
        "",
        "| 层级 | 分数 | 权重 | 说明 |",
        "|---|---:|---:|---|",
    ]
    for ly in res.layers:
        sc = f"{ly.score:.1f}" if ly.score is not None else "skip"
        reason = ly.skipped_reason or ly.detail
        lines.append(f"| {ly.name} | {sc} | {ly.weight:.0f} | {reason} |")
    if res.notes:
        lines += ["", "## Notes"] + [f"- {n}" for n in res.notes]
    if res.disclaimer:
        lines += ["", f"> {res.disclaimer}"]
    return "\n".join(lines) + "\n"



# ── alerts & lightweight walk-forward ──────────────────────────────────


@dataclass
class AlertEvent:
    trade_date: str
    composite: float
    signal: str
    last_close: float
    fwd_5d: Optional[float] = None
    fwd_10d: Optional[float] = None


def classify_alert(composite: Optional[float], cfg: dict | None = None) -> Optional[str]:
    """Return alert tag when score crosses lean thresholds."""
    if composite is None:
        return None
    cfg = cfg or load_config()
    th = cfg["thresholds"]
    if composite >= th["strong_long"]:
        return "STRONG_LONG"
    if composite >= th["lean_long"]:
        return "LEAN_LONG"
    if composite <= th["strong_short"]:
        return "STRONG_SHORT"
    if composite <= th["lean_short"]:
        return "LEAN_SHORT"
    return None


def backtest_meituan_reversal(
    start: str = "",
    end: str = "",
    hold_days: int = 5,
    long_threshold: Optional[float] = None,
    short_threshold: Optional[float] = None,
    config_name: str = "meituan_reversal.yaml",
    max_points: int = 80,
) -> dict[str, Any]:
    """Event-style evaluation of composite on Meituan daily bars.

    For each sampled date, score with as_of=date (fundamentals PIT), then
    measure forward close-to-close return over hold_days.
    Long alerts expect positive fwd; short alerts expect negative.
    """
    cfg = load_config(config_name)
    th = cfg["thresholds"]
    long_th = float(long_threshold if long_threshold is not None else th["lean_long"])
    short_th = float(short_threshold if short_threshold is not None else th["lean_short"])
    symbol = cfg["symbol"]

    df = _load_ohlcv(symbol, end or None, 400)
    if df.empty:
        return {"error": "no price data", "events": [], "summary": {}}
    df = df.sort_values("trade_date").reset_index(drop=True)
    if start:
        df = df[df["trade_date"] >= start].reset_index(drop=True)
    if end:
        df = df[df["trade_date"] <= end].reset_index(drop=True)
    if len(df) < 40:
        return {"error": "insufficient bars", "events": [], "summary": {}}

    # sample dates (stride) to keep runtime bounded
    stride = max(1, len(df) // max_points)
    dates = list(df["trade_date"].iloc[30:-hold_days:stride])
    events: list[AlertEvent] = []
    closes = df.set_index("trade_date")["close"]

    for d in dates:
        res = run_meituan_reversal(as_of=str(d), demo=False, config_name=config_name)
        if res.composite is None or res.last_close is None:
            continue
        if not (res.composite >= long_th or res.composite <= short_th):
            continue
        try:
            idx = closes.index.get_loc(d)
        except KeyError:
            continue
        if isinstance(idx, slice):
            continue
        # get_loc may return int
        i = int(idx)
        if i + hold_days >= len(closes):
            fwd = None
        else:
            c0 = float(closes.iloc[i])
            c1 = float(closes.iloc[i + hold_days])
            fwd = c1 / c0 - 1
        events.append(
            AlertEvent(
                trade_date=str(d),
                composite=float(res.composite),
                signal=res.signal,
                last_close=float(res.last_close),
                fwd_5d=fwd if hold_days == 5 else None,
                fwd_10d=fwd if hold_days == 10 else None,
            )
        )
        # store generic fwd on both if hold_days other
        if hold_days not in (5, 10) and fwd is not None:
            setattr(events[-1], "fwd_Nd", fwd)

    # attach fwd into a uniform field
    rows = []
    for e in events:
        fwd = e.fwd_5d if hold_days == 5 else (e.fwd_10d if hold_days == 10 else getattr(e, "fwd_Nd", None))
        # recompute if missing
        if fwd is None:
            try:
                i = list(closes.index).index(e.trade_date)
                c0 = float(closes.iloc[i])
                c1 = float(closes.iloc[i + hold_days])
                fwd = c1 / c0 - 1
            except Exception:
                fwd = None
        rows.append(
            {
                "trade_date": e.trade_date,
                "composite": round(e.composite, 2),
                "signal": e.signal,
                "close": e.last_close,
                "fwd": None if fwd is None else round(fwd, 4),
            }
        )

    long_rows = [r for r in rows if r["composite"] >= long_th and r["fwd"] is not None]
    short_rows = [r for r in rows if r["composite"] <= short_th and r["fwd"] is not None]
    def _win(rs: list[dict], expect_pos: bool) -> dict[str, Any]:
        if not rs:
            return {"n": 0, "win_rate": None, "avg_fwd": None}
        fwds = [r["fwd"] for r in rs]
        wins = sum(1 for x in fwds if (x > 0) == expect_pos) if True else 0
        # for long: win if fwd>0; for short: win if fwd<0
        if expect_pos:
            wins = sum(1 for x in fwds if x > 0)
        else:
            wins = sum(1 for x in fwds if x < 0)
        return {"n": len(fwds), "win_rate": round(wins / len(fwds), 3), "avg_fwd": round(float(np.mean(fwds)), 4)}

    summary = {
        "hold_days": hold_days,
        "long_threshold": long_th,
        "short_threshold": short_th,
        "long": _win(long_rows, True),
        "short": _win(short_rows, False),
        "n_events": len(rows),
        "sample_note": "线性加权综合分事件回测；非完整交易系统，忽略成本/涨跌停",
    }
    return {"summary": summary, "events": rows}
