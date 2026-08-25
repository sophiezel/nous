"""rebound_backtest.py — 短期反弹引擎事件驱动回测（验收 #6 规格）

纪律（2026-08-24-rebound-backtest-acceptance-design.md）:
  - 数据: 只用信号日收盘前可得数据；因子窗口 <=20 日（未复权）
  - 成交: 信号日收盘后计算 → 次日开盘成交（P5/P6）；一字板/次日触板不可成交（P3）
  - 费用: 单边 ≈0.3%（佣金万三 + 卖出印花税 0.05% + 滑点 0.25%）
  - 退出: #9 规格（分族止损 / 首目标出半+移动止盈 / 时间止损 / 组合级降仓清仓）
  - 仓位: 单票 ≤15%、单日 ≤5 只、总仓位 ≤ regime 上限
  - 前视剔除: 无 K7_*（本回测不用基本面因子）；板块仅当日截面；北向不用
"""

# ── 路径 ─────────────────────────────────────────────
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# 仓库根：从模块位置向上找到含 config/rebound_risk.yaml 的目录（兼容 src/ 布局）
def _find_repo_root() -> Path:
    env_dir = os.environ.get("NOUS_CONFIG_DIR", "")
    if env_dir and (Path(env_dir) / "rebound_risk.yaml").exists():
        return Path(env_dir).parent
    cur = Path(__file__).resolve()
    for _ in range(6):
        if (cur / "config" / "rebound_risk.yaml").exists():
            return cur
        cur = cur.parent
    return cur

PROJECT_ROOT = _find_repo_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import yaml

from nous.data.storage import get_db
from nous.engine.screening.rebound import (
    ReboundEngine,
    _compute_atr,
    _compute_ma,
    _get_limit_pct,
    _is_limit_up,
)


def _load_risk() -> dict:
    try:
        d = yaml.safe_load((PROJECT_ROOT / "config" / "rebound_risk.yaml").read_text())
        return (d or {}).get("rebound", {}).get("risk", {}) or {}
    except Exception:
        return {}


RISK = _load_risk()

FEE_RATE = 0.003          # 单边 ≈0.3%
MAX_SINGLE_PCT = 0.15     # 单票 15%
REGIME_CAP = {"BULL": 0.90, "SIDEWAYS": 0.60, "VOLATILE": 0.30, "BEAR": 0.20}
BLOCK_OVERSOLD_REGIMES = ("BEAR", "VOLATILE")
BLOCK_STRONG_SENTIMENT = ("cold", "cool")
SENTIMENT_TH = {"hot": 75, "warm": 60, "cool": 40}


@dataclass
class Position:
    symbol: str
    name: str
    family: str
    entry_date: str
    entry_price: float
    shares: int
    buy_day_low: float          # 买入日最低价（止损锚点）
    buy_day_atr: float          # 买入日 ATR（兜底）
    stop_price: float           # 当前止损价
    first_target: float         # 首目标价
    trail_peak: float           # 移动止盈峰值（收盘）
    time_stop_days: int
    min_gain: float
    cost: float                 # 累计成本（含费）
    pnl: float = 0.0            # 已实现盈亏
    stopped: bool = False
    took_first: bool = False    # 首目标已出半仓


@dataclass
class ClosedTrade:
    symbol: str
    family: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl: float                  # 扣费后净盈亏（金额）
    pnl_pct: float
    reason: str


class ReboundBacktest:
    def __init__(self, start: str, end: str, initial_capital: float = 1_000_000.0):
        self.start = start
        self.end = end
        self.initial_capital = initial_capital
        self.memory = self._preload()
        self._sym_dates: dict[str, list[str]] = {
            sym: [b["date"] for b in sym_bars] for sym, sym_bars in self.memory["bars"].items()}
        self.trade_dates: list[str] = self._trade_dates()
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        self.closed: list[ClosedTrade] = []
        self.equity: list[dict] = []
        self.limit_down_streak = 0
        self.peak_equity = initial_capital

    # ── 预载（一次性）──────────────────────────────
    def _preload(self) -> dict:
        conn = get_db(write=False)
        try:
            # bars: 窗口 + 60 日回看（信号需 40 根）
            lo = self._calendar_back(self.start, 90)
            rows = conn.execute(
                "SELECT symbol, trade_date, open, high, low, close, volume, amount "
                "FROM stock_daily_all WHERE trade_date >= ? AND trade_date <= ?",
                (lo, self.end)).fetchall()
            bars: dict[str, list[dict]] = defaultdict(list)
            for symbol, td, o, h, l, c, v, amt in rows:
                if o is None or c is None:
                    continue
                bars[symbol].append({"date": td, "open": float(o), "high": float(h or o),
                                     "low": float(l or o), "close": float(c),
                                     "volume": float(v or 0), "amount": float(amt or 0)})
            for sym in bars:
                bars[sym].sort(key=lambda b: b["date"])

            names = {s: (n or "") for s, n in
                     conn.execute("SELECT symbol, name FROM stock_basic WHERE market='a'")}

            total_mv = {}
            for symbol, mv in conn.execute("SELECT symbol, total_mv FROM stock_fundamental"):
                if mv:
                    total_mv[symbol] = float(mv)

            industry_map = {}
            for symbol, i2 in conn.execute(
                    "SELECT symbol, industry_l2 FROM stock_industry_multilevel WHERE is_current=1"):
                if i2:
                    industry_map[symbol] = i2

            # 龙虎榜近5日净买（按日预聚合）
            lhb_dates = [r[0] for r in conn.execute(
                "SELECT trade_date FROM lhb_daily WHERE trade_date>=? GROUP BY trade_date "
                "ORDER BY trade_date", (lo,))]
            lhb_by_date: dict[str, dict[str, float]] = {}
            for td in lhb_dates:
                d = {}
                for symbol, net in conn.execute(
                        "SELECT symbol, SUM(net_amount) FROM lhb_daily WHERE trade_date=? "
                        "GROUP BY symbol", (td,)):
                    if net:
                        d[symbol] = float(net)
                lhb_by_date[td] = d

            # 情绪
            sentiment_map = {}
            for d, sc in conn.execute("SELECT date, score FROM sentiment_cache"):
                if sc:
                    sentiment_map[d] = int(sc)

            # 上市日期近似（最早 trade_date）
            listing_dates = {}
            for symbol in bars:
                first = bars[symbol][0]["date"]
                # 若窗口起点即最早，说明上市可能早于窗口；用全库查精确最早日
                listing_dates[symbol] = first
            # 全库最早（仅对回看窗口起点的 symbol 精确化——简化：直接查全库一次）
            for symbol, first in conn.execute(
                    "SELECT symbol, MIN(trade_date) FROM stock_daily_all GROUP BY symbol"):
                if symbol in bars:
                    listing_dates[symbol] = first

            # 跌停家数（按日）
            limit_down_map = self._build_limit_down_map(bars, names)

            # 个股两融（margin_stock_daily；过滤 ETF 前缀 1/5）
            margin_by_symbol: dict[str, tuple[list, list]] = {}
            for sym, td, bal in conn.execute(
                    "SELECT symbol, trade_date, margin_balance FROM margin_stock_daily "
                    "WHERE trade_date>=? AND symbol NOT LIKE '1%' AND symbol NOT LIKE '5%' "
                    "ORDER BY symbol, trade_date", (lo,)):
                if bal is None:
                    continue
                margin_by_symbol.setdefault(sym, ([], []))
                margin_by_symbol[sym][0].append(td)
                margin_by_symbol[sym][1].append(float(bal))
        finally:
            conn.close()

        return {
            "bars": dict(bars), "names": names, "total_mv": total_mv,
            "industry_map": industry_map, "lhb_by_date": lhb_by_date,
            "sentiment_map": sentiment_map, "listing_dates": listing_dates,
            "limit_down_map": limit_down_map,
            "margin_by_symbol": margin_by_symbol,
            "all_bars": dict(bars),
        }
    @staticmethod
    def _calendar_back(date_str: str, days: int) -> str:
        from datetime import timedelta
        d = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days)
        return d.strftime("%Y-%m-%d")

    def _build_limit_down_map(self, bars, names) -> dict[str, int]:
        """按日统计跌停家数（推断：跌幅 <= -涨停线×0.995）。"""
        by_date: dict[str, list] = defaultdict(list)
        for sym, sym_bars in bars.items():
            for i in range(1, len(sym_bars)):
                by_date[sym_bars[i]["date"]].append((sym, sym_bars[i - 1]["close"], sym_bars[i]["close"]))
        out = {}
        for day, lst in by_date.items():
            n = 0
            for sym, pc, c in lst:
                if pc <= 0:
                    continue
                limit = _get_limit_pct(sym, names.get(sym, ""))
                if c / pc - 1.0 <= -limit * 0.995:
                    n += 1
            out[day] = n
        return out

    def _trade_dates(self) -> list[str]:
        dates = sorted({b["date"] for sym_bars in self.memory["bars"].values() for b in sym_bars})
        return [d for d in dates if self.start <= d <= self.end]

    # ── 每日执行 ────────────────────────────────────
    def _slice_bars(self, day: str) -> dict[str, list[dict]]:
        """取每个 symbol 截至 day 的最近 45 根 bar。"""
        out = {}
        for sym, sym_bars in self.memory["bars"].items():
            dates = self._sym_dates[sym]
            if not dates or dates[0] > day:
                continue
            import bisect
            idx = bisect.bisect_right(dates, day)
            if idx > 0:
                out[sym] = sym_bars[max(0, idx - 45):idx]
        return out

    def _lhb_net5_for(self, day: str) -> dict[str, float]:
        lhb = self.memory["lhb_by_date"]
        days = sorted(d for d in lhb if d <= day)[-5:]
        out = defaultdict(float)
        for d in days:
            for sym, net in lhb[d].items():
                out[sym] += net
        return dict(out)

    def run(self) -> dict:
        for day in self.trade_dates:
            self._manage_positions(day)
            self._enter_positions(day)
            self._mark_equity(day)
        return self._metrics()

    def _enter_positions(self, day: str) -> None:
        """day = 信号日；次日开盘买入。"""
        nxt = self._next_trade_date(day)
        if nxt is None:
            return
        day_bars = self._slice_bars(day)
        if not day_bars:
            return
        memory = dict(self.memory)
        memory["bars"] = day_bars
        memory["lhb_net5"] = self._lhb_net5_for(day)
        engine = ReboundEngine(report_date=day, memory=memory)
        try:
            result = engine.scan()
        except Exception:
            return
        # ML 对照（#12）: 用模型概率替换透明加权得分
        ml_rank = getattr(self, "ml_rank", None)
        if ml_rank is not None:
            for c in result.candidates:
                try:
                    prob = ml_rank(engine, c.symbol)
                    c.score = float(prob) * 100.0
                except Exception:
                    pass
        if not result.candidates:
            return
        max_picks = int(RISK.get("position", {}).get("max_daily_picks", 5))
        min_score = float(RISK.get("quality", {}).get("min_score", 0.0))
        allowed = RISK.get("quality", {}).get("families", ["oversold", "strong"])
        # 质量门槛（降换手，对抗费用拖累）→ 族过滤 → 排序后取单日 ≤max_picks 只
        picks = [c for c in result.candidates if c.family in allowed and c.score >= min_score][:max_picks]
        nxt_bars = self._slice_bars(nxt)
        # 买入价：验收=次日开盘（P6）；诊断=s信号日收盘（违反 P6，仅定位追高）
        entry_mode = getattr(self, "entry_mode", "next_open")
        if entry_mode == "signal_close":
            buy_bars = self._slice_bars(day)
            open_prices = {sym: (b[-1]["close"] if b else 0.0) for sym, b in buy_bars.items()}
            prev_close = {}
        else:
            open_prices = {sym: (b[-1]["open"] if b and b[-1]["date"] == nxt else 0.0)
                           for sym, b in nxt_bars.items()}
            prev_close = {sym: (b[-2]["close"] if len(b) >= 2 else 0.0)
                          for sym, b in nxt_bars.items()}
        cash_per = self.cash * MAX_SINGLE_PCT
        chase_cap = float(RISK.get("quality", {}).get("entry_chase_cap", 0.0))  # 0=不限价
        for sig in picks:
            if sig.symbol in self.positions:
                continue
            op = open_prices.get(sig.symbol, 0.0)
            pc = prev_close.get(sig.symbol, 0.0)
            if op <= 0:
                continue
            # 限价单模型：次日开盘 > 信号日收盘×(1+cap) 则不成交（拒绝追高；无后视镜，限价单合法）
            if chase_cap > 0 and entry_mode == "next_open":
                sig_close = day_bars.get(sig.symbol, [{}])[-1].get("close", 0) if day_bars.get(sig.symbol) else 0
                if sig_close > 0 and op > sig_close * (1 + chase_cap):
                    continue
            limit = _get_limit_pct(sig.symbol, sig.name)
            # 次日触板不可成交（P3）；诊断模式跳过此检查
            if entry_mode == "next_open" and pc > 0 and _is_limit_up(op, pc, limit):
                continue
            cost = op * (1 + FEE_RATE)
            if cost <= 0 or cash_per < cost:
                continue
            shares = int(cash_per / cost / 100) * 100
            if shares < 100:
                continue
            # 总仓位约束（regime 上限）
            used = sum(p.shares * p.entry_price for p in self.positions.values())
            total_cap = self.initial_capital * REGIME_CAP.get(result.regime, 0.6)
            if used + shares * cost > total_cap:
                continue
            # 买入日低点/ATR 止损锚点（验收=次日，诊断=信号日）
            anchor_bars = nxt_bars.get(sig.symbol, []) if entry_mode == "next_open" else day_bars.get(sig.symbol, [])
            buy_day_low = min(b["low"] for b in anchor_bars) if anchor_bars else op
            buy_day_atr = _compute_atr(anchor_bars) or op * 0.03
            atr_mult = RISK.get("stop_loss", {}).get("atr_multiplier", 2.0)
            day_low_buffer = RISK.get("stop_loss", {}).get("day_low_min_buffer", 0.01)
            if sig.family == "oversold":
                stop = buy_day_low
                if (op - buy_day_low) / op < day_low_buffer:
                    stop = op - buy_day_atr * atr_mult
            else:
                ma20 = _compute_ma([b["close"] for b in anchor_bars], 20) or op
                strong_pct = RISK.get("stop_loss", {}).get("strong_family_pct", 0.05)
                stop = min(op * (1 - strong_pct), ma20)
            self.cash -= shares * cost
            self.positions[sig.symbol] = Position(
                symbol=sig.symbol, name=sig.name, family=sig.family,
                entry_date=nxt, entry_price=op, shares=shares,
                buy_day_low=buy_day_low, buy_day_atr=buy_day_atr,
                stop_price=stop, first_target=sig.take_profit_first,
                trail_peak=op, time_stop_days=sig.time_stop_days,
                min_gain=RISK.get("time_stop", {}).get("min_gain", 0.02),
                cost=op * (1 + FEE_RATE),
            )

    def _next_trade_date(self, day: str) -> Optional[str]:
        dates = self.trade_dates
        import bisect
        idx = bisect.bisect_right(dates, day)
        return dates[idx] if idx < len(dates) else None

    def _manage_positions(self, day: str) -> None:
        """按收盘管理持仓：止损/止盈/时间止损/组合级退出。"""
        day_bars = self._slice_bars(day)
        limit_down_alert = self.memory["limit_down_map"].get(day, 0) > RISK.get(
            "gate", {}).get("exit_limit_down_count", 200)
        if limit_down_alert:
            self.limit_down_streak += 1
        else:
            self.limit_down_streak = 0

        for sym in list(self.positions):
            pos = self.positions[sym]
            b = day_bars.get(sym, [])
            if not b or b[-1]["date"] != day:
                continue  # 停牌：持仓不动
            last = b[-1]
            limit = _get_limit_pct(sym, pos.name)
            # 跌停卖不出：顺延
            prev = b[-2]["close"] if len(b) >= 2 else pos.entry_price
            if prev > 0 and last["close"] / prev - 1.0 <= -limit * 0.995:
                continue
            days_held = self._days_held(pos.entry_date, day)
            total_return = last["close"] / pos.cost - 1.0
            reason = None

            # 买入日当天：只检查首目标（止损锚点=买入日最低价，当日不判，次日才有效）
            if day == pos.entry_date:
                if last["close"] >= pos.first_target and not pos.took_first:
                    pos.took_first = True
                    self._sell_half(pos, day, last["close"], "take_first")
                if last["close"] > pos.trail_peak:
                    pos.trail_peak = last["close"]
                continue

            # 止损
            if last["low"] <= pos.stop_price:
                reason = "stop"
                exit_px = min(pos.stop_price, last["open"])
            # 首目标：出 1/2（首个达到目标日）
            elif last["close"] >= pos.first_target and not pos.took_first:
                pos.took_first = True
                self._sell_half(pos, day, last["close"], "take_first")
                if last["close"] > pos.trail_peak:
                    pos.trail_peak = last["close"]
                continue
            # 时间止损
            elif days_held >= pos.time_stop_days:
                if total_return < pos.min_gain:
                    reason = "time"
                    exit_px = last["close"]
            # 移动止盈（剩余仓）
            if reason is None and pos.took_first:
                trail = RISK.get("take_profit", {}).get("trail_drawdown", 0.04)
                if last["close"] <= pos.trail_peak * (1 - trail) and pos.trail_peak > pos.entry_price * 1.02:
                    reason = "trail"
                    exit_px = last["close"]
            if reason is None and last["close"] > pos.trail_peak:
                pos.trail_peak = last["close"]

            if reason is None:
                # 组合级退出：连续 2 日跌停警报 → 清仓
                if self.limit_down_streak >= 2:
                    reason = "market_exit"
                    exit_px = last["close"]
                elif self.limit_down_streak == 1:
                    # 降仓 50%：卖一半
                    self._sell_half(pos, day, last["close"], "market_half")
                    continue

            if reason:
                self._sell_all(pos, day, exit_px, reason)

    def _days_held(self, entry_date: str, day: str) -> int:
        dates = self.trade_dates
        import bisect
        i0 = bisect.bisect_left(dates, entry_date)
        i1 = bisect.bisect_right(dates, day)
        return i1 - i0

    def _sell_all(self, pos: Position, day: str, px: float, reason: str) -> None:
        proceeds = pos.shares * px * (1 - FEE_RATE)
        pnl = proceeds - pos.shares * pos.cost
        self.cash += proceeds
        self.closed.append(ClosedTrade(
            symbol=pos.symbol, family=pos.family, entry_date=pos.entry_date, exit_date=day,
            entry_price=pos.entry_price, exit_price=px, pnl=pnl,
            pnl_pct=px / pos.cost - 1.0, reason=reason))
        del self.positions[pos.symbol]

    def _sell_half(self, pos: Position, day: str, px: float, reason: str) -> None:
        ratio = RISK.get("take_profit", {}).get("partial", 0.5) if reason == "take_first" else 0.5
        sell = int(pos.shares * ratio / 100) * 100
        if sell < 100:
            return
        proceeds = sell * px * (1 - FEE_RATE)
        pnl = proceeds - sell * pos.cost
        self.cash += proceeds
        pos.shares -= sell
        self.closed.append(ClosedTrade(
            symbol=pos.symbol, family=pos.family, entry_date=pos.entry_date, exit_date=day,
            entry_price=pos.entry_price, exit_price=px, pnl=pnl,
            pnl_pct=px / pos.cost - 1.0, reason=reason))

    def _mark_equity(self, day: str) -> None:
        day_bars = self._slice_bars(day)
        mkt = 0.0
        for sym, pos in self.positions.items():
            b = day_bars.get(sym, [])
            px = b[-1]["close"] if b and b[-1]["date"] == day else pos.entry_price
            mkt += pos.shares * px
        equity = self.cash + mkt
        self.equity.append({"date": day, "equity": round(equity, 2)})
        if equity > self.peak_equity:
            self.peak_equity = equity

    # ── 指标 ────────────────────────────────────────
    def _metrics(self) -> dict:
        wins = [t for t in self.closed if t.pnl > 0]
        losses = [t for t in self.closed if t.pnl <= 0]
        gp = sum(t.pnl for t in wins)
        gl = -sum(t.pnl for t in losses)
        win_rate = len(wins) / len(self.closed) if self.closed else 0.0
        pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
        expectancy = sum(t.pnl for t in self.closed) / len(self.closed) if self.closed else 0.0
        # 最大回撤（净值曲线）
        peak = -1e18
        mdd = 0.0
        for e in self.equity:
            if e["equity"] > peak:
                peak = e["equity"]
            dd = (peak - e["equity"]) / peak if peak > 0 else 0.0
            mdd = max(mdd, dd)
        # 简单年化 Sharpe（校准目标，规格 #8 §6）
        rets = []
        for i in range(1, len(self.equity)):
            e0, e1 = self.equity[i - 1]["equity"], self.equity[i]["equity"]
            if e0 > 0:
                rets.append(e1 / e0 - 1.0)
        sharpe = 0.0
        if len(rets) > 2:
            arr = np.asarray(rets)
            sd = float(np.std(arr))
            if sd > 0:
                sharpe = float(np.mean(arr)) / sd * (252 ** 0.5)
        final_equity = self.equity[-1]["equity"] if self.equity else self.initial_capital
        return {
            "window": f"{self.start}..{self.end}",
            "sharpe": round(sharpe, 3),
            "n_trade_days": len(self.trade_dates),
            "n_closed": len(self.closed),
            "n_open": len(self.positions),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(pf, 4) if pf != float("inf") else None,
            "expectancy": round(expectancy, 2),
            "gross_profit": round(gp, 2),
            "gross_loss": round(gl, 2),
            "max_drawdown": round(mdd, 4),
            "total_return": round(final_equity / self.initial_capital - 1.0, 4),
            "final_equity": round(final_equity, 2),
            "by_family": {
                fam: {
                    "n": sum(1 for t in self.closed if t.family == fam),
                    "win_rate": round(
                        sum(1 for t in self.closed if t.family == fam and t.pnl > 0)
                        / max(1, sum(1 for t in self.closed if t.family == fam)), 4),
                } for fam in ("oversold", "strong")
            },
            "reasons": {r: sum(1 for t in self.closed if t.reason == r) for r in
                        set(t.reason for t in self.closed)},
            "trusted": bool(self.equity) and all(e["equity"] > 0 for e in self.equity),
        }


def run_backtest(start: str, end: str, initial_capital: float = 1_000_000.0,
                 risk: Optional[dict] = None, weights: Optional[dict] = None,
                 fee_rate: Optional[float] = None,
                 entry_mode: str = "next_open",
                 ml_rank=None) -> dict:
    """
    risk/weights: 覆盖参数（校准迭代用）。临时替换模块全局，跑完恢复。
    risk = rebound_risk.yaml 的 rebound.risk 段；weights = rebound_weights.yaml 的 rebound.weights 段。
    fee_rate: 费用覆盖（诊断用；验收固定 0.3%）。
    entry_mode: "next_open"(验收纪律) | "signal_close"(诊断用，违反 P6，仅定位追高问题)。
    ml_rank: callable(engine, symbol) -> prob in [0,1]（#12 ML 对照，替换透明加权得分）。
    """
    import nous.engine.screening.rebound as rmod
    saved_risk_r, saved_w = rmod.RISK, rmod.WEIGHTS
    saved_risk_b, saved_fee = RISK, FEE_RATE
    if risk is not None:
        rmod.RISK = risk
        globals()["RISK"] = risk
    if weights is not None:
        rmod.WEIGHTS = weights
    if fee_rate is not None:
        globals()["FEE_RATE"] = fee_rate
    try:
        bt = ReboundBacktest(start, end, initial_capital)
        bt.entry_mode = entry_mode
        if ml_rank is not None:
            bt.ml_rank = ml_rank
        return bt.run()
    finally:
        rmod.RISK, rmod.WEIGHTS = saved_risk_r, saved_w
        globals()["RISK"] = saved_risk_b
        globals()["FEE_RATE"] = saved_fee


if __name__ == "__main__":
    import json
    m = run_backtest("2024-01-01", "2026-08-21")
    print(json.dumps(m, ensure_ascii=False, indent=2))
