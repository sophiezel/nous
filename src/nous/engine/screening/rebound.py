"""rebound.py — 短期反弹选股引擎（超跌反弹族 + 强势回调反包族）

依据规格（docs/superpowers/specs/）:
  - 2026-08-24-rebound-factor-signal-design.md    #7 因子清单与买卖点
  - 2026-08-24-rebound-weight-calibration-design.md #8 权重体系
  - 2026-08-24-rebound-risk-gate-design.md        #9 止损止盈与闸门

设计要点（对应规格）:
  - 两信号族: 超跌族（均值回归，方新侠语义） / 反包族（续攻，徐翔/赵老哥/作手新一语义）
  - 硬过滤 6 条 → 族触发 → 加权排序（config/rebound_weights.yaml）→ 三层闸门
  - 买入=次日开盘（本模块只出信号，不含成交）；止损锚点用买入日（执行层落实）
  - 全部因子窗口 <=20 日（全库未复权，规避除权失真）
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ── 路径 ─────────────────────────────────────────────
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# 仓库根：从模块位置向上找到含 config/rebound_weights.yaml 的目录
def _find_repo_root() -> Path:
    env_dir = os.environ.get("NOUS_CONFIG_DIR", "")
    if env_dir and (Path(env_dir) / "rebound_weights.yaml").exists():
        return Path(env_dir).parent
    cur = Path(__file__).resolve()
    for _ in range(6):
        if (cur / "config" / "rebound_weights.yaml").exists():
            return cur
        cur = cur.parent
    return cur

PROJECT_ROOT = _find_repo_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import yaml

from nous.data.storage import get_db

# ══════════════════════════════════════════════════════
# 配置加载
# ══════════════════════════════════════════════════════

def _load_yaml(name: str) -> dict:
    """从 config/ 加载 yaml，缺失或解析失败返回 {}。"""
    for base in (Path(os.environ.get("NOUS_CONFIG_DIR", PROJECT_ROOT / "config")), PROJECT_ROOT / "config"):
        p = Path(base) / name
        if p.exists():
            try:
                return yaml.safe_load(p.read_text()) or {}
            except Exception:
                pass
    return {}


WEIGHTS = _load_yaml("rebound_weights.yaml").get("rebound", {}).get("weights", {}) or {
    "oversold": {"ret_5d": 30, "price_position_20": 20, "ma_gap_20": 15, "stabilize": 5,
                 "volume_dry": 20, "lhb_net": 7, "sector_heat": 3},
    "strong": {"limit_up_streak": 40, "volume_accel": 30, "lhb_net": 20, "sector_heat": 10},
}
RISK = _load_yaml("rebound_risk.yaml").get("rebound", {}).get("risk", {}) or {}


def _risk(*keys, default):
    cur = RISK
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


# ══════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════

@dataclass
class EntrySignal:
    symbol: str
    name: str
    family: str            # "oversold" | "strong"
    trigger: str           # 命中触发描述
    score: float           # 0-100 加权排序分
    confidence: float      # 0-100
    stop_loss: float       # 预估止损价（执行层以买入日实际低点/ATR 落实）
    take_profit_first: float  # 首目标价（+5%/+8%）
    trail_drawdown: float  # 移动止盈回撤
    time_stop_days: int    # 时间止损 N 日
    position_pct: float    # 单票仓位上限
    detail: str = ""


@dataclass
class ReboundResult:
    report_date: str
    regime: str = "SIDEWAYS"
    sentiment_status: str = "cool"
    position_cap: float = 0.6
    limit_down_alert: bool = False
    candidates: list = field(default_factory=list)   # EntrySignal 已按分数降序
    oversold_count: int = 0
    strong_count: int = 0
    skipped: dict = field(default_factory=dict)      # 过滤统计

    def top(self, n: int = 20) -> list:
        return self.candidates[:n]


# ══════════════════════════════════════════════════════
# 工具函数（纯计算，无 DB）
# ══════════════════════════════════════════════════════

def _get_limit_pct(symbol: str, name: str = "") -> float:
    """涨跌幅限制: 30/68 开头 20%，8/4 开头 30%，ST 5%，其余 10%。"""
    if "ST" in name.upper():
        return 0.05
    if symbol.startswith(("30", "68")):
        return 0.20
    if symbol.startswith(("8", "4")):
        return 0.30
    return 0.10


def _is_limit_up(close: float, prev_close: float, limit: float) -> bool:
    if prev_close <= 0:
        return False
    return close >= prev_close * (1 + limit) * 0.995


def _compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = np.diff(np.asarray(closes, dtype=float))
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _compute_ma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return float(np.mean(closes[-period:]))


def _compute_atr(bars: list[dict], period: int = 14) -> Optional[float]:
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return float(np.mean(trs[-period:]))


def _compute_volume_ratio(volumes: list[float]) -> Optional[float]:
    if len(volumes) < 6:
        return None
    base = float(np.mean(volumes[-6:-1]))
    if base <= 0:
        return None
    return volumes[-1] / base


def _consecutive_limit_up(bars: list[dict], limit: float) -> int:
    """以最新 bar 为终点往前数连续涨停天数。"""
    n = 0
    for i in range(len(bars) - 1, 0, -1):
        if _is_limit_up(bars[i]["close"], bars[i - 1]["close"], limit):
            n += 1
        else:
            break
    return n


# ══════════════════════════════════════════════════════
# 数据访问
# ══════════════════════════════════════════════════════

class ReboundEngine:
    def __init__(self, report_date: str = "", n_bars: int = 40, memory: Optional[dict] = None):
        """
        memory=None: DB 模式（读 screener.db）。
        memory=dict: 内存模式（回测用），键:
          bars / names / lhb_net5 / sector_heat / total_mv / listing_dates /
          industry_map / sentiment_map / regime_map / limit_down_map
          其中 *_map 为 {日期: 值}，用于回测日循环避免反复查库。
        """
        self.conn = None
        self.n_bars = n_bars
        self.bars: dict[str, list[dict]] = {}
        self.names: dict[str, str] = {}
        self.lhb_net5: dict[str, float] = {}
        self.sector_heat: dict[str, float] = {}
        self.total_mv: dict[str, float] = {}
        self.listing_dates: dict[str, str] = {}
        self._industry_map: Optional[dict] = None
        self._sentiment_map: Optional[dict] = None
        self._regime_map: Optional[dict] = None
        self._limit_down_map: Optional[dict] = None
        if memory is not None:
            self._init_memory(memory, report_date)
        else:
            self.report_date = report_date or self._latest_date()
            self.conn = get_db(write=False)
            self._load()

    def _init_memory(self, memory: dict, report_date: str) -> None:
        self.bars = memory.get("bars", {})
        self.names = memory.get("names", {})
        self.lhb_net5 = memory.get("lhb_net5", {})
        self.sector_heat = memory.get("sector_heat", {})
        self.total_mv = memory.get("total_mv", {})
        self.listing_dates = memory.get("listing_dates", {})
        self._industry_map = memory.get("industry_map")
        self._sentiment_map = memory.get("sentiment_map")
        self._regime_map = memory.get("regime_map")
        self._limit_down_map = memory.get("limit_down_map")
        if report_date:
            self.report_date = report_date
        else:
            d = max((b[-1]["date"] for b in self.bars.values() if b), default=date.today().isoformat())
            self.report_date = d
        if not self.sector_heat and self._industry_map is not None:
            self._compute_sector_heat()

    # ── 基础加载 ────────────────────────────────────
    def _latest_date(self) -> str:
        row = self.conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()
        return row[0] if row and row[0] else date.today().isoformat()

    def _load(self) -> None:
        # 最近 n_bars 个交易日的日线（单查询，避免 short_term.py 的窗口 bug: 升序 LIMIT 取到最早 N 天）
        dates = [r[0] for r in self.conn.execute(
            "SELECT trade_date FROM stock_daily WHERE trade_date<=? "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?",
            (self.report_date, self.n_bars))]
        if not dates:
            return
        cutoff = dates[-1]
        rows = self.conn.execute(
            "SELECT symbol, trade_date, open, high, low, close, volume, amount FROM stock_daily "
            "WHERE trade_date >= ? AND trade_date <= ?", (cutoff, self.report_date)).fetchall()
        for symbol, td, o, h, l, c, v, amt in rows:
            self.bars.setdefault(symbol, []).append(
                {"date": td, "open": float(o or 0), "high": float(h or 0), "low": float(l or 0),
                 "close": float(c or 0), "volume": float(v or 0), "amount": float(amt or 0)})
        for sym in self.bars:
            self.bars[sym].sort(key=lambda b: b["date"])

        # 名称（A股池）
        for symbol, name in self.conn.execute(
                "SELECT symbol, name FROM stock_basic WHERE market='a'"):
            self.names[symbol] = name or ""

        # 龙虎榜近5日净买（截止报告日）
        lhb_dates = [r[0] for r in self.conn.execute(
            "SELECT trade_date FROM lhb_daily WHERE trade_date<=? "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5", (self.report_date,))]
        if lhb_dates:
            lhb_from = lhb_dates[-1]
            for symbol, net in self.conn.execute(
                    "SELECT symbol, SUM(net_amount) FROM lhb_daily WHERE trade_date >= ? "
                    "AND trade_date <= ? GROUP BY symbol", (lhb_from, self.report_date)):
                if net:
                    self.lhb_net5[symbol] = float(net)

        # 总市值（当前快照，仅徐翔式成交额/总市值用）
        for symbol, mv in self.conn.execute("SELECT symbol, total_mv FROM stock_fundamental"):
            if mv:
                self.total_mv[symbol] = float(mv)

        self._load_sector_heat()

    def _compute_sector_heat(self) -> None:
        """板块热度 = 同 industry_l2 股票近5日平均收益（当日截面，无 point-in-time，仅当日选股用）。"""
        ret5: dict[str, float] = {}
        for sym, bars in self.bars.items():
            if len(bars) >= 6 and bars[-1]["close"] > 0 and bars[-6]["close"] > 0:
                ret5[sym] = bars[-1]["close"] / bars[-6]["close"] - 1.0
        # 行业映射（当前快照）
        if self._industry_map is None:
            ind: dict[str, str] = {}
            for symbol, i2 in self.conn.execute(
                    "SELECT symbol, industry_l2 FROM stock_industry_multilevel WHERE is_current=1"):
                if i2:
                    ind[symbol] = i2
            self._industry_map = ind
        ind = self._industry_map
        from collections import defaultdict
        sums, cnts = defaultdict(float), defaultdict(int)
        for sym, sec in ind.items():
            if sym in ret5:
                sums[sec] += ret5[sym]
                cnts[sec] += 1
        sec_mean = {s: sums[s] / cnts[s] for s in sums if cnts[s] > 0}
        self.sector_heat = {sym: sec_mean.get(sec, 0.0) for sym, sec in ind.items() if sym in ret5}

    def _load_sector_heat(self) -> None:
        self._compute_sector_heat()

    # ── 市场状态 ────────────────────────────────────
    def _get_sentiment_status(self) -> str:
        """情绪 gate（sentiment_cache.score 阈值，配置可调）。"""
        if self._sentiment_map is not None:
            score = int(self._sentiment_map.get(self.report_date, 0) or 0)
        else:
            row = self.conn.execute(
                "SELECT score FROM sentiment_cache WHERE date<=? ORDER BY date DESC LIMIT 1",
                (self.report_date,)).fetchone()
            score = int(row[0]) if row and row[0] else 0
        th = _risk("gate", "sentiment_thresholds", default={"hot": 75, "warm": 60, "cool": 40})
        if score >= th.get("hot", 75):
            return "hot"
        if score >= th.get("warm", 60):
            return "warm"
        if score >= th.get("cool", 40):
            return "cool"
        return "cold"

    def _get_regime(self) -> str:
        """regime（规则版）: 复用 market_regime.label_regime（沪深300 20日涨+波动率，透明规则）。"""
        if self._regime_map is not None:
            return self._regime_map.get(self.report_date, "SIDEWAYS")
        try:
            rows = self.conn.execute(
                "SELECT trade_date, close FROM index_daily WHERE symbol='IDX_000300' "
                "AND trade_date<=? ORDER BY trade_date DESC LIMIT 60", (self.report_date,)).fetchall()
            if len(rows) < 25:
                return "SIDEWAYS"
            rows = list(reversed(rows))
            import pandas as pd
            from nous.engine.ml.market_regime import label_regime
            df = pd.DataFrame(rows, columns=["trade_date", "close"])
            out = label_regime(df, confirm_days=3)
            return str(out.iloc[-1]["regime"])
        except Exception:
            return "SIDEWAYS"

    def _get_limit_down_count(self, day: str) -> int:
        """当日跌停家数（推断：跌幅 <= -涨停线）。"""
        if self._limit_down_map is not None:
            return int(self._limit_down_map.get(day, 0) or 0)
        n = 0
        for sym, sym_bars in self.bars.items():
            if len(sym_bars) < 2:
                continue
            last = sym_bars[-1]
            if last["date"] != day:
                continue
            prev = sym_bars[-2]["close"]
            if prev <= 0:
                continue
            pct = last["close"] / prev - 1.0
            limit = _get_limit_pct(sym, self.names.get(sym, ""))
            if pct <= -limit * 0.995:
                n += 1
        return n

    # ── 硬过滤 ──────────────────────────────────────
    def _pass_hard_filters(self, symbol: str) -> Optional[str]:
        """返回 None=通过，否则返回被拒原因。"""
        name = self.names.get(symbol, "")
        bars = self.bars.get(symbol)
        if not bars:
            return "no_data"
        last = bars[-1]
        # F1 ST
        if "ST" in name.upper():
            return "F1_ST"
        # F2 停牌（最新bar非报告日 或 当日无成交）
        if last["date"] != self.report_date or last["volume"] <= 0:
            return "F2_suspended"
        # F6 20%涨跌幅标的（可选）
        limit = _get_limit_pct(symbol, name)
        if not _risk("hard_filter", "allow_20pct_board", default=True) and limit >= 0.20:
            return "F6_20pct_board"
        # F3 一字涨停不可成交
        if last["close"] > 0 and len(bars) >= 2:
            if _is_limit_up(last["close"], bars[-2]["close"], limit) and \
               last["open"] == last["high"] == last["low"] == last["close"]:
                return "F3_limit_up_one_word"
        # F4 流动性：近5日日均成交额
        amt5 = [b["amount"] for b in bars[-5:]] if len(bars) >= 5 else [b["amount"] for b in bars]
        if amt5 and float(np.mean(amt5)) < _risk("hard_filter", "min_daily_amount_5d", default=50_000_000):
            return "F4_liquidity"
        return None

    def _pass_listing_days(self, symbol: str) -> bool:
        """F5 上市天数 >= N（用 stock_daily_all 最早 trade_date 近似）。"""
        min_days = _risk("hard_filter", "min_listing_days", default=60)
        if symbol not in self.listing_dates:
            row = self.conn.execute(
                "SELECT MIN(trade_date) FROM stock_daily_all WHERE symbol=?", (symbol,)).fetchone()
            self.listing_dates[symbol] = row[0] if row and row[0] else ""
        first = self.listing_dates[symbol]
        if not first:
            return True
        try:
            d0 = datetime.strptime(first, "%Y-%m-%d")
            d1 = datetime.strptime(self.report_date, "%Y-%m-%d")
            return (d1 - d0).days >= min_days
        except Exception:
            return True

    # ── 族触发 ──────────────────────────────────────
    def _oversold_trigger(self, symbol: str) -> Optional[str]:
        """超跌族硬触发（方新侠语义，全部满足）: 连跌N日 + RSI<M + 阳线 + 量比>1.5（N/M 可配置）"""
        trig = _risk("quality", "oversold_trigger", default={"min_consecutive_drops": 3, "max_rsi": 30})
        drops = int(trig.get("min_consecutive_drops", 3))
        max_rsi = float(trig.get("max_rsi", 30))
        bars = self.bars[symbol]
        closes = [b["close"] for b in bars]
        if len(closes) < 16:
            return None
        # 连跌 N 日（近 N 日日收益均<0）
        for i in range(1, drops + 1):
            if closes[-i] >= closes[-i - 1]:
                return None
        # RSI < max_rsi
        rsi = _compute_rsi(closes)
        if rsi is None or rsi >= max_rsi:
            return None
        # 阳线
        if bars[-1]["close"] <= bars[-1]["open"]:
            return None
        # 量比 > 1.5
        vr = _compute_volume_ratio([b["volume"] for b in bars])
        if vr is None or vr <= 1.5:
            return None
        return f"连跌{drops}日 RSI{rsi:.0f}<{max_rsi:.0f} 阳线 量比{vr:.1f}>1.5"

    def _strong_triggers(self, symbol: str) -> Optional[str]:
        """反包族触发（任一命中）: 徐翔式 / 赵老哥式 / 作手新一式"""
        bars = self.bars[symbol]
        name = self.names.get(symbol, "")
        limit = _get_limit_pct(symbol, name)
        if len(bars) < 22:
            return None
        hits = []
        # 徐翔式: 昨日涨停 + 今日高开>3% + 成交额/总市值>0.5%
        if len(bars) >= 3:
            y_close, y_prev = bars[-2]["close"], bars[-3]["close"]
            if _is_limit_up(y_close, y_prev, limit) and bars[-1]["open"] > y_close * 1.03:
                mv = self.total_mv.get(symbol, 0)
                if mv > 0 and bars[-1]["amount"] / mv > 0.005:
                    hits.append("徐翔式(昨涨停+高开)")
                elif mv <= 0:
                    hits.append("徐翔式(昨涨停+高开,无市值校验)")
        # 赵老哥式: 首板(3日前)放量>=2倍 + 二板(2日前)涨停缩量<0.8
        if len(bars) >= 5:
            b1 = bars[-3]; b1_prev = bars[-4]
            b2 = bars[-2]
            if _is_limit_up(b1["close"], b1_prev["close"], limit) and _is_limit_up(b2["close"], b1["close"], limit):
                base_vol = float(np.mean([b["volume"] for b in bars[-8:-3]])) if len(bars) >= 8 else 0
                if base_vol > 0 and b1["volume"] / base_vol >= 2.0 and b2["volume"] / b1["volume"] < 0.8:
                    hits.append("赵老哥式(首板放量+二板缩量)")
        # 作手新一式: 今日涨幅>5% + 量比>2 + close>MA20
        closes = [b["close"] for b in bars]
        ma20 = _compute_ma(closes, 20)
        vr = _compute_volume_ratio([b["volume"] for b in bars])
        if len(closes) >= 2 and ma20:
            ret_today = closes[-1] / closes[-2] - 1.0
            if ret_today > 0.05 and vr and vr > 2.0 and closes[-1] > ma20:
                hits.append(f"作手新一式(涨{ret_today:.1%}+量比{vr:.1f}+站上MA20)")
        return "; ".join(hits) if hits else None

    # ── 评分 ────────────────────────────────────────
    def _family_scores(self, family: str, symbols: list[str]) -> dict[str, dict]:
        """对族内候选计算各因子原始值 → 池内分位归一化 [0,1] → 加权总分 [0,100]。"""
        weights = WEIGHTS.get(family, {})
        raw: dict[str, dict[str, float]] = {}
        for sym in symbols:
            bars = self.bars[sym]
            closes = [b["close"] for b in bars]
            vols = [b["volume"] for b in bars]
            f: dict[str, float] = {}
            if family == "oversold":
                f["ret_5d"] = closes[-1] / closes[-6] - 1.0 if len(closes) >= 6 else 0.0
                lo20 = min(b["low"] for b in bars[-20:])
                hi20 = max(b["high"] for b in bars[-20:])
                f["price_position_20"] = (closes[-1] - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5
                ma20 = _compute_ma(closes, 20) or closes[-1]
                f["ma_gap_20"] = (closes[-1] - ma20) / ma20
                vr = _compute_volume_ratio(vols) or 1.0
                f["volume_dry"] = vr  # 越小越缩量
                prev5_low = min(b["low"] for b in bars[-6:-1]) if len(bars) >= 6 else closes[-1]
                f["stabilize"] = 1.0 if closes[-1] > prev5_low else 0.0
                # 反弹弹性（AlphaReversal 启发）: 近20日最大单日涨幅 / 最大单日跌幅绝对值
                gains, losses = [], []
                for i in range(1, min(21, len(closes))):
                    r = closes[-i] / closes[-i - 1] - 1.0
                    (gains if r > 0 else losses).append(abs(r))
                max_gain = max(gains) if gains else 0.0
                max_loss = max(losses) if losses else 0.0
                f["bounce_elasticity"] = (max_gain / max_loss) if max_loss > 0 else 0.5
            else:
                f["limit_up_streak"] = float(_consecutive_limit_up(bars, _get_limit_pct(sym, self.names.get(sym, ""))))
                f["volume_accel"] = _compute_volume_ratio(vols) or 1.0
            f["lhb_net"] = abs(self.lhb_net5.get(sym, 0.0))
            f["sector_heat"] = self.sector_heat.get(sym, 0.0)
            raw[sym] = f

        # 分位归一化
        def _norm(vals: list[float], lower_better: bool) -> dict[str, float]:
            arr = np.asarray(vals, dtype=float)
            if len(arr) == 0 or np.ptp(arr) == 0:
                return {s: 0.5 for s in symbols}
            mn, mx = float(np.percentile(arr, 5)), float(np.percentile(arr, 95))
            span = (mx - mn) or 1e-9
            out = {}
            for i, s in enumerate(symbols):
                v = np.clip((arr[i] - mn) / span, 0.0, 1.0)
                out[s] = v if not lower_better else 1.0 - v
            return out

        scores: dict[str, float] = {}
        factor_scores: dict[str, dict[str, float]] = {}
        for fname, w in weights.items():
            lower_better = fname in ("ret_5d", "price_position_20", "ma_gap_20", "volume_dry")
            if fname == "stabilize":
                ns = {s: raw[s].get("stabilize", 0.0) for s in symbols}
            else:
                ns = _norm([raw[s].get(fname, 0.0) for s in symbols], lower_better)
            factor_scores[fname] = ns
            for s in symbols:
                scores[s] = scores.get(s, 0.0) + w * ns[s]
        total_w = sum(weights.values()) or 1.0
        return {s: {"score": 100.0 * scores[s] / total_w,
                    "factors": {k: round(v[s], 3) for k, v in factor_scores.items()}} for s in symbols}

    # ── 主扫描 ──────────────────────────────────────
    def scan(self) -> ReboundResult:
        res = ReboundResult(report_date=self.report_date)
        res.regime = self._get_regime()
        res.sentiment_status = self._get_sentiment_status()
        res.position_cap = float(_risk("gate", "regime_position_limit", default={}).get(res.regime, 0.6))
        res.limit_down_alert = self._get_limit_down_count(self.report_date) > _risk(
            "gate", "exit_limit_down_count", default=200)

        # 闸门开关
        block_oversold = res.regime in _risk("gate", "block_oversold_regimes", default=["BEAR", "VOLATILE"])
        block_strong = res.sentiment_status in _risk("gate", "block_strong_sentiment", default=["cold", "cool"])

        oversold_pool, strong_pool = [], []
        for symbol in self.bars:
            if not self.bars[symbol]:
                continue
            if symbol not in self.names:
                continue
            if self.bars[symbol][-1]["date"] != self.report_date:
                continue
            reason = self._pass_hard_filters(symbol)
            if reason:
                res.skipped[reason] = res.skipped.get(reason, 0) + 1
                continue
            if not self._pass_listing_days(symbol):
                res.skipped["F5_listing"] = res.skipped.get("F5_listing", 0) + 1
                continue
            if not block_oversold:
                t = self._oversold_trigger(symbol)
                if t:
                    oversold_pool.append((symbol, t))
            if not block_strong:
                t = self._strong_triggers(symbol)
                if t:
                    strong_pool.append((symbol, t))

        # 评分
        if oversold_pool:
            syms = [s for s, _ in oversold_pool]
            sc = self._family_scores("oversold", syms)
            for symbol, trig in oversold_pool:
                info = sc[symbol]
                conf = min(85.0, 40.0 + (30 - (self._rsi_now(symbol) or 30)) * 2 + info["score"] * 0.3)
                if res.sentiment_status in ("cold", "cool"):
                    conf = min(100.0, conf + 10)
                sig = self._make_signal(symbol, "oversold", trig, info["score"], conf)
                res.candidates.append(sig)
            res.oversold_count = len(oversold_pool)

        if strong_pool:
            syms = [s for s, _ in strong_pool]
            sc = self._family_scores("strong", syms)
            for symbol, trig in strong_pool:
                info = sc[symbol]
                conf = min(90.0, 60.0 + info["score"] * 0.3)
                if res.sentiment_status in ("hot", "warm"):
                    conf = min(100.0, conf + 5)
                sig = self._make_signal(symbol, "strong", trig, info["score"], conf)
                res.candidates.append(sig)
            res.strong_count = len(strong_pool)

        res.candidates.sort(key=lambda s: s.score, reverse=True)
        res.candidates = res.candidates[: _risk("position", "max_daily_picks", default=5) * 4]
        return res

    def _rsi_now(self, symbol: str) -> Optional[float]:
        return _compute_rsi([b["close"] for b in self.bars[symbol]])

    def _make_signal(self, symbol: str, family: str, trigger: str, score: float, confidence: float) -> EntrySignal:
        bars = self.bars[symbol]
        close = bars[-1]["close"]
        atr = _compute_atr(bars) or close * 0.03
        risk = RISK
        stop_loss, first_pct = close, 0.05
        if family == "oversold":
            first_pct = float(_risk("take_profit", "oversold_first", default=0.05))
            stop_loss = close - atr * float(_risk("stop_loss", "atr_multiplier", default=2.0))
            time_days = int(_risk("time_stop", "oversold_days", default=5))
        else:
            first_pct = float(_risk("take_profit", "strong_first", default=0.08))
            ma20 = _compute_ma([b["close"] for b in bars], 20) or close
            stop_loss = min(close * (1 - float(_risk("stop_loss", "strong_family_pct", default=0.05))), ma20)
            time_days = int(_risk("time_stop", "strong_days", default=3))
        return EntrySignal(
            symbol=symbol, name=self.names.get(symbol, ""), family=family, trigger=trigger,
            score=round(float(score), 1), confidence=round(float(confidence), 1),
            stop_loss=round(stop_loss, 2),
            take_profit_first=round(close * (1 + first_pct), 2),
            trail_drawdown=float(_risk("take_profit", "trail_drawdown", default=0.04)),
            time_stop_days=time_days,
            position_pct=float(_risk("position", "max_single_pct", default=0.15)),
            detail=f"信号日收盘 {close:.2f}",
        )

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


def run_rebound(report_date: str = "") -> ReboundResult:
    engine = ReboundEngine(report_date=report_date)
    try:
        return engine.scan()
    finally:
        engine.close()


# ══════════════════════════════════════════════════════
# 报告与追踪
# ══════════════════════════════════════════════════════

FAMILY_CN = {"oversold": "超跌反弹", "strong": "强势反包"}


def render_markdown(res: ReboundResult) -> str:
    """每日反弹报告（Markdown）。"""
    lines = [
        f"# 短期反弹选股报告 — {res.report_date}",
        "",
        f"- 市场 regime: **{res.regime}**（总仓位上限 {res.position_cap:.0%}）",
        f"- 情绪: **{res.sentiment_status}**（sentiment_cache）",
        f"- 超跌族候选: {res.oversold_count} 只 | 反包族候选: {res.strong_count} 只",
        f"- 极端警报: {'⚠️ 单日跌停家数超阈值（降仓/清仓信号）' if res.limit_down_alert else '无'}",
        "",
        "## 候选列表（按得分降序）",
        "",
        "| 族 | 代码 | 名称 | 得分 | 置信 | 触发 | 止损(预估) | 首目标 | 时间止损 | 仓位 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in res.candidates:
        lines.append(
            f"| {FAMILY_CN.get(s.family, s.family)} | {s.symbol} | {s.name} | {s.score:.1f} | "
            f"{s.confidence:.1f} | {s.trigger} | {s.stop_loss:.2f} | {s.take_profit_first:.2f} | "
            f"{s.time_stop_days}日 | {s.position_pct:.0%} |"
        )
    lines += ["", "## 执行纪律（规格 #9）", "", "- 买入：次日开盘（信号日收盘后计算，零前视）",
              "- 止损：超跌族=破买入日最低价（<1% 则放宽到 买入价−2×ATR）；反包族=破 MA20 或 −5%",
              "- 止盈：首目标出 1/2，剩余收盘创新高回撤 4% 移动止盈",
              f"- 时间止损：超跌族 {int(_risk('time_stop', 'oversold_days', default=5))} 日 / 反包族 {int(_risk('time_stop', 'strong_days', default=3))} 日，累计 <+2% 收盘走",
              f"- 仓位：单票 ≤{_risk('position', 'max_single_pct', default=0.15):.0%}、单板块 ≤{_risk('position', 'max_sector_pct', default=0.30):.0%}、单日 ≤{_risk('position', 'max_daily_picks', default=5)} 只",
              "", f"*Nous rebound 引擎 · {datetime.now().strftime('%Y-%m-%d %H:%M')}*", ""]
    return "\n".join(lines)


def track_recommendations(res: ReboundResult, conn=None) -> int:
    """写入 recommendation_track（供复盘/回测追踪胜率）。返回写入条数。"""
    own = conn is None
    if own:
        conn = get_db(write=True)
    n = 0
    try:
        for s in res.candidates:
            conn.execute(
                "INSERT INTO recommendation_track (symbol, name, market, rec_date, score, period, "
                "expected_return, scenarios_json) VALUES (?,?,?,?,?,?,?,?)",
                (s.symbol, s.name, "a", res.report_date, s.score, "short",
                 round(s.take_profit_first / s.stop_loss - 1.0, 4) if s.stop_loss else None,
                 f'{{"family": "{s.family}", "trigger": "{s.trigger}"}}'))
            n += 1
        conn.commit()
    finally:
        if own:
            conn.close()
    return n


if __name__ == "__main__":
    r = run_rebound()
    print(f"regime={r.regime} sentiment={r.sentiment_status} cap={r.position_cap:.0%} "
          f"oversold={r.oversold_count} strong={r.strong_count}")
    for s in r.top(10):
        print(f"  [{s.family:8s}] {s.symbol} {s.name:<8s} score={s.score:5.1f} conf={s.confidence:5.1f} "
              f"stop={s.stop_loss:7.2f} target={s.take_profit_first:7.2f} | {s.trigger}")
