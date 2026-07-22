#!/usr/bin/env python3
"""
港股量化盘引擎 — T+0 日内执行器

核心逻辑：
  1. 09:35 morning_rebalance — 日内首次调仓（买入/卖出）
  2. 14:00 afternoon_rebalance — 日内二次调仓（仅调权重，不开新仓）
  3. intraday_cooling_tracker — 卖出后60分钟冷却期
  4. ATR×2 止损

约束：
  - 仅限 hk_connect_universe 表标的
  - T+0 但单日同只股票买卖不超1次（一次完整 round-trip）
  - 日换手上限 30%
  - ATR×2 止损
  - 开盘30分钟内不执行二次调仓
  - 写入 quant_hk_position / quant_hk_nav / quant_hk_trades 表

佣金参考：
  from nous.trader.executor import CommissionCalc
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional
import traceback

# ============================================================
# 项目路径
# ============================================================
PROJECT_DIR = Path.home() / "code/stock-advisor"
SCREENER_DB = Path.home() / "code/stock-screener" / "data" / "screener.db"

sys.path.insert(0, str(PROJECT_DIR))

# 佣金计算（复用 executor）
try:
    from nous.trader.executor import CommissionCalc
    HAS_COMMISSION = True
except ImportError:
    HAS_COMMISSION = False

    # 降级：内置港股佣金计算
    class CommissionCalc:
        """降级版港股费用计算器"""
        HK_COMMISSION_RATE = Decimal("0.001")
        HK_MIN_COMMISSION = Decimal("100.00")
        HK_STAMP_TAX_RATE = Decimal("0.001")
        HK_STAMP_MIN = Decimal("1.00")
        HK_TRADING_FEE = Decimal("0.00005")
        HK_LEVY_RATE = Decimal("0.000027")
        HK_SYSTEM_FEE = Decimal("0.50")
        HK_SETTLEMENT_RATE = Decimal("0.00002")
        HK_SETTLEMENT_MIN = Decimal("2.00")
        HK_SETTLEMENT_MAX = Decimal("100.00")

        @classmethod
        def buy_cost(cls, price: Decimal, shares: int, market: str = "HK") -> tuple:
            return cls._cost(price, shares, "buy")

        @classmethod
        def sell_cost(cls, price: Decimal, shares: int, market: str = "HK") -> tuple:
            return cls._cost(price, shares, "sell")

        @classmethod
        def _cost(cls, price: Decimal, shares: int, side: str) -> tuple:
            amount = price * shares
            commission = max(amount * cls.HK_COMMISSION_RATE, cls.HK_MIN_COMMISSION)
            stamp = max(amount * cls.HK_STAMP_TAX_RATE, cls.HK_STAMP_MIN)
            trading_fee = amount * cls.HK_TRADING_FEE
            levy = amount * cls.HK_LEVY_RATE
            settlement = max(min(amount * cls.HK_SETTLEMENT_RATE, cls.HK_SETTLEMENT_MAX), cls.HK_SETTLEMENT_MIN)
            other = trading_fee + levy + cls.HK_SYSTEM_FEE + settlement
            return (
                commission.quantize(Decimal("0.01")),
                stamp.quantize(Decimal("0.01")),
                other.quantize(Decimal("0.01")),
            )


# ============================================================
# ATR 计算
# ============================================================

def calc_atr(symbol: str, period: int = 14) -> Optional[float]:
    """从 stock_daily 计算 ATR
    
    使用最近 N 日的 High-Low / |High-PrevClose| / |Low-PrevClose| TR 均值
    """
    try:
        db = sqlite3.connect(str(SCREENER_DB))
        rows = db.execute("""
            SELECT trade_date, open, high, low, close
            FROM stock_daily_all
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT ?
        """, (symbol, period + 1)).fetchall()
        db.close()

        if len(rows) < period + 1:
            return None

        # 倒序（从旧到新）
        rows_rev = list(reversed(rows))

        tr_sum = 0.0
        for i in range(1, len(rows_rev)):
            high = rows_rev[i][2]
            low = rows_rev[i][3]
            prev_close = rows_rev[i - 1][4]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_sum += tr

        return round(tr_sum / period, 2)
    except Exception:
        return None


def calc_factor_score(symbol: str) -> float:
    """港股多因子综合评分（0-100）

    复用 scoring.py 逻辑，此处做内联实现避免跨包依赖。
    因子：
      - 趋势（MA5 > MA20 得分）
      - 动量（最近5日涨幅）
      - 量能（成交量相对20日均量）
      - 南向资金（hsgt_stock_daily）
      - 做空比例（hk_short_signal）
      - ATR 波动率修正
    """
    score = 50.0  # 基础分

    try:
        db = sqlite3.connect(str(SCREENER_DB))

        # --- 1. 趋势因子 (0-20) ---
        trend_rows = db.execute("""
            SELECT trade_date, close
            FROM stock_daily_all
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT 20
        """, (symbol,)).fetchall()

        if len(trend_rows) >= 20:
            closes = [r[1] for r in reversed(trend_rows)]
            ma5 = sum(closes[-5:]) / 5
            ma20 = sum(closes[-20:]) / 20
            if ma5 > ma20:
                trend_score = 75 + min(25, (ma5 / ma20 - 1) * 500)
            else:
                trend_score = max(0, 50 - (ma20 / ma5 - 1) * 200)
        else:
            trend_score = 50

        # --- 2. 动量因子 (0-100) ---
        if len(trend_rows) >= 6:
            latest_close = trend_rows[0][1]
            close_5d_ago = trend_rows[4][1] if len(trend_rows) >= 5 else trend_rows[-1][1]
            mom = (latest_close / close_5d_ago - 1) * 100  # %
            if mom > 10:
                mom_score = 95
            elif mom > 5:
                mom_score = 85
            elif mom > 2:
                mom_score = 70
            elif mom > 0:
                mom_score = 60
            elif mom > -3:
                mom_score = 40
            elif mom > -8:
                mom_score = 25
            else:
                mom_score = 10
        else:
            mom_score = 50

        # --- 3. 量能因子 (0-100) ---
        vol_rows = db.execute("""
            SELECT volume
            FROM stock_daily_all
            WHERE symbol = ?
            ORDER BY trade_date DESC
            LIMIT 20
        """, (symbol,)).fetchall()

        if len(vol_rows) >= 20:
            vols = [r[0] or 0 for r in vol_rows]
            latest_vol = vols[0]
            avg_vol = sum(vols[1:]) / 19 if len(vols) > 1 else latest_vol
            vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 1.0
            if vol_ratio > 2.0:
                vol_score = 90
            elif vol_ratio > 1.5:
                vol_score = 75
            elif vol_ratio > 1.0:
                vol_score = 60
            elif vol_ratio > 0.5:
                vol_score = 40
            else:
                vol_score = 20
        else:
            vol_score = 50

        # --- 4. 南向资金因子 (0-100) ---
        try:
            south_rows = db.execute("""
                SELECT net_buy FROM hsgt_stock_daily
                WHERE symbol = ? AND trade_date >= ?
                ORDER BY trade_date DESC LIMIT 5
            """, (symbol, (date.today() - timedelta(days=10)).isoformat())).fetchall()

            if south_rows:
                net_buys = [r[0] or 0 for r in south_rows]
                weights = [0.35, 0.25, 0.2, 0.12, 0.08]
                weighted = sum(nb * w for nb, w in zip(net_buys, weights[:len(net_buys)]))
                if weighted > 100_000_000:
                    south_score = 90
                elif weighted > 10_000_000:
                    south_score = 75
                elif weighted > 0:
                    south_score = 60
                elif weighted > -10_000_000:
                    south_score = 40
                else:
                    south_score = 20
            else:
                south_score = 50
        except Exception:
            south_score = 50

        # --- 5. 做空因子 (0-100) ---
        try:
            short_row = db.execute("""
                SELECT short_ratio FROM hk_short_signal
                WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1
            """, (symbol,)).fetchone()

            if short_row and short_row[0] is not None:
                ratio = float(short_row[0])
                if ratio > 25:
                    short_score = 10
                elif ratio > 15:
                    short_score = 30
                elif ratio > 8:
                    short_score = 50
                elif ratio > 3:
                    short_score = 70
                else:
                    short_score = 90
            else:
                short_score = 50
        except Exception:
            short_score = 50

        # --- 6. 全球宏观 (0-100) ---
        try:
            macro_rows = db.execute("""
                SELECT symbol, close FROM index_global_daily
                WHERE symbol IN ('VIX', 'USDCNH')
                AND trade_date = (SELECT MAX(trade_date) FROM index_global_daily WHERE symbol IN ('VIX', 'USDCNH'))
            """).fetchall()
            macro = {r[0]: r[1] for r in macro_rows}

            vix = float(macro.get("VIX", 20) or 20)
            usdcnh = float(macro.get("USDCNH", 7.2) or 7.2)

            vix_factor = 1.0
            if vix > 30:
                vix_factor = 0.3
            elif vix > 25:
                vix_factor = 0.5
            elif vix > 20:
                vix_factor = 0.7

            cnh_factor = 1.0
            if usdcnh > 7.3:
                cnh_factor = 0.5
            elif usdcnh > 7.1:
                cnh_factor = 0.7

            macro_score = (vix_factor * 0.6 + cnh_factor * 0.4) * 100
        except Exception:
            macro_score = 60

        db.close()

        # 总分 (各子项0-100, 加权后0-100)
        score = (trend_score * 0.20 +
                 mom_score * 0.20 +
                 vol_score * 0.15 +
                 south_score * 0.20 +
                 short_score * 0.15 +
                 macro_score * 0.10)

        return round(min(100, max(0, score)), 1)

    except Exception as e:
        print(f"  [quant_hk] calc_factor_score({symbol}) 失败: {e}", file=sys.stderr)
        return score  # 返回基础分


# ============================================================
# 引擎
# ============================================================

class QuantHKExecutor:
    """港股量化盘日内执行器

    使用 screener.db 中的 quant_hk_position/nav/trades 表
    做持久化，而不是 trader 的 StateManager。
    """

    # 港股交易时段 (北京时间)
    HK_OPEN_TIME = "09:30"
    HK_CLOSE_TIME = "16:00"
    MORNING_REBALANCE_TIME = "09:35"
    AFTERNOON_REBALANCE_TIME = "14:00"

    # 约束参数
    MAX_DAILY_TURNOVER = 0.30       # 日换手上限 30%
    ATR_STOP_MULTIPLIER = 2.0       # ATR×2 止损
    COOLDOWN_MINUTES = 60           # 卖出后冷却60分钟
    OPEN_NO_TRADE_MINUTES = 30      # 开盘30分钟内不执行二次调仓
    MAX_POSITIONS = 12              # 最大持仓数量
    TARGET_POSITIONS = 8            # 目标持仓数量

    def __init__(self, db_path: str = "", initial_capital: float = 1_000_000):
        """
        Args:
            db_path: screener.db 路径（默认 ~/code/stock-screener/data/screener.db）
            initial_capital: 初始资金（人民币），仅首次运行时使用
        """
        self.db_path = db_path or str(SCREENER_DB)
        self.initial_capital = initial_capital

        # 盘中跟踪
        self._today_roundtrips: dict[str, bool] = {}      # {symbol: 已完整roundtrip}
        self._today_buys: dict[str, float] = {}            # {symbol: 买入时间戳}
        self._today_sells: dict[str, float] = {}            # {symbol: 卖出时间戳}
        self._cooling_symbols: set[str] = set()            # 冷却中的标的
        self._cooling_end: dict[str, datetime] = {}        # {symbol: 冷却结束时间}
        self._session_date: str = ""                        # 当前交易日
        self._morning_done: bool = False                    # 早盘调仓已完成
        self._afternoon_done: bool = False                  # 午盘调仓已完成

    # ---- DB 连接 ----

    def _get_db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    # ---- 初始化 / 重置 ----

    def reset_daily_tracking(self):
        """每日重置盘中跟踪状态"""
        self._today_roundtrips.clear()
        self._today_buys.clear()
        self._today_sells.clear()
        self._cooling_symbols.clear()
        self._cooling_end.clear()
        self._morning_done = False
        self._afternoon_done = False
        self._session_date = date.today().isoformat()

    # ---- 核心调仓入口 ----

    def execute_morning_rebalance(self) -> dict:
        """09:35 早盘调仓

        流程：
          1. 扫描所有港股通标的，计算因子评分
          2. 对当前持仓检查 ATR×2 止损 → 触发卖出
          3. 按评分排序，取 TOP N 买入
          4. 确保换手率 ≤ 30%

        Returns:
            dict { 'buys': [...], 'sells': [...], 'nav': float, 'turnover': float }
        """
        today = date.today().isoformat()
        self.reset_daily_tracking()
        self._session_date = today

        db = self._get_db()
        try:
            # --- 获取当前持仓 ---
            current_positions = self._load_positions(db)
            cash, total_asset = self._load_nav(db)
            if total_asset is None:
                # 首次运行，初始化 NAV
                self._init_nav(db, today, cash=self.initial_capital)
                cash = float(self.initial_capital)
                total_asset = self.initial_capital

            print(f"  [quant_hk] 早盘调仓 | 总资产 ¥{total_asset:,.2f} | 现金 ¥{cash:,.2f} | 持仓 {len(current_positions)} 只",
                  file=sys.stderr)

            # --- 止损检查 ---
            sell_results = self._check_stop_losses(db, current_positions, today)

            # 刷新持仓（卖出后）
            current_positions = self._load_positions(db)
            cash, total_asset = self._load_nav(db)
            total_asset = total_asset or cash

            # --- 获取港股通标的评分 ---
            universe = self._load_hk_universe(db)
            scored = self._score_universe(db, universe)

            # 排除已持仓（避免重复买入）
            held_symbols = {p["symbol"] for p in current_positions}
            candidates = [s for s in scored if s["symbol"] not in held_symbols]

            # 按评分降序
            candidates.sort(key=lambda x: x["score"], reverse=True)

            # --- 确定买入 ---
            max_buys = self.TARGET_POSITIONS - len(current_positions)
            if max_buys <= 0:
                buy_results = []
                print(f"  [quant_hk] 持仓已达目标({self.TARGET_POSITIONS}只)，无新买入", file=sys.stderr)
            else:
                # 计算可用资金（换手率约束）
                max_turnover_amount = total_asset * self.MAX_DAILY_TURNOVER
                available_cash = min(cash, max_turnover_amount)
                # 扣除卖出回笼资金已经体现在 cash 中

                buy_results = self._execute_buys(
                    db, candidates[:max_buys * 2], today,
                    available_cash, total_asset,
                    current_positions
                )

            # --- 最终 NAV ---
            final_positions = self._load_positions(db)
            final_cash, final_asset = self._load_nav(db)
            total_turnover = sum(
                b.get("amount", 0) for b in buy_results
            ) + sum(
                s.get("amount", 0) for s in sell_results
            )
            turnover_pct = total_turnover / total_asset if total_asset > 0 else 0

            # 记录今日NAV
            self._update_nav(db, today, final_asset, final_cash, turnover_pct)

            self._morning_done = True

            result = {
                "session": "morning",
                "date": today,
                "buys": buy_results,
                "sells": sell_results,
                "nav": round(final_asset, 2) if final_asset else 0,
                "cash": round(final_cash, 2) if final_cash else 0,
                "turnover": round(turnover_pct, 4),
                "position_count": len(final_positions),
            }

            print(f"  [quant_hk] 早盘调仓完成 | NAV ¥{final_asset:,.2f} | 换手率 {turnover_pct:.2%}",
                  file=sys.stderr)
            return result

        finally:
            db.close()

    def execute_afternoon_rebalance(self) -> dict:
        """14:00 日内二次调仓

        约束：
          - 开盘30分钟内不执行（本函数14:00调用自然满足）
          - 不开新仓，只调权重
          - 单只股票每日最多1次完整 round-trip
          - 冷却中的标的跳过

        Returns:
            dict
        """
        today = date.today().isoformat()
        if self._session_date != today:
            self.reset_daily_tracking()

        # 检查是否在开盘30分钟内（09:30-10:00禁调）
        now = datetime.now()
        open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
        if open_dt <= now <= open_dt + timedelta(minutes=self.OPEN_NO_TRADE_MINUTES):
            return {"session": "afternoon", "status": "skipped",
                    "reason": f"开盘{self.OPEN_NO_TRADE_MINUTES}分钟内不执行二次调仓"}

        db = self._get_db()
        try:
            current_positions = self._load_positions(db)
            cash, total_asset = self._load_nav(db)

            print(f"  [quant_hk] 午盘调仓 | 总资产 ¥{total_asset:,.2f} | 现金 ¥{cash:,.2f} | 持仓 {len(current_positions)} 只",
                  file=sys.stderr)

            if not current_positions:
                # 更新NAV
                self._update_nav(db, today, total_asset, cash, 0)
                return {"session": "afternoon", "status": "skipped",
                        "reason": "无持仓，跳过午盘调仓"}

            # --- 止损检查（二次） ---
            sell_results = self._check_stop_losses(db, current_positions, today,
                                                     is_afternoon=True)

            # 刷新持仓
            current_positions = self._load_positions(db)
            cash, total_asset = self._load_nav(db)

            # --- 权重调整（不开新仓，仅调已有持仓权重） ---
            adjust_results = self._rebalance_weights(
                db, current_positions, today, cash, total_asset
            )

            # --- 最终NAV ---
            final_positions = self._load_positions(db)
            final_cash, final_asset = self._load_nav(db)
            total_turnover = sum(
                a.get("amount", 0) for a in adjust_results
            ) + sum(
                s.get("amount", 0) for s in sell_results
            )
            turnover_pct = total_turnover / total_asset if total_asset > 0 else 0

            self._update_nav(db, today, final_asset, final_cash, turnover_pct)
            self._afternoon_done = True

            result = {
                "session": "afternoon",
                "date": today,
                "adjustments": adjust_results,
                "sells": sell_results,
                "nav": round(final_asset, 2) if final_asset else 0,
                "cash": round(final_cash, 2) if final_cash else 0,
                "turnover": round(turnover_pct, 4),
                "position_count": len(final_positions),
            }

            print(f"  [quant_hk] 午盘调仓完成 | NAV ¥{final_asset:,.2f} | 换手率 {turnover_pct:.2%}",
                  file=sys.stderr)
            return result

        finally:
            db.close()

    # ---- 冷却跟踪器 ----

    def intraday_cooling_tracker(self) -> dict:
        """检查冷却状态，返回冷却中的标的列表

        Returns:
            { 'cooling': [symbol, ...], 'expired': [symbol, ...] }
        """
        now = datetime.now()
        cooling = []
        expired = []

        for symbol, end_time in list(self._cooling_end.items()):
            if now < end_time:
                cooling.append(symbol)
            else:
                expired.append(symbol)
                self._cooling_symbols.discard(symbol)
                del self._cooling_end[symbol]
                # 标记该标的今日不能再交易
                self._today_roundtrips[symbol] = True

        return {"cooling": cooling, "expired": expired}

    def _is_cooling(self, symbol: str) -> bool:
        """检查标的是否在冷却期"""
        self.intraday_cooling_tracker()  # 刷新
        return symbol in self._cooling_symbols

    def _start_cooling(self, symbol: str):
        """开始冷却计时（卖出后调用）"""
        now = datetime.now()
        end = now + timedelta(minutes=self.COOLDOWN_MINUTES)
        self._cooling_symbols.add(symbol)
        self._cooling_end[symbol] = end

    # ========================================================
    # 内部方法
    # ========================================================

    def _load_hk_universe(self, db: sqlite3.Connection) -> list[dict]:
        """加载港股通标的列表"""
        rows = db.execute("""
            SELECT symbol, name FROM hk_connect_universe
            ORDER BY symbol
        """).fetchall()
        return [{"symbol": r["symbol"], "name": r["name"]} for r in rows]

    def _load_positions(self, db: sqlite3.Connection) -> list[dict]:
        """从 quant_hk_position 表加载当前持仓"""
        rows = db.execute("""
            SELECT symbol, name, weight, shares, entry_price, entry_date,
                   target_weight, factor_score, last_rebalance
            FROM quant_hk_position
            ORDER BY weight DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def _load_nav(self, db: sqlite3.Connection) -> tuple[Optional[float], Optional[float]]:
        """加载最新NAV → (cash, total_asset)"""
        row = db.execute("""
            SELECT cash, total_asset FROM quant_hk_nav
            ORDER BY trade_date DESC LIMIT 1
        """).fetchone()
        if row:
            return float(row["cash"] or 0), float(row["total_asset"] or 0)
        return None, None

    def _init_nav(self, db: sqlite3.Connection, trade_date: str, cash: float):
        """初始化NAV记录"""
        db.execute("""
            INSERT OR REPLACE INTO quant_hk_nav (trade_date, nav, daily_return, cash, total_asset, turnover)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (trade_date, cash, 0.0, cash, cash, 0.0))
        db.commit()

    def _update_nav(self, db: sqlite3.Connection, trade_date: str, total_asset: float,
                    cash: float, turnover: float):
        """更新当日NAV

        计算日收益率（相对于前一日NAV）
        """
        prev = db.execute("""
            SELECT nav FROM quant_hk_nav
            WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1
        """, (trade_date,)).fetchone()
        prev_nav = float(prev["nav"]) if prev else total_asset

        daily_return = (total_asset / prev_nav - 1) if prev_nav > 0 else 0.0

        db.execute("""
            INSERT OR REPLACE INTO quant_hk_nav (trade_date, nav, daily_return, cash, total_asset, turnover)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (trade_date, total_asset, round(daily_return, 6), round(cash, 2),
              round(total_asset, 2), round(turnover, 4)))
        db.commit()

    def _score_universe(self, db: sqlite3.Connection,
                        universe: list[dict]) -> list[dict]:
        """为港股通标的打分

        使用内联因子计算，返回 scored 列表。
        """
        scored = []
        batch_size = 50

        for i, stock in enumerate(universe):
            symbol = stock["symbol"]
            try:
                score = calc_factor_score(symbol)
                scored.append({
                    "symbol": symbol,
                    "name": stock["name"],
                    "score": score,
                })
            except Exception as e:
                print(f"  [quant_hk] 评分失败 {symbol}: {e}", file=sys.stderr)

            if (i + 1) % batch_size == 0:
                print(f"  [quant_hk] 评分进度: {i + 1}/{len(universe)}", file=sys.stderr)

        print(f"  [quant_hk] 评分完成: {len(scored)}/{len(universe)} 只", file=sys.stderr)
        return scored

    def _check_stop_losses(self, db: sqlite3.Connection,
                           positions: list[dict], today: str,
                           is_afternoon: bool = False) -> list[dict]:
        """检查 ATR×2 止损

        对每个持仓计算当前 ATR，若价格跌破 entry_price - 2*ATR 则卖出。
        """
        results = []

        for pos in positions:
            symbol = pos["symbol"]

            # 冷却中且是午盘 → 跳过（冷却期结束后不再交易）
            if is_afternoon and self._is_cooling(symbol):
                continue

            # 已 round-trip → 跳过
            if self._today_roundtrips.get(symbol, False):
                continue

            # 获取当前价格
            price_row = db.execute("""
                SELECT close FROM stock_daily_all
                WHERE symbol = ? AND trade_date = ?
                ORDER BY trade_date DESC LIMIT 1
            """, (symbol, today)).fetchone()

            if not price_row:
                # 用最新非今日数据
                price_row = db.execute("""
                    SELECT close FROM stock_daily_all
                    WHERE symbol = ?
                    ORDER BY trade_date DESC LIMIT 1
                """, (symbol,)).fetchone()

            if not price_row or not price_row["close"]:
                continue

            current_price = float(price_row["close"])
            entry_price = float(pos.get("entry_price", current_price))

            # 计算 ATR
            atr = calc_atr(symbol)
            if atr is None or atr <= 0:
                continue

            # ATR×2 止损线
            stop_price = entry_price - self.ATR_STOP_MULTIPLIER * atr

            if current_price <= stop_price:
                # 执行止损卖出
                shares = int(pos["shares"])
                amount = current_price * shares

                # 计算费用
                price_dec = Decimal(str(current_price))
                try:
                    comm, stamp, other = CommissionCalc.sell_cost(price_dec, shares, "HK")
                except Exception:
                    comm, stamp, other = Decimal("0"), Decimal("0"), Decimal("0")
                total_fee = float(comm + stamp + other)
                proceeds = amount - total_fee

                # 写入 trades 表
                db.execute("""
                    INSERT INTO quant_hk_trades (symbol, name, action, trade_date, price, shares, amount, reason, factor_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (symbol, pos["name"], "sell", today, current_price, shares,
                      round(amount, 2),
                      f"ATR×{self.ATR_STOP_MULTIPLIER}止损 (entry={entry_price}, atr={atr}, stop≤{stop_price:.2f})",
                      pos.get("factor_score")))
                db.commit()

                # 删除持仓
                db.execute("DELETE FROM quant_hk_position WHERE symbol = ?", (symbol,))
                db.commit()

                # 冷却
                self._start_cooling(symbol)

                results.append({
                    "symbol": symbol,
                    "name": pos["name"],
                    "action": "stop_loss",
                    "price": current_price,
                    "shares": shares,
                    "amount": round(amount, 2),
                    "proceeds": round(proceeds, 2),
                    "reason": f"ATR止损: entry={entry_price}, price={current_price}, stop≤{stop_price:.2f}",
                })

                print(f"  [quant_hk] 止损卖出 {pos['name']}({symbol}) "
                      f"@{current_price} | 入场 {entry_price} | ATR {atr} | 止损 ≤{stop_price:.2f}",
                      file=sys.stderr)

        return results

    def _execute_buys(self, db: sqlite3.Connection,
                      candidates: list[dict], today: str,
                      available_cash: float, total_asset: float,
                      current_positions: list[dict]) -> list[dict]:
        """执行买入

        按评分依次买入，直到资金用完或达到目标持仓数。

        资金分配：等权重分配，每只约 available_cash / target_new_positions
        """
        results = []
        target_new = max(1, self.TARGET_POSITIONS - len(current_positions))
        if target_new <= 0:
            return results

        # 等权重分配
        per_position_cash = available_cash / target_new
        bought_count = 0

        for c in candidates:
            if bought_count >= target_new:
                break
            if per_position_cash <= 0:
                break

            symbol = c["symbol"]
            name = c["name"]
            score = c["score"]

            # 获取最新价格
            price_row = db.execute("""
                SELECT close FROM stock_daily_all
                WHERE symbol = ?
                ORDER BY trade_date DESC LIMIT 1
            """, (symbol,)).fetchone()

            if not price_row or not price_row["close"]:
                continue

            price = float(price_row["close"])
            if price <= 0:
                continue

            # 计算可买股数（按整手，港股每手数量不一，简化按100股一手）
            lot_size = 100
            max_shares_by_cash = int(per_position_cash / price / lot_size) * lot_size

            if max_shares_by_cash < lot_size:
                continue

            shares = max_shares_by_cash
            amount = price * shares

            # 计算费用
            price_dec = Decimal(str(price))
            try:
                comm, stamp, other = CommissionCalc.buy_cost(price_dec, shares, "HK")
            except Exception:
                comm, stamp, other = Decimal("0"), Decimal("0"), Decimal("0")
            total_fee = float(comm + stamp + other)
            total_cost = amount + total_fee

            # 检查资金
            if total_cost > per_position_cash:
                # 减仓到整手能买得起的
                affordable = int(per_position_cash / price / lot_size) * lot_size
                if affordable < lot_size:
                    continue
                shares = affordable
                amount = price * shares
                try:
                    comm, stamp, other = CommissionCalc.buy_cost(price_dec, shares, "HK")
                except Exception:
                    comm, stamp, other = Decimal("0"), Decimal("0"), Decimal("0")
                total_fee = float(comm + stamp + other)
                total_cost = amount + total_fee

            # ATR 检查（若价格远高于合理区间则跳过）
            atr = calc_atr(symbol)
            if atr is not None and atr > 0:
                # 检查价格是否在 entry_price ± 3*ATR 合理区间
                # 没有 entry_price，用近20日均价
                avg_rows = db.execute("""
                    SELECT AVG(close) as avg_close FROM (
                        SELECT close FROM stock_daily_all
                        WHERE symbol = ?
                        ORDER BY trade_date DESC LIMIT 20
                    )
                """, (symbol,)).fetchone()
                if avg_rows and avg_rows["avg_close"]:
                    avg_price = float(avg_rows["avg_close"])
                    if price > avg_price + 3 * atr:
                        print(f"  [quant_hk] 跳过 {name}({symbol}): 价格 {price} 远超20日均价 {avg_price:.2f}+3ATR({3*atr:.2f})",
                              file=sys.stderr)
                        continue

            # 写入持仓
            target_weight = round(1.0 / self.TARGET_POSITIONS, 4) if self.TARGET_POSITIONS > 0 else 0.1

            db.execute("""
                INSERT OR REPLACE INTO quant_hk_position
                    (symbol, name, weight, shares, entry_price, entry_date,
                     target_weight, factor_score, last_rebalance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, name, 0.0, shares, price, today,
                  target_weight, score, today))
            db.commit()

            # 写 trades
            db.execute("""
                INSERT INTO quant_hk_trades (symbol, name, action, trade_date, price, shares, amount, reason, factor_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, name, "buy", today, price, shares,
                  round(amount, 2),
                  f"早盘调仓买入 (评分 {score})", score))
            db.commit()

            # 标记今日已买入
            self._today_buys[symbol] = datetime.now().timestamp()
            bought_count += 1

            results.append({
                "symbol": symbol,
                "name": name,
                "action": "buy",
                "price": price,
                "shares": shares,
                "amount": round(amount, 2),
                "cost": round(total_cost, 2),
                "score": score,
            })

            print(f"  [quant_hk] 买入 {name}({symbol}) {shares}股 @{price} | 评分 {score} | 金额 ¥{amount:,.2f}",
                  file=sys.stderr)

        return results

    def _rebalance_weights(self, db: sqlite3.Connection,
                           positions: list[dict], today: str,
                           cash: float, total_asset: float) -> list[dict]:
        """午盘权重调整

        不新开仓。对于评分下降或偏离目标权重的持仓：
          - 超配（weight > target_weight + 偏差容忍）→ 减仓
          - 低配 → 加仓（有资金且未 round-trip 前提下）
        """
        results = []
        if not positions:
            return results

        if total_asset is None or total_asset <= 0:
            return results

        # 加载所有标的的最新评分
        universe = self._load_hk_universe(db)
        universe_scores = {}
        for u in universe:
            universe_scores[u["symbol"]] = {"name": u["name"], "score": 50.0}

        # 评分（只对当前持仓和候选做）
        for pos in positions:
            sym = pos["symbol"]
            try:
                score = calc_factor_score(sym)
                if sym in universe_scores:
                    universe_scores[sym]["score"] = score
            except Exception:
                pass

        # 等权目标
        n_positions = len(positions)
        target_weight = 1.0 / n_positions if n_positions > 0 else 0
        tolerance = 0.02  # 2% 偏差容忍

        available_cash = cash

        for pos in positions:
            symbol = pos["symbol"]
            name = pos["name"]

            # 已 round-trip → 跳过
            if self._today_roundtrips.get(symbol, False):
                continue

            # 冷却中 → 跳过
            if self._is_cooling(symbol):
                continue

            current_weight = float(pos.get("weight", 0))
            score = universe_scores.get(symbol, {}).get("score", 50)
            factor_score = float(pos.get("factor_score", 50) or 50)
            current_price_row = db.execute("""
                SELECT close FROM stock_daily_all
                WHERE symbol = ?
                ORDER BY trade_date DESC LIMIT 1
            """, (symbol,)).fetchone()

            if not current_price_row or not current_price_row["close"]:
                continue

            price = float(current_price_row["close"])
            shares = int(pos["shares"])
            market_value = price * shares

            # 评分下降 → 考虑减仓
            if score < factor_score * 0.8 and current_weight > target_weight + tolerance:
                # 减仓 30%
                reduce_ratio = 0.3
                reduce_shares = max(100, int(shares * reduce_ratio / 100) * 100)
                if reduce_shares >= 100:
                    reduce_shares = min(reduce_shares, shares - 100)  # 至少留100股
                    if reduce_shares <= 0:
                        continue

                    amount = price * reduce_shares
                    price_dec = Decimal(str(price))
                    try:
                        comm, stamp, other = CommissionCalc.sell_cost(price_dec, reduce_shares, "HK")
                    except Exception:
                        comm, stamp, other = Decimal("0"), Decimal("0"), Decimal("0")
                    total_fee = float(comm + stamp + other)
                    proceeds = amount - total_fee

                    new_shares = shares - reduce_shares
                    new_weight = (market_value - amount) / total_asset

                    # 更新持仓
                    if new_shares <= 0:
                        db.execute("DELETE FROM quant_hk_position WHERE symbol = ?", (symbol,))
                    else:
                        db.execute("""
                            UPDATE quant_hk_position
                            SET weight = ?, shares = ?, last_rebalance = ?
                            WHERE symbol = ?
                        """, (round(new_weight, 4), new_shares, today, symbol))
                    db.commit()

                    # 写 trades
                    db.execute("""
                        INSERT INTO quant_hk_trades (symbol, name, action, trade_date, price, shares, amount, reason, factor_score)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (symbol, name, "sell", today, price, reduce_shares,
                          round(amount, 2),
                          f"午盘权重减仓 (评分 {score} < 原 {factor_score})",
                          score))
                    db.commit()

                    results.append({
                        "symbol": symbol,
                        "name": name,
                        "action": "reduce",
                        "price": price,
                        "shares": -reduce_shares,
                        "amount": round(amount, 2),
                        "reason": f"评分下降: {factor_score}→{score}",
                    })

                    print(f"  [quant_hk] 减仓 {name}({symbol}) {reduce_shares}股 @{price} | 评分 {factor_score}→{score}",
                          file=sys.stderr)

            # 评分大幅上升且低配 → 考虑加仓
            elif score > factor_score * 1.2 and current_weight < target_weight - tolerance:
                # 加仓至目标权重
                target_market_value = target_weight * total_asset
                add_value = target_market_value - market_value
                if add_value > 0 and available_cash > 0:
                    add_value = min(add_value, available_cash * 0.3)  # 不超过可用资金的30%
                    lot_size = 100
                    add_shares = int(add_value / price / lot_size) * lot_size
                    if add_shares >= lot_size:
                        amount = price * add_shares
                        price_dec = Decimal(str(price))
                        try:
                            comm, stamp, other = CommissionCalc.buy_cost(price_dec, add_shares, "HK")
                        except Exception:
                            comm, stamp, other = Decimal("0"), Decimal("0"), Decimal("0")
                        total_fee = float(comm + stamp + other)
                        total_cost = amount + total_fee

                        if total_cost <= available_cash:
                            new_shares = shares + add_shares
                            new_weight = (market_value + amount) / total_asset

                            db.execute("""
                                UPDATE quant_hk_position
                                SET weight = ?, shares = ?, factor_score = ?, last_rebalance = ?
                                WHERE symbol = ?
                            """, (round(new_weight, 4), new_shares, score, today, symbol))
                            db.commit()

                            db.execute("""
                                INSERT INTO quant_hk_trades (symbol, name, action, trade_date, price, shares, amount, reason, factor_score)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (symbol, name, "buy", today, price, add_shares,
                                  round(amount, 2),
                                  f"午盘权重加仓 (评分 {score} > 原 {factor_score})",
                                  score))
                            db.commit()

                            available_cash -= total_cost

                            results.append({
                                "symbol": symbol,
                                "name": name,
                                "action": "add",
                                "price": price,
                                "shares": add_shares,
                                "amount": round(amount, 2),
                                "reason": f"评分上升: {factor_score}→{score}",
                            })

                            print(f"  [quant_hk] 加仓 {name}({symbol}) {add_shares}股 @{price} | 评分 {factor_score}→{score}",
                                  file=sys.stderr)

        # 写回更新后的权重
        self._normalize_weights(db)

        return results

    def _normalize_weights(self, db: sqlite3.Connection):
        """归一化持仓权重，确保总和 ≈ 1.0"""
        positions = self._load_positions(db)
        if not positions:
            return

        total_weight = sum(float(p.get("weight", 0)) for p in positions)
        if total_weight <= 0:
            return

        for p in positions:
            norm_weight = float(p["weight"]) / total_weight
            db.execute("""
                UPDATE quant_hk_position SET weight = ? WHERE symbol = ?
            """, (round(norm_weight, 4), p["symbol"]))
        db.commit()

    # ---- 辅助方法 ----

    def get_current_state(self) -> dict:
        """获取当前量化盘状态"""
        db = self._get_db()
        try:
            positions = self._load_positions(db)
            cash, total_asset = self._load_nav(db)

            # 获取最新 NAV 日收益率
            nav_rows = db.execute("""
                SELECT trade_date, nav, daily_return FROM quant_hk_nav
                ORDER BY trade_date DESC LIMIT 5
            """).fetchall()

            # 获取今日交易
            today = date.today().isoformat()
            trades = db.execute("""
                SELECT action, COUNT(*) as cnt, SUM(amount) as total_amount
                FROM quant_hk_trades
                WHERE trade_date = ?
                GROUP BY action
            """, (today,)).fetchall()

            return {
                "date": today,
                "positions": positions,
                "position_count": len(positions),
                "cash": round(cash, 2) if cash else 0,
                "total_asset": round(total_asset, 2) if total_asset else 0,
                "nav_history": [dict(r) for r in nav_rows],
                "today_trades": [dict(r) for r in trades],
                "morning_done": self._morning_done,
                "afternoon_done": self._afternoon_done,
                "cooling": list(self._cooling_symbols),
            }
        finally:
            db.close()


# ============================================================
# CLI 入口
# ============================================================

def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="港股量化盘引擎")
    parser.add_argument("action", choices=["morning", "afternoon", "state", "score"],
                        help="执行动作: morning(09:35调仓), afternoon(14:00调仓), state(查看状态), score(评分测试)")
    parser.add_argument("--db", default="", help="screener.db 路径")
    parser.add_argument("--capital", type=float, default=1_000_000, help="初始资金")
    parser.add_argument("--symbol", default="", help="评分测试用的股票代码")

    args = parser.parse_args()

    engine = QuantHKExecutor(db_path=args.db, initial_capital=args.capital)

    if args.action == "morning":
        result = engine.execute_morning_rebalance()
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.action == "afternoon":
        result = engine.execute_afternoon_rebalance()
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.action == "state":
        state = engine.get_current_state()
        import json
        print(json.dumps(state, ensure_ascii=False, indent=2, default=str))

    elif args.action == "score":
        symbol = args.symbol or "00700"
        score = calc_factor_score(symbol)
        atr = calc_atr(symbol)
        print(f"📊 {symbol} 评分: {score}")
        print(f"📏 ATR(14): {atr}")


if __name__ == "__main__":
    main()
