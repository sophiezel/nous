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
    trigger: str           # 命中触发描述（大白话）
    score: float           # 0-100 加权排序分
    confidence: float      # 0-100
    entry_ref: float       # 信号日收盘（参考买入基准）
    stop_loss: float       # 预估止损价（执行层以买入日实际低点/ATR 落实）
    take_profit_first: float  # 首目标价（+5%/+8%）
    trail_drawdown: float  # 移动止盈回撤
    time_stop_days: int    # 时间止损 N 日
    position_pct: float    # 单票仓位上限
    score_detail: str = ""   # 得分构成（中文，透明）
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
    near_miss: list = field(default_factory=list)    # 差一点触发的观察列表 (dict)
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
        self._industry_pit: Optional[dict] = None   # symbol -> [(start_date, industry_code), ...] 升序 (PIT)
        self._sentiment_map: Optional[dict] = None
        self._regime_map: Optional[dict] = None
        self._limit_down_map: Optional[dict] = None
        self._margin_map: Optional[dict] = None   # symbol -> [(trade_date, margin_balance), ...] 升序
        if memory is not None:
            self._init_memory(memory, report_date)
        else:
            self.conn = get_db(write=False)
            self.report_date = report_date or self._latest_date()
            self._load()

    def _init_memory(self, memory: dict, report_date: str) -> None:
        self.bars = memory.get("bars", {})
        self.names = memory.get("names", {})
        self.lhb_net5 = memory.get("lhb_net5", {})
        self.sector_heat = memory.get("sector_heat", {})
        self.total_mv = memory.get("total_mv", {})
        self.listing_dates = memory.get("listing_dates", {})
        self._industry_map = memory.get("industry_map")
        self._industry_pit = memory.get("industry_pit")
        if self._industry_pit is None and memory.get("industry_pit_db") is not None:
            self._industry_pit = memory.get("industry_pit_db")
        self._sentiment_map = memory.get("sentiment_map")
        self._regime_map = memory.get("regime_map")
        self._limit_down_map = memory.get("limit_down_map")
        self._margin_map = memory.get("margin_by_symbol")
        if self._margin_map is None and memory.get("margin_db") is not None:
            self._margin_map = memory.get("margin_db")
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
        # 最近 n_bars 个交易日的日线（走 stock_daily_all 视图：年分区+热表全量，支持历史日期）
        dates = [r[0] for r in self.conn.execute(
            "SELECT trade_date FROM stock_daily_all WHERE trade_date<=? "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?",
            (self.report_date, self.n_bars))]
        if not dates:
            return
        cutoff = dates[-1]
        rows = self.conn.execute(
            "SELECT symbol, trade_date, open, high, low, close, volume, amount FROM stock_daily_all "
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

        # 个股两融（近 30 交易日，供融资余额变化率因子）
        try:
            m_dates = [r[0] for r in self.conn.execute(
                "SELECT trade_date FROM margin_stock_daily WHERE trade_date<=? "
                "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 30", (self.report_date,))]
            if m_dates:
                m_from = m_dates[-1]
                mm: dict[str, tuple[list, list]] = {}
                for sym, td, bal in self.conn.execute(
                        "SELECT symbol, trade_date, margin_balance FROM margin_stock_daily "
                        "WHERE trade_date>=? AND trade_date<=? AND symbol NOT LIKE '1%' "
                        "AND symbol NOT LIKE '5%' ORDER BY symbol, trade_date", (m_from, self.report_date)):
                    if bal is None:
                        continue
                    mm.setdefault(sym, ([], []))
                    mm[sym][0].append(td)
                    mm[sym][1].append(float(bal))
                self._margin_map = mm
        except Exception:
            self._margin_map = None

        # 申万 PIT 行业分类（消除行业因子前视污染）
        try:
            pit: dict[str, tuple[list, list]] = {}
            for sym, sd, code in self.conn.execute(
                    "SELECT symbol, start_date, industry_code FROM industry_pit ORDER BY symbol, start_date"):
                pit.setdefault(sym, ([], []))
                pit[sym][0].append(sd)
                pit[sym][1].append(code)
            self._industry_pit = pit
        except Exception:
            self._industry_pit = None

        self._load_sector_heat()

    def _get_industry(self, symbol: str, day: str) -> Optional[str]:
        """PIT 行业归属：取 start_date <= day 的最新一段；无 PIT 数据回退当前快照。"""
        if self._industry_pit:
            seq = self._industry_pit.get(symbol)
            if seq:
                import bisect
                dates = seq[0]
                i = bisect.bisect_right(dates, day) - 1
                if i >= 0:
                    return seq[1][i]
        return (self._industry_map or {}).get(symbol)

    def _compute_sector_heat(self) -> None:
        """板块热度 = 同行业股票近5日平均收益（PIT 行业归属优先，消除前视污染）。"""
        ret5: dict[str, float] = {}
        for sym, bars in self.bars.items():
            if len(bars) >= 6 and bars[-1]["close"] > 0 and bars[-6]["close"] > 0:
                ret5[sym] = bars[-1]["close"] / bars[-6]["close"] - 1.0
        day = self.report_date
        from collections import defaultdict
        sums, cnts = defaultdict(float), defaultdict(int)
        for sym in ret5:
            sec = self._get_industry(sym, day)
            if sec:
                sums[sec] += ret5[sym]
                cnts[sec] += 1
        sec_mean = {s: sums[s] / cnts[s] for s in sums if cnts[s] > 0}
        self.sector_heat = {sym: sec_mean.get(self._get_industry(sym, day), 0.0) for sym in ret5}

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
        return f"连续跌了{drops}天 + 跌过头(RSI {rsi:.0f}) + 今天收阳 + 放量{vr:.1f}倍"

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
                # 融资余额 5 日变化率（资金面: 杠杆资金是否在进场）
                f["margin_chg5"] = self._margin_chg5(sym, bars[-1]["date"])
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

        # 闸门开关 + 族过滤（校准配置：超跌族-only）
        families = _risk("quality", "families", default=["oversold", "strong"])
        min_score = float(_risk("quality", "min_score", default=0.0))
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
            if "oversold" in families and not block_oversold:
                t = self._oversold_trigger(symbol)
                if t:
                    oversold_pool.append((symbol, t))
                else:
                    nm = self._near_miss(symbol)
                    if nm:
                        res.near_miss.append(nm)
            if "strong" in families and not block_strong:
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
                sig = self._make_signal(symbol, "oversold", trig, info["score"], conf, info.get("factors"))
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
                sig = self._make_signal(symbol, "strong", trig, info["score"], conf, info.get("factors"))
                res.candidates.append(sig)
            res.strong_count = len(strong_pool)

        res.candidates.sort(key=lambda s: s.score, reverse=True)
        res.candidates = [c for c in res.candidates if c.score >= min_score]
        res.candidates = res.candidates[: _risk("position", "max_daily_picks", default=5) * 4]
        res.near_miss.sort(key=lambda n: n["rsi"])
        res.near_miss = res.near_miss[:15]
        return res

    def _margin_chg5(self, symbol: str, day: str) -> float:
        """融资余额 5 交易日变化率；无数据返回 0（中性）。"""
        if self._margin_map is None:
            return 0.0
        seq = self._margin_map.get(symbol)
        if not seq:
            return 0.0
        import bisect
        dates = seq[0]
        balances = seq[1]
        i = bisect.bisect_right(dates, day) - 1
        j = bisect.bisect_right(dates, day) - 6
        if i < 0 or j < 0 or i >= len(balances) or balances[i] is None or balances[j] is None or balances[j] <= 0:
            return 0.0
        return balances[i] / balances[j] - 1.0

    def _rsi_now(self, symbol: str) -> Optional[float]:
        return _compute_rsi([b["close"] for b in self.bars[symbol]])

    def _near_miss(self, symbol: str) -> Optional[dict]:
        """观察列表：连跌≥2 + 阳线 + 量比>1.5 但 RSI 未到 35（或连跌只 1 日）的候选。"""
        bars = self.bars[symbol]
        closes = [b["close"] for b in bars]
        if len(closes) < 16:
            return None
        # 连跌 2 日
        drops2 = all(closes[-i] < closes[-i - 1] for i in (1, 2))
        drops1 = closes[-1] < closes[-2]
        if not drops2 and not drops1:
            return None
        rsi = _compute_rsi(closes)
        if rsi is None:
            return None
        vr = _compute_volume_ratio([b["volume"] for b in bars])
        if bars[-1]["close"] <= bars[-1]["open"]:
            return None
        # 差 RSI 一口气（35~45）或只跌 1 日
        if drops2 and 35 <= rsi < 45:
            gap = f"RSI {rsi:.1f}，还差一点点才够跌过头"
        elif drops1 and rsi < 35 and vr and vr > 1.5:
            gap = "只连续跌了1天"
        else:
            return None
        return {"symbol": symbol, "name": self.names.get(symbol, ""), "rsi": round(rsi, 1),
                "volume_ratio": round(vr, 2) if vr else None, "gap": gap}

    def ml_feature_vector(self, symbol: str) -> list[float]:
        """ML 特征向量（PIT，窗口 ≤20 日；与 _family_scores 同源，供 #12 ML 对照用）。
        顺序: [ret_5d, price_position_20, ma_gap_20, rsi14, volume_ratio, stabilize,
               atr_pct, ret_1d, ret_3d, lhb_net5, sector_heat]
        """
        bars = self.bars[symbol]
        closes = [b["close"] for b in bars]
        if len(closes) < 6:
            return [0.0] * 11
        ret_5d = closes[-1] / closes[-6] - 1.0
        lo20 = min(b["low"] for b in bars[-20:])
        hi20 = max(b["high"] for b in bars[-20:])
        pp = (closes[-1] - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5
        ma20 = _compute_ma(closes, 20) or closes[-1]
        mg = (closes[-1] - ma20) / ma20
        rsi = _compute_rsi(closes) or 50.0
        vr = _compute_volume_ratio([b["volume"] for b in bars]) or 1.0
        prev5_low = min(b["low"] for b in bars[-6:-1]) if len(bars) >= 6 else closes[-1]
        stab = 1.0 if closes[-1] > prev5_low else 0.0
        atr = _compute_atr(bars) or closes[-1] * 0.03
        atr_pct = atr / closes[-1] if closes[-1] > 0 else 0.0
        ret_1d = closes[-1] / closes[-2] - 1.0 if len(closes) >= 2 else 0.0
        ret_3d = closes[-1] / closes[-4] - 1.0 if len(closes) >= 4 else 0.0
        return [ret_5d, pp, mg, rsi, vr, stab, atr_pct, ret_1d, ret_3d,
                self.lhb_net5.get(symbol, 0.0), self.sector_heat.get(symbol, 0.0)]

    def _make_signal(self, symbol: str, family: str, trigger: str, score: float, confidence: float,
                     factors: Optional[dict] = None) -> EntrySignal:
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
        score_detail = self._fmt_score_detail(family, factors)
        return EntrySignal(
            symbol=symbol, name=self.names.get(symbol, ""), family=family, trigger=trigger,
            score=round(float(score), 1), confidence=round(float(confidence), 1),
            entry_ref=round(close, 2),
            stop_loss=round(stop_loss, 2),
            take_profit_first=round(close * (1 + first_pct), 2),
            trail_drawdown=float(_risk("take_profit", "trail_drawdown", default=0.04)),
            time_stop_days=time_days,
            position_pct=float(_risk("position", "max_single_pct", default=0.15)),
            score_detail=score_detail,
            detail=f"信号日收盘 {close:.2f}",
        )

    @staticmethod
    def _fmt_score_detail(family: str, factors: Optional[dict]) -> str:
        """得分构成（中文，透明）：因子 → 归一化值×权重。"""
        if not factors:
            return ""
        labels = {
            "oversold": {"ret_5d": "5日跌幅", "price_position_20": "20日低位", "ma_gap_20": "偏离20日线",
                         "volume_dry": "缩量", "stabilize": "企稳", "margin_chg5": "融资余额变化",
                         "bounce_elasticity": "反弹弹性",
                         "lhb_net": "龙虎榜净买", "sector_heat": "板块热度"},
            "strong": {"limit_up_streak": "连板高度", "volume_accel": "放量",
                        "lhb_net": "龙虎榜净买", "sector_heat": "板块热度"},
        }
        weights = WEIGHTS.get(family, {})
        lbl = labels.get(family, {})
        parts = []
        for k, w in weights.items():
            v = (factors.get(k) or 0.0) * w
            parts.append(f"{lbl.get(k, k)} {v:.0f}/{w:.0f}")
        return "，".join(parts)

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
        "| # | 名称 | 代码 | 符合度 | 为什么选它 | 参考买入价 | 止损价 | 首目标 | 剩余移动止盈 | 时间止损 | 得分构成 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, s in enumerate(res.candidates, 1):
        lines.append(
            f"| {i} | {s.name} | {s.symbol} | {s.score:.0f}/100 | {s.trigger} | {s.entry_ref:.2f} | "
            f"{s.stop_loss:.2f} | {s.take_profit_first:.2f} | 新高回撤{s.trail_drawdown:.0%} | "
            f"{s.time_stop_days}日<+2% | {s.score_detail} |"
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


def review_recommendations(rec_date_from: str = "", conn=None) -> dict:
    """复盘：对 recommendation_track 中 actual_return 为空的记录，按验收模型退出规则计算实际结果。

    规则（与验收模型一致）：
      - 入场 = 推荐日次日开盘（限价：开盘 > 推荐日收盘×1.05 则未成交 → 跳过）
      - 首目标 +5% 触及（收盘）→ 赢；第 6 个交易日收盘收益 ≥ +2% → 赢；< +2% → 输
      - 破买入日低点（止损）→ 输（按止损价计）
    返回 {reviewed, wins, losses, win_rate, skipped_no_trade, total_pnl_pct}。
    """
    own = conn is None
    if own:
        conn = get_db(write=False)
    try:
        q = "SELECT id, symbol, rec_date FROM recommendation_track WHERE actual_return IS NULL"
        if rec_date_from:
            q += " AND rec_date >= ?"
            rows = conn.execute(q, (rec_date_from,)).fetchall()
        else:
            rows = conn.execute(q).fetchall()
        if not rows:
            return {"reviewed": 0, "wins": 0, "losses": 0, "win_rate": None,
                    "skipped_no_trade": 0, "total_pnl_pct": 0.0, "note": "无待复盘记录"}
        # 取每个 symbol 全量 bars（含推荐日之后）
        symbols = sorted({r[1] for r in rows})
        bars: dict[str, list[dict]] = {}
        for sym in symbols:
            b = conn.execute(
                "SELECT trade_date, open, high, low, close FROM stock_daily WHERE symbol=? "
                "ORDER BY trade_date", (sym,)).fetchall()
            bars[sym] = [{"date": td, "open": float(o or 0), "high": float(h or 0),
                          "low": float(l or 0), "close": float(c or 0)} for td, o, h, l, c in b]
        name_map = {}
        for sym in symbols:
            row = conn.execute("SELECT name FROM stock_basic WHERE symbol=?", (sym,)).fetchone()
            name_map[sym] = row[0] if row else ""

        wins = losses = skipped = 0
        total_pnl_pct = 0.0
        reviewed = 0
        for rid, sym, rec_date in rows:
            b = bars.get(sym, [])
            if not b:
                continue
            import bisect
            dates = [x["date"] for x in b]
            i = bisect.bisect_right(dates, rec_date)
            if i >= len(b):
                continue  # 无后续数据
            sig_close = b[i - 1]["close"] if i > 0 else 0.0
            entry = b[i]["open"]
            if entry <= 0:
                continue
            # 限价单：次日开盘 > 推荐日收盘×1.05 → 未成交
            if sig_close > 0 and entry > sig_close * 1.05:
                conn.execute("UPDATE recommendation_track SET actual_return=0, hit=0, "
                             "scenarios_json=json_set(COALESCE(scenarios_json,'{}'),'$.review','limit_up_skip') "
                             "WHERE id=?", (rid,))
                skipped += 1
                continue
            # 买入日低点止损锚点（与验收一致）
            buy_day_low = b[i]["low"]
            stop = buy_day_low if (entry - buy_day_low) / entry >= 0.01 else entry * 0.95
            # 扫描后续 6 个交易日
            out = None
            for k in range(i + 1, min(i + 7, len(b))):
                day = b[k]
                if day["low"] <= stop:
                    out = ("loss", stop / entry - 1.0)
                    break
                if day["close"] >= entry * 1.05:
                    out = ("win", 0.05)
                    break
            if out is None:
                last = b[min(i + 6, len(b) - 1)]
                ret = last["close"] / entry - 1.0
                out = ("win" if ret >= 0.02 else "loss", ret)
            pnl_pct = out[1]
            conn.execute("UPDATE recommendation_track SET actual_return=?, hit=? WHERE id=?",
                         (round(pnl_pct, 4), 1 if out[0] == "win" else 0, rid))
            if out[0] == "win":
                wins += 1
            else:
                losses += 1
            total_pnl_pct += pnl_pct
            reviewed += 1
        conn.commit()
        wr = wins / (wins + losses) if (wins + losses) else None
        return {"reviewed": reviewed, "wins": wins, "losses": losses, "win_rate": wr,
                "skipped_no_trade": skipped, "total_pnl_pct": round(total_pnl_pct, 4)}
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    r = run_rebound()
    print(f"regime={r.regime} sentiment={r.sentiment_status} cap={r.position_cap:.0%} "
          f"oversold={r.oversold_count} strong={r.strong_count}")
    for s in r.top(10):
        print(f"  [{s.family:8s}] {s.symbol} {s.name:<8s} score={s.score:5.1f} conf={s.confidence:5.1f} "
              f"stop={s.stop_loss:7.2f} target={s.take_profit_first:7.2f} | {s.trigger}")
