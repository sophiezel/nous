"""模拟盘交易系统 — 卖出纪律引擎

五大卖出规则，作为 executor.py 和 trader_poll.py 的可选增强层。
不破坏现有卖出逻辑，新增为独立可调用的纪律检查。

用法：
    from sell_discipline import check_sell_signals, resolve_sell_signals

    for pos in portfolio.positions.values():
        signals = check_sell_signals(pos, {"current_price": price, "theme_status": "watch"})
        final = resolve_sell_signals(signals)
        if final and final.urgency in ("immediate", "today"):
            executor._execute_sell(pos, price, final.reason)

依赖：
    - sqlite3（直连 screener.db 查日线和基本面）
    - 不依赖 pandas / akshare
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional
import sqlite3
import os


# ============================================================
# 数据库路径
# ============================================================

SCREENER_DB = os.path.expanduser("~/code/stock-screener/data/screener.db")


# ============================================================
# 数据结构
# ============================================================

@dataclass
class ExitSignal:
    """卖出信号

    Attributes:
        reason:  触发原因，如 'hard_stop_full' / 'trailing_stop' / 'time_stop_30d_loss'
        urgency: 执行紧迫度，'immediate' | 'today' | 'this_week'
        action:  卖出操作，'sell_all' | 'sell_half' | 'reduce_to_30pct'
        detail:  详细说明（含数字）
        symbol:  股票代码
    """
    reason: str
    urgency: str       # 'immediate' | 'today' | 'this_week'
    action: str        # 'sell_all' | 'sell_half' | 'reduce_to_30pct'
    detail: str        # 详细说明
    symbol: str = ''


# ============================================================
# ATR 计算（从 stock_daily 表查询日线）
# ============================================================

def query_daily_bars(symbol: str, limit: int = 20) -> list[dict]:
    """从 stock_daily 表查询最近 N 个交易日数据

    Args:
        symbol: 股票代码，如 '600519'
        limit:  查询天数（含当天）

    Returns:
        升序排列的日线列表，每项含 trade_date, high, low, close
        数据库不存在或查询失败时返回空列表
    """
    db_path = os.path.expanduser(SCREENER_DB)
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT trade_date, high, low, close FROM stock_daily "
            "WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?",
            (symbol, limit)
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return []
        # 反转使日期升序（最旧在前）
        rows.reverse()
        return [
            {
                "trade_date": r[0],
                "high": float(r[1] or 0),
                "low": float(r[2] or 0),
                "close": float(r[3] or 0),
            }
            for r in rows
        ]
    except Exception:
        return []


def compute_atr_from_bars(bars: list[dict], period: int = 14) -> Decimal:
    """从日线数据计算 ATR(period)

    TR = max(H-L, |H-C_prev|, |L-C_prev|)
    ATR = TR 的 EMA(period)，初始值用简单平均

    Args:
        bars:  日线列表（至少 period+1 条）
        period: ATR 周期，默认 14

    Returns:
        Decimal，数据不足时返回 Decimal('0')
    """
    if len(bars) < period + 1:
        return Decimal("0")

    tr_list = []
    for i in range(1, len(bars)):
        h = bars[i]["high"]
        l = bars[i]["low"]
        c_prev = bars[i - 1]["close"]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_list.append(tr)

    if len(tr_list) < period:
        return Decimal("0")

    # EMA 初始值用简单平均
    initial_atr = sum(tr_list[:period]) / period
    multiplier = 2.0 / (period + 1)

    atr = initial_atr
    for tr in tr_list[period:]:
        atr = (tr - atr) * multiplier + atr

    return Decimal(str(round(atr, 4)))


# ============================================================
# 基本面查询（从 stock_fundamental 表）
# ============================================================

def query_fundamental(symbol: str) -> dict:
    """查询最新基本面数据

    Args:
        symbol: 股票代码

    Returns:
        dict，含 pe_ttm, roe, snapshot_date 等字段
        若无数据返回空 dict
    """
    db_path = os.path.expanduser(SCREENER_DB)
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT pe, roe, pe_static, pe_dynamic, snapshot_date "
            "FROM stock_fundamental WHERE symbol = ?",
            (symbol,)
        )
        row = cur.fetchone()
        conn.close()
        if row is None:
            return {}

        pe, roe, pe_static, pe_dynamic, snap_date = row
        result = {}
        # 优先用 pe_dynamic（TTM）
        if pe_dynamic is not None and pe_dynamic > 0:
            result["pe_ttm"] = float(pe_dynamic)
        elif pe is not None and pe > 0:
            result["pe_ttm"] = float(pe)
        if roe is not None:
            result["roe"] = float(roe)
        result["snapshot_date"] = snap_date or ""
        return result
    except Exception:
        return {}


# ============================================================
# 卖出纪律引擎
# ============================================================

class SellDiscipline:
    """卖出纪律引擎

    五大规则按优先级依次检查，返回 ExitSignal 列表。
    由 resolve() 合并为最终执行信号。

    与现有系统关系：
        - Executor.execute_exits() 使用 risk.evaluate_exit_signals()
          处理基础的止损/止盈/移动止盈/时间止损
        - SellDiscipline 是可选增强层，增加 ATR 硬止损、精细化移动止盈、
          时间分层止损、基本面恶化检测、主线退潮检测
        - 可在 trader_poll.py 中额外调用，不影响现有逻辑
    """

    def __init__(self, db_path: str = ""):
        self.db_path = db_path or os.path.expanduser(SCREENER_DB)

        # 持仓期间最高价追踪（与 Position.highest_price 同步）
        self.trailing_highs: dict[str, float] = {}

        # 买入成本价缓存 {symbol: float}
        self.buy_prices: dict[str, float] = {}

        # 买入日期缓存 {symbol: str}
        self.buy_dates: dict[str, str] = {}

        # 基线基本面 {symbol: {'pe_ttm': float, 'roe': float}}
        # 首次检查时记录，后续用于比较
        self._baseline_fundamentals: dict[str, dict] = {}

    # ---- 公开接口 ----

    def check_all(self, position, market_data: dict) -> list[ExitSignal]:
        """返回所有触发的卖出信号

        Args:
            position:   Position 对象（需有 symbol, entry_price, entry_date,
                       current_price, highest_price, days_held 等属性）
            market_data: 市场数据字典
                - 'current_price': Decimal (可选，默认用 position.current_price)
                - 'theme_status':  str (可选，'confirmed'|'potential'|'watch'|'skip')
                - 'price_float':   float (备用)

        Returns:
            list[ExitSignal]: 所有触发的卖出信号列表
        """
        symbol = position.symbol

        # 同步缓存
        entry_price_float = float(position.entry_price)
        self.buy_prices[symbol] = entry_price_float
        self.buy_dates[symbol] = position.entry_date

        # 更新最高价追踪
        current_high = float(position.highest_price or position.current_price)
        if symbol in self.trailing_highs:
            self.trailing_highs[symbol] = max(
                self.trailing_highs[symbol], current_high
            )
        else:
            self.trailing_highs[symbol] = current_high

        signals = []

        # 依次检查各规则（优先级由高到低）
        signals.extend(self._check_hard_stop(position, market_data))
        signals.extend(self._check_trailing_stop(position, market_data))
        signals.extend(self._check_time_stop(position, market_data))
        signals.extend(self._check_fundamental_deterioration(position, market_data))
        signals.extend(self._check_theme_fade(position, market_data))

        return signals

    def resolve(self, signals: list[ExitSignal]) -> Optional[ExitSignal]:
        """合并多个信号为最终执行信号

        优先级规则：
            urgency:  immediate > today > this_week
            action:   sell_all > sell_half > reduce_to_30pct

        Args:
            signals: ExitSignal 列表

        Returns:
            Optional[ExitSignal]: None 如果没有信号
        """
        if not signals:
            return None

        urgency_rank = {"immediate": 0, "today": 1, "this_week": 2}
        action_rank = {"sell_all": 0, "sell_half": 1, "reduce_to_30pct": 2}

        # 取最高 urgency
        best_urgency = min(signals, key=lambda s: urgency_rank.get(s.urgency, 99))

        # 在同 urgency 中取最激进 action
        same_urgency = [
            s for s in signals
            if s.urgency == best_urgency.urgency
        ]
        best_action = min(
            same_urgency, key=lambda s: action_rank.get(s.action, 99)
        )

        # 合并所有 detail
        all_details = "; ".join(
            s.detail for s in signals if s.detail
        )

        # 取第一条信号的 reason（最紧急的）
        best_reason = best_urgency.reason
        # 如果同 urgency 下 action 更激进，用它的 reason
        if best_action.reason != best_reason:
            best_reason = best_action.reason

        return ExitSignal(
            reason=best_reason,
            urgency=best_urgency.urgency,
            action=best_action.action,
            detail=all_details or best_urgency.detail,
            symbol=best_urgency.symbol or best_action.symbol,
        )

    # ---- 规则1: 硬止损 ----

    def _check_hard_stop(self, position, market_data: dict) -> list[ExitSignal]:
        """硬止损：基于 ATR 的价位止损

        规则细节：
            ATR止损位 = 买入价 - 2 x ATR(14)
            - 当前价 <= 买入价 - 2 x ATR  → sell_all (immediate)
            - 当前价 <= 买入价 - 1.5 x ATR → sell_half (immediate)

        同时满足两条件时返回两个信号，由 resolve() 取最激进。
        若 ATR 计算失败（数据不足）则静默跳过。
        """
        symbol = position.symbol
        entry = float(position.entry_price)
        if entry <= 0:
            return []

        # 获取当前价格
        current = self._get_current_price(position, market_data)
        if current is None:
            return []

        # 计算 ATR
        atr = self._get_atr(symbol, period=14)
        if atr <= 0:
            return []

        signals = []

        # 半仓止损线: 买入价 - 1.5 x ATR
        half_stop = entry - 1.5 * atr
        if current <= half_stop:
            signals.append(ExitSignal(
                reason="hard_stop_half",
                urgency="immediate",
                action="sell_half",
                detail=(
                    f"触发半仓硬止损：当前{current:.2f} <= "
                    f"{half_stop:.2f}（买入{entry:.2f} - 1.5xATR({atr:.2f})）"
                ),
                symbol=symbol,
            ))

        # 全仓止损线: 买入价 - 2 x ATR
        full_stop = entry - 2.0 * atr
        if current <= full_stop:
            signals.append(ExitSignal(
                reason="hard_stop_full",
                urgency="immediate",
                action="sell_all",
                detail=(
                    f"触发全仓硬止损：当前{current:.2f} <= "
                    f"{full_stop:.2f}（买入{entry:.2f} - 2xATR({atr:.2f})）"
                ),
                symbol=symbol,
            ))

        return signals

    # ---- 规则2: 移动止盈 ----

    def _check_trailing_stop(self, position, market_data: dict) -> list[ExitSignal]:
        """移动止盈：涨幅越大，回调容忍度越低

        规则细节：
            - 涨超 8%  → 止损设在最高价 - 3%
            - 涨超 12% → 止损设在最高价 - 2%
            - 涨超 20% → 止损设在最高价 - 1.5%
            - 当前价跌破上述止损位 → sell_all, urgency='today'

        内部维护 trailing_highs 字典追踪持仓期间最高价。
        """
        symbol = position.symbol
        entry = float(position.entry_price)
        if entry <= 0:
            return []

        current = self._get_current_price(position, market_data)
        if current is None:
            return []

        # 更新最高价
        if symbol in self.trailing_highs:
            self.trailing_highs[symbol] = max(
                self.trailing_highs[symbol], current
            )
        else:
            self.trailing_highs[symbol] = current

        high = self.trailing_highs[symbol]
        if high <= entry:
            return []

        gain_pct = (high - entry) / entry

        # 根据涨幅确定回调敏感度
        if gain_pct >= 0.20:
            callback = 0.015   # 涨超20%：回调1.5%即走
        elif gain_pct >= 0.12:
            callback = 0.02    # 涨超12%：回调2%即走
        elif gain_pct >= 0.08:
            callback = 0.03    # 涨超8%：回调3%即走
        else:
            return []          # 涨幅不足8%，不移止盈

        stop_price = high * (1 - callback)

        if current <= stop_price:
            return [ExitSignal(
                reason="trailing_stop",
                urgency="today",
                action="sell_all",
                detail=(
                    f"移动止盈触发：涨幅{gain_pct*100:.1f}%，"
                    f"从最高{high:.2f}回调到{current:.2f}，"
                    f"跌破{stop_price:.2f}（{callback*100:.1f}%回调线）"
                ),
                symbol=symbol,
            )]

        return []

    # ---- 规则3: 时间止损 ----

    def _check_time_stop(self, position, market_data: dict) -> list[ExitSignal]:
        """时间止损：持仓时间过长但表现不佳

        规则细节：
            - 买入 > 10 个交易日，涨幅 < 3%   → sell_half,   urgency='today'
            - 买入 > 20 个交易日，涨幅 < 5%   → sell_all,    urgency='this_week'
            - 买入 > 30 个交易日，仍在亏损     → sell_all,    urgency='today'

        持仓天数优先用 position.days_held，若无则从 entry_date 计算自然天数。
        多条规则同时满足时返回多个信号，由 resolve() 合并。
        """
        symbol = position.symbol
        entry = float(position.entry_price)
        if entry <= 0:
            return []

        current = self._get_current_price(position, market_data)
        if current is None:
            return []

        # 实际持仓天数优先用 position.days_held
        days_held = getattr(position, 'days_held', 0)
        if days_held <= 0 and position.entry_date:
            try:
                entry_dt = datetime.strptime(
                    position.entry_date, "%Y-%m-%d"
                ).date()
                days_held = (date.today() - entry_dt).days
            except (ValueError, TypeError):
                pass

        if days_held <= 0:
            return []

        gain_pct = (current - entry) / entry

        signals = []

        # 买入 > 10 个交易日，涨幅 < 3%
        if days_held > 10 and gain_pct < 0.03:
            signals.append(ExitSignal(
                reason="time_stop_10d",
                urgency="today",
                action="sell_half",
                detail=(
                    f"时间止损(10d)：持仓{days_held}天，"
                    f"涨幅{gain_pct*100:.2f}%（<3%），建议减半"
                ),
                symbol=symbol,
            ))

        # 买入 > 20 个交易日，涨幅 < 5%
        if days_held > 20 and gain_pct < 0.05:
            signals.append(ExitSignal(
                reason="time_stop_20d",
                urgency="this_week",
                action="sell_all",
                detail=(
                    f"时间止损(20d)：持仓{days_held}天，"
                    f"涨幅{gain_pct*100:.2f}%（<5%），建议清仓"
                ),
                symbol=symbol,
            ))

        # 买入 > 30 个交易日，仍在亏损
        if days_held > 30 and gain_pct < 0:
            signals.append(ExitSignal(
                reason="time_stop_30d_loss",
                urgency="today",
                action="sell_all",
                detail=(
                    f"时间止损(30d)：持仓{days_held}天，"
                    f"亏损{gain_pct*100:.2f}%，强制清仓"
                ),
                symbol=symbol,
            ))

        return signals

    # ---- 规则4: 基本面恶化 ----

    def _check_fundamental_deterioration(
        self, position, market_data: dict
    ) -> list[ExitSignal]:
        """基本面恶化检查

        规则细节：
            - PE_TTM 翻倍（vs 买入时基线） → reduce_to_30pct, urgency='this_week'
            - ROE 腰斩                    → sell_half,       urgency='this_week'

        基线数据在首次检查时从 stock_fundamental 表获取并缓存。
        后续检查对比当前数据与基线。
        若数据库无数据（首次买入无基线），静默跳过。
        """
        symbol = position.symbol

        # 获取当前基本面
        current_fund = query_fundamental(symbol)
        if not current_fund:
            return []   # 无数据，静默跳过

        signals = []

        # 获取/初始化基线
        if symbol not in self._baseline_fundamentals:
            # 首次遇到此股，记录当前为基线
            self._baseline_fundamentals[symbol] = {
                "pe_ttm": current_fund.get("pe_ttm"),
                "roe": current_fund.get("roe"),
            }
            return []   # 首次无比较基准，跳过

        baseline = self._baseline_fundamentals[symbol]

        # PE_TTM 翻倍检查
        baseline_pe = baseline.get("pe_ttm")
        current_pe = current_fund.get("pe_ttm")
        if (
            baseline_pe is not None
            and current_pe is not None
            and baseline_pe > 0
        ):
            pe_ratio = current_pe / baseline_pe
            if pe_ratio >= 2.0:
                signals.append(ExitSignal(
                    reason="fundamental_pe_double",
                    urgency="this_week",
                    action="reduce_to_30pct",
                    detail=(
                        f"PE_TTM翻倍：{baseline_pe:.1f} -> {current_pe:.1f}"
                        f"（{pe_ratio:.1f}x），基本面恶化减仓至30%"
                    ),
                    symbol=symbol,
                ))

        # ROE 腰斩检查
        baseline_roe = baseline.get("roe")
        current_roe = current_fund.get("roe")
        if baseline_roe is not None and current_roe is not None:
            if baseline_roe > 0 and current_roe < baseline_roe / 2:
                signals.append(ExitSignal(
                    reason="fundamental_roe_halved",
                    urgency="this_week",
                    action="sell_half",
                    detail=(
                        f"ROE腰斩：{baseline_roe:.2f}% -> {current_roe:.2f}%"
                        f"，盈利能力恶化减半仓"
                    ),
                    symbol=symbol,
                ))

        # 注意：基线一旦设定不再更新，确保比较基准一致

        return signals

    # ---- 规则5: 主线退潮 ----

    def _check_theme_fade(self, position, market_data: dict) -> list[ExitSignal]:
        """主线退潮检查

        规则细节：
            theme_status 由外部传入，可选值：
            - 'confirmed' | 'potential' : 活跃/潜在主线，不减仓
            - 'watch'                   : 退潮监控 → sell_half, this_week
            - 'skip'                    : 已退出主线 → sell_all, today

        若无 theme_status 数据（未传入），静默跳过。
        """
        symbol = position.symbol
        theme_status = market_data.get("theme_status", "")
        if not theme_status:
            return []   # 无主线状态数据，跳过

        theme_value = getattr(position, 'theme', '') or market_data.get("theme", "")

        signals = []

        if theme_status == "watch":
            signals.append(ExitSignal(
                reason="theme_fade_watch",
                urgency="this_week",
                action="sell_half",
                detail=(
                    f"主线退潮(watch)：{theme_value} "
                    f"已进入退潮观察期，减半仓"
                ),
                symbol=symbol,
            ))
        elif theme_status == "skip":
            signals.append(ExitSignal(
                reason="theme_fade_skip",
                urgency="today",
                action="sell_all",
                detail=(
                    f"主线退潮(skip)：{theme_value} "
                    f"已退出主线名单，全仓清退"
                ),
                symbol=symbol,
            ))
        # 'confirmed' / 'potential': 不减仓，也不产生信号

        return signals

    # ---- 内部辅助 ----

    def _get_current_price(
        self, position, market_data: dict
    ) -> Optional[float]:
        """从 position 或 market_data 获取当前价格浮点数"""
        # 优先 market_data
        price = market_data.get("current_price")
        if price is not None:
            try:
                return float(price)
            except (TypeError, ValueError):
                pass

        # fallback: position.current_price
        try:
            cp = getattr(position, 'current_price', None)
            if cp is not None:
                return float(cp)
        except (TypeError, ValueError):
            pass

        # fallback: market_data 中的 float 价格
        price = market_data.get("price_float")
        if price is not None:
            try:
                return float(price)
            except (TypeError, ValueError):
                pass

        return None

    def _get_atr(self, symbol: str, period: int = 14) -> float:
        """查询并计算 ATR

        数据不足时返回 0.0（静默失败）
        """
        bars = query_daily_bars(symbol, limit=period + 5)
        atr = compute_atr_from_bars(bars, period)
        return float(atr)

    def reset_position(self, symbol: str):
        """重置某只股票的内部状态（清仓后调用）"""
        self.trailing_highs.pop(symbol, None)
        self.buy_prices.pop(symbol, None)
        self.buy_dates.pop(symbol, None)
        self._baseline_fundamentals.pop(symbol, None)

    def reset_all(self):
        """重置所有内部状态"""
        self.trailing_highs.clear()
        self.buy_prices.clear()
        self.buy_dates.clear()
        self._baseline_fundamentals.clear()


# ============================================================
# 便捷函数接口（供 executor.py / trader_poll.py 调用）
# ============================================================

_global_sell_discipline: Optional[SellDiscipline] = None


def get_sell_discipline(db_path: str = "") -> SellDiscipline:
    """获取/创建全局卖出纪律引擎单例"""
    global _global_sell_discipline
    if _global_sell_discipline is None:
        _global_sell_discipline = SellDiscipline(db_path=db_path)
    return _global_sell_discipline


def check_sell_signals(
    position, market_data: dict, db_path: str = ""
) -> list[ExitSignal]:
    """便捷函数：检查一个持仓的所有卖出信号

    Args:
        position:    Position 对象
        market_data: dict，至少需包含 current_price
        db_path:     screener.db 路径（默认 ~/code/stock-screener/data/screener.db）

    Returns:
        list[ExitSignal]
    """
    sd = get_sell_discipline(db_path)
    return sd.check_all(position, market_data)


def resolve_sell_signals(signals: list[ExitSignal]) -> Optional[ExitSignal]:
    """便捷函数：合并卖出信号为最终执行信号

    Args:
        signals: ExitSignal 列表

    Returns:
        Optional[ExitSignal]: None 表示无信号
    """
    sd = get_sell_discipline()
    return sd.resolve(signals)


# ============================================================
# 独立演示
# ============================================================

if __name__ == "__main__":
    import sys

    print("=" * 64)
    print("  卖出纪律引擎 SellDiscipline — 单元演示")
    print("=" * 64)

    # 动态导入 Position（同级模块）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from portfolio import Position as _Pos
    from decimal import Decimal as _D

    sd = SellDiscipline()

    # -------------------------------------------------------
    # 场景 1: 硬止损触发
    # -------------------------------------------------------
    print("\n--- 场景 1: 硬止损触发（半仓线 + 全仓线）---")
    # entry=10.00, ATR ≈ 0.50 (模拟), 1.5xATR=9.25, 2xATR=9.00
    # current=9.10 → 跌穿两条线 → 返回两个信号
    pos1 = _Pos(
        symbol="000001", name="测试A", market="A", strategy="short_term",
        entry_price=_D("10.00"), entry_date="2026-05-01",
        current_price=_D("9.10"), shares=1000, total_cost=_D("10000"),
        highest_price=_D("10.00"), days_held=5,
    )
    sigs1 = sd.check_all(pos1, {"current_price": _D("9.10")})
    for s in sigs1:
        print(f"  [{s.urgency:10s}] {s.action:15s} | {s.reason}")
        print(f"    {s.detail}")
    final1 = sd.resolve(sigs1)
    if final1:
        print(f"  >> 合并: [{final1.urgency}] {final1.action} | {final1.reason}")

    # -------------------------------------------------------
    # 场景 2: 移动止盈触发
    # -------------------------------------------------------
    print("\n--- 场景 2: 移动止盈触发（涨超20%，回调1.5%）---")
    # entry=10, high=12 (+20%), callback = 1.5%, stop=12*0.985=11.82
    # current=11.50 < 11.82 → 触发
    sd2 = SellDiscipline()
    pos2 = _Pos(
        symbol="000002", name="测试B", market="A", strategy="short_term",
        entry_price=_D("10.00"), entry_date="2026-04-15",
        current_price=_D("11.50"), shares=1000, total_cost=_D("10000"),
        highest_price=_D("12.00"), days_held=10,
    )
    sigs2 = sd2.check_all(pos2, {"current_price": _D("11.50")})
    for s in sigs2:
        print(f"  [{s.urgency:10s}] {s.action:15s} | {s.reason}")
        print(f"    {s.detail}")

    # -------------------------------------------------------
    # 场景 3: 时间止损
    # -------------------------------------------------------
    print("\n--- 场景 3: 时间止损（30天+亏损）---")
    pos3 = _Pos(
        symbol="000003", name="测试C", market="A", strategy="short_term",
        entry_price=_D("20.00"), entry_date="2026-04-01",
        current_price=_D("19.50"), shares=1000, total_cost=_D("20000"),
        highest_price=_D("20.00"), days_held=35,
    )
    sigs3 = sd.check_all(pos3, {"current_price": _D("19.50")})
    for s in sigs3:
        print(f"  [{s.urgency:10s}] {s.action:15s} | {s.reason}")
        print(f"    {s.detail}")

    # -------------------------------------------------------
    # 场景 4: 无触发信号
    # -------------------------------------------------------
    print("\n--- 场景 4: 正常持仓（无触发信号）---")
    pos4 = _Pos(
        symbol="000004", name="测试D", market="A", strategy="short_term",
        entry_price=_D("15.00"),
        entry_date=(date.today() - timedelta(days=2)).isoformat(),
        current_price=_D("15.10"), shares=1000, total_cost=_D("15000"),
        highest_price=_D("15.10"), days_held=2,
    )
    sigs4 = sd.check_all(pos4, {"current_price": _D("15.10")})
    if not sigs4:
        print("  ✅ 无触发信号（正常持仓）")
    else:
        for s in sigs4:
            print(f"  [{s.urgency}] {s.action} | {s.reason}")

    # -------------------------------------------------------
    # 场景 5: 主线退潮
    # -------------------------------------------------------
    print("\n--- 场景 5: 主线退潮（skip → 清仓）---")
    pos5 = _Pos(
        symbol="000005", name="测试E", market="A", strategy="short_term",
        entry_price=_D("8.00"), entry_date="2026-05-01",
        current_price=_D("8.20"), shares=1000, total_cost=_D("8000"),
        highest_price=_D("8.50"), days_held=8, theme="AI产业链",
    )
    sigs5 = sd.check_all(
        pos5, {"current_price": _D("8.20"), "theme_status": "skip"}
    )
    for s in sigs5:
        print(f"  [{s.urgency:10s}] {s.action:15s} | {s.reason}")
        print(f"    {s.detail}")

    # -------------------------------------------------------
    # 场景 6: 多重信号合并
    # -------------------------------------------------------
    print("\n--- 场景 6: 多重信号合并（时间止损 + 主线退潮）---")
    pos6 = _Pos(
        symbol="000006", name="测试F", market="A", strategy="short_term",
        entry_price=_D("50.00"), entry_date="2026-03-01",
        current_price=_D("51.00"), shares=1000, total_cost=_D("50000"),
        highest_price=_D("52.00"), days_held=45, theme="新能源",
    )
    sigs6 = sd.check_all(
        pos6, {"current_price": _D("51.00"), "theme_status": "skip"}
    )
    for s in sigs6:
        print(f"  [{s.urgency:10s}] {s.action:15s} | {s.reason}")
    final6 = sd.resolve(sigs6)
    if final6:
        print(f"  >> 合并: [{final6.urgency}] {final6.action} | {final6.reason}")
        print(f"     {final6.detail}")

    # -------------------------------------------------------
    # 场景 7: resolve 优先级测试
    # -------------------------------------------------------
    print("\n--- 场景 7: resolve 优先级 ---")
    from sell_discipline import ExitSignal as _ES
    test_signals = [
        _ES(reason="time_stop_20d", urgency="this_week", action="sell_all",
            detail="测试优先级", symbol="000007"),
        _ES(reason="trailing_stop", urgency="today", action="sell_all",
            detail="今日移动止盈", symbol="000007"),
        _ES(reason="hard_stop_half", urgency="immediate", action="sell_half",
            detail="即时半仓止损", symbol="000007"),
    ]
    final7 = sd.resolve(test_signals)
    print(f"  输入: 3 signals (this_week/sell_all, today/sell_all, immediate/sell_half)")
    print(f"  结果: [{final7.urgency}] {final7.action} | {final7.reason}")
    # 最高 urgency = immediate, 该层级下最激进 action = sell_half（唯一）
    assert final7.urgency == "immediate", "immediate 应为最高优先级"
    assert final7.action == "sell_half", "immediate 层级下最激进操作为 sell_half"
    print(f"  ✅ 优先级正确（immediate > today > this_week, 先选urgency）")

    # -------------------------------------------------------
    # 场景 8: 数据库查询演示（非必须，展示接口）
    # -------------------------------------------------------
    print("\n--- 场景 8: 数据库查询演示 ---")
    db_path = os.path.expanduser("~/code/stock-screener/data/screener.db")
    if os.path.exists(db_path):
        bars = query_daily_bars("600519", limit=20)
        print(f"  600519 日线: {len(bars)} 条")
        if bars:
            atr = compute_atr_from_bars(bars)
            print(f"  ATR(14): {atr}")
            print(f"  最新: {bars[-1]}")
        fund = query_fundamental("600519")
        print(f"  基本面: {fund}")
    else:
        print(f"  数据库不存在，跳过: {db_path}")

    # -------------------------------------------------------
    # 场景 9: reset 功能
    # -------------------------------------------------------
    print("\n--- 场景 9: reset_position 和 resolve(空列表) ---")
    sd.reset_position("000001")
    print(f"  000001 已重置, trailing_highs 中 keys: {list(sd.trailing_highs.keys())}")
    assert sd.resolve([]) is None, "空列表 resolve 应为 None"
    print("  ✅ resolve([]) -> None")

    print("\n" + "=" * 64)
    print("  全部演示通过")
    print("=" * 64)
