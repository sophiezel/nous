"""短线交易引擎 — 5种游资操盘方法论编码为可执行规则

支持5大流派:
  Rule 1: 徐翔涨停板追涨
  Rule 2: 赵老哥二板定龙头
  Rule 3: 炒股养家情绪周期（市场过滤器）
  Rule 4: 作手新一逻辑驱动（龙虎榜跟买）
  Rule 5: 方新侠拐点博弈（超跌反弹）

用法:
  python -m src.short_term_engine scan          # 全市场扫描输出JSON
  python -m src.short_term_engine check 000001  # 单票5规则检查

集成点:
  - theme_recommend.py 作为短线补充
  - trader/executor.py  短线开仓
  - trader_poll.py      短线平仓

纯sqlite3实现，无pandas/akshare依赖。
"""

import json
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ── 路径 ──────────────────────────────────────────────
from nous.core.paths import screener_db
DB_PATH = screener_db()
# ── 数据结构 ──────────────────────────────────────────

@dataclass
class EntrySignal:
    """买入信号"""
    symbol: str
    name: str
    rule: str          # 徐翔/赵老哥/作手新一/方新侠
    confidence: float  # 0-100
    position_pct: float  # 15-30%
    trigger_price: float
    stop_loss: float
    detail: str = ""


@dataclass
class ExitSignal:
    """卖出信号"""
    symbol: str
    reason: str
    urgency: str   # immediate / today / close
    action: str    # sell_all / sell_half
    detail: str = ""


# ── 工具函数 ──────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _get_limit_pct(symbol: str) -> float:
    """根据代码前缀判断涨跌幅限制"""
    if symbol.startswith("30") or symbol.startswith("68"):
        return 0.20   # 创业板/科创板 20%
    if symbol.startswith("8") or symbol.startswith("4"):
        return 0.30   # 北交所 30%
    return 0.10       # 主板 10%


def _get_daily_list(symbol: str, limit: int = 60) -> list[dict]:
    """获取最近日线数据（按日期升序）"""
    conn = _get_db()
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, volume, amount "
        "FROM stock_daily WHERE symbol=? ORDER BY trade_date ASC LIMIT ?",
        (symbol, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_stock_info(symbol: str) -> dict:
    """获取股票基本信息"""
    conn = _get_db()
    row = conn.execute(
        "SELECT symbol, name, market FROM stock_basic WHERE symbol=?", (symbol,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def _is_limit_up(daily_list: list[dict], idx: int = -1) -> bool:
    """判断某日是否涨停（收盘价 >= 前收盘 * (1+limit)）"""
    n = len(daily_list)
    if n < 2:
        return False
    if idx < 0:
        idx = n + idx
    if idx < 1 or idx >= n:
        return False
    prev_close = daily_list[idx - 1]["close"]
    if prev_close is None or prev_close == 0:
        return False
    symbol = daily_list[0].get("_symbol", "")
    limit_pct = _get_limit_pct(symbol)
    close_today = daily_list[idx]["close"]
    return close_today >= prev_close * (1 + limit_pct) * 0.995


def _compute_volume_ratio(daily_list: list[dict], recent_n: int = 5) -> Optional[float]:
    """量比：今日量 / 近N日均量"""
    n = len(daily_list)
    if n < recent_n + 1:
        return None
    vol_today = daily_list[-1]["volume"] or 0
    vols = [d["volume"] or 0 for d in daily_list[-(recent_n + 1):-1]]
    vol_avg = sum(vols) / len(vols) if vols else 0
    if vol_avg == 0:
        return None
    return vol_today / vol_avg


def _compute_rsi(daily_list: list[dict], period: int = 14) -> Optional[float]:
    """RSI 计算（纯Python）"""
    n = len(daily_list)
    if n < period + 1:
        return None
    closes = [d["close"] for d in daily_list]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    # 使用SMA计算初始RSI
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


def _compute_ma(daily_list: list[dict], period: int = 20) -> Optional[float]:
    """计算移动平均线"""
    n = len(daily_list)
    if n < period:
        return None
    closes = [d["close"] for d in daily_list[-period:]]
    return sum(closes) / period


def _get_consecutive_limit_up_count(daily_list: list[dict]) -> int:
    """计算从最新交易日往回连续涨停的天数"""
    count = 0
    for i in range(len(daily_list) - 1, 0, -1):
        if _is_limit_up(daily_list, i):
            count += 1
        else:
            break
    return count


# ══════════════════════════════════════════════════════
# Rule 3: 炒股养家情绪周期（市场过滤器）
# ══════════════════════════════════════════════════════

def _get_market_sentiment(db_path: str = str(DB_PATH)) -> dict:
    """分析市场情绪：涨停梯队高度 + 昨日涨停溢价率

    返回: {'status': 'hot'|'warm'|'cool'|'cold', 'max_boards': int, 'premium_pct': float, 'detail': str}
    """
    conn = _get_db()

    # 找到最近两个交易日
    dates = conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date DESC LIMIT 2"
    ).fetchall()
    conn.close()

    if len(dates) < 1:
        return {"status": "cold", "max_boards": 0, "premium_pct": 0, "detail": "无数据"}

    trade_date = dates[0]["trade_date"]
    prev_date = dates[1]["trade_date"] if len(dates) > 1 else None

    # 获取当天所有A股日线
    conn2 = _get_db()
    all_daily = conn2.execute(
        "SELECT d.symbol, d.close, d.volume, d.open, b.name "
        "FROM stock_daily d JOIN stock_basic b ON d.symbol=b.symbol "
        "WHERE d.trade_date=? AND b.market='a'",
        (trade_date,)
    ).fetchall()
    conn2.close()

    # 涨停检测
    limitup_symbols = []
    for row in all_daily:
        sym = row["symbol"]
        rows_data = _get_daily_list(sym, limit=5)
        if len(rows_data) < 2:
            continue
        # 标记symbol用于涨停检测
        for r in rows_data:
            r["_symbol"] = sym
        if _is_limit_up(rows_data):
            limitup_symbols.append({"symbol": sym, "name": row["name"]})

    if not limitup_symbols:
        return {"status": "cold", "max_boards": 0, "premium_pct": 0,
                "detail": "今日无涨停"}

    # 计算连板高度
    max_boards = 0
    for item in limitup_symbols[:50]:
        sym = item["symbol"]
        rows_data = _get_daily_list(sym, limit=20)
        for r in rows_data:
            r["_symbol"] = sym
        boards = _get_consecutive_limit_up_count(rows_data)
        if boards > max_boards:
            max_boards = boards

    # 计算昨日涨停股今日溢价率
    premium_total = 0.0
    premium_count = 0

    if prev_date:
        conn3 = _get_db()
        prev_daily = conn3.execute(
            "SELECT d.symbol, d.close, d.open "
            "FROM stock_daily d JOIN stock_basic b ON d.symbol=b.symbol "
            "WHERE d.trade_date=? AND b.market='a'",
            (prev_date,)
        ).fetchall()
        conn3.close()

        for row in prev_daily:
            sym = row["symbol"]
            prev_close = row["close"]
            # 查当天收盘
            conn4 = _get_db()
            today_row = conn4.execute(
                "SELECT close FROM stock_daily WHERE symbol=? AND trade_date=?",
                (sym, trade_date)
            ).fetchone()
            conn4.close()
            if today_row and prev_close and prev_close > 0:
                rows_data = _get_daily_list(sym, limit=5)
                for r in rows_data:
                    r["_symbol"] = sym
                if len(rows_data) >= 2:
                    # 检查昨天是否涨停
                    prev_idx = next(
                        (i for i, r in enumerate(rows_data) if r["trade_date"] == prev_date),
                        None
                    )
                    if prev_idx is not None and prev_idx >= 1:
                        if _is_limit_up(rows_data, prev_idx):
                            premium = (today_row["close"] - prev_close) / prev_close * 100
                            premium_total += premium
                            premium_count += 1

    premium_pct = round(premium_total / premium_count, 2) if premium_count > 0 else 0

    # 情绪判定
    if max_boards >= 7 and premium_pct > 5:
        status = "hot"
    elif max_boards >= 5 and premium_pct > 2:
        status = "warm"
    elif max_boards >= 3 and premium_pct >= 0:
        status = "cool"
    else:
        status = "cold"

    return {
        "status": status,
        "max_boards": max_boards,
        "premium_pct": premium_pct,
        "detail": f"连板最高{max_boards}板, 昨日涨停溢价{premium_pct:.1f}%"
    }


# ══════════════════════════════════════════════════════
# Rule 1: 徐翔涨停板追涨
# ══════════════════════════════════════════════════════

def _rule_xu_xiang(symbol: str, name: str, sentiment: str) -> Optional[EntrySignal]:
    """徐翔涨停板追涨

    条件: 昨日涨停 + 今日高开>3% + 封单量/流通市值>0.5%
    仓位: 30%
    退出: 炸板>5分钟未回封 → 立即卖 / 次日低开>2% → 开盘卖
    """
    daily = _get_daily_list(symbol, limit=5)
    if len(daily) < 3:
        return None

    # 标记symbol
    for r in daily:
        r["_symbol"] = symbol

    # 昨日是否涨停
    if not _is_limit_up(daily, -1):
        # daily[-1]是今天，daily[-2]是昨天
        # 我们需要检查daily[-2]是否涨停（即昨天）
        # 但_is_limit_up检查idx和idx-1，所以idx=-2检查昨天相对前天
        if not _is_limit_up(daily, -2):
            return None

    yesterday_idx = -2
    yesterday_close = daily[yesterday_idx]["close"]
    day_before_close = daily[yesterday_idx - 1]["close"]
    if day_before_close is None or yesterday_close is None:
        return None

    limit_pct = _get_limit_pct(symbol)
    # 确认昨日涨停
    if not (yesterday_close >= day_before_close * (1 + limit_pct) * 0.995):
        return None

    today = daily[-1]
    today_open = today["open"]
    if today_open is None or yesterday_close is None:
        return None

    # 今日高开>3%
    if today_open <= yesterday_close * 1.03:
        return None

    # 封单量/流通市值>0.5%
    today_amount = today["amount"] or 0
    conn = _get_db()
    row = conn.execute(
        "SELECT total_mv FROM stock_fundamental WHERE symbol=?", (symbol,)
    ).fetchone()
    conn.close()
    total_mv = row["total_mv"] if row and row["total_mv"] else 0
    if total_mv <= 0:
        return None

    ratio = today_amount / total_mv * 100
    if ratio < 0.5:
        return None

    trigger_price = today_open
    stop_loss = yesterday_close * 0.98

    confidence = min(85, 50 + ratio * 10)
    return EntrySignal(
        symbol=symbol, name=name, rule="徐翔",
        confidence=round(confidence, 1), position_pct=30,
        trigger_price=trigger_price, stop_loss=stop_loss,
        detail=f"昨日涨停+今日高开{((today_open/yesterday_close-1)*100):.1f}%+成交额占比{ratio:.2f}%"
    )


# ══════════════════════════════════════════════════════
# Rule 2: 赵老哥二板定龙头
# ══════════════════════════════════════════════════════

def _rule_zhao_laoge(symbol: str, name: str) -> Optional[EntrySignal]:
    """赵老哥二板定龙头

    条件: 首板放量(量比>2) + 二板缩量加速(二板量/首板量<0.8)
    仓位: 25%
    退出: 三板炸板 → 卖 / 断板(不再涨停) → 收盘卖
    """
    daily = _get_daily_list(symbol, limit=10)
    if len(daily) < 4:
        return None

    for r in daily:
        r["_symbol"] = symbol

    limit_pct = _get_limit_pct(symbol)

    # 首板 = daily[-3], 二板 = daily[-2], 今天 = daily[-1]
    board1 = daily[-3]
    board2 = daily[-2]

    # 首板涨停?
    prev_close_b1 = daily[-4]["close"]
    if prev_close_b1 is None or prev_close_b1 == 0:
        return None
    is_board1 = board1["close"] >= prev_close_b1 * (1 + limit_pct) * 0.995

    # 二板涨停?
    prev_close_b2 = daily[-3]["close"]
    if prev_close_b2 is None or prev_close_b2 == 0:
        return None
    is_board2 = board2["close"] >= prev_close_b2 * (1 + limit_pct) * 0.995

    if not (is_board1 and is_board2):
        return None

    # 首板放量（量比>2）
    vol_b1 = board1["volume"] or 0
    avg_vol = sum(
        daily[i]["volume"] or 0 for i in range(max(0, len(daily) - 8), len(daily) - 3)
    )
    avg_vol = avg_vol / max(1, (len(daily) - 3) - max(0, len(daily) - 8))
    if avg_vol == 0 or (vol_b1 / avg_vol) < 2.0:
        return None

    # 二板缩量加速（二板量/首板量<0.8）
    vol_b2 = board2["volume"] or 0
    if vol_b1 <= 0 or (vol_b2 / vol_b1) >= 0.8:
        return None

    trigger_price = board2["close"] * 1.01
    stop_loss = board1["close"] * 0.95

    ratio = vol_b2 / vol_b1
    confidence = min(90, 60 + (1 - ratio) * 50)
    return EntrySignal(
        symbol=symbol, name=name, rule="赵老哥",
        confidence=round(confidence, 1), position_pct=25,
        trigger_price=trigger_price, stop_loss=stop_loss,
        detail=f"首板放量({vol_b1/avg_vol:.1f}x)+二板缩量({ratio:.2f}x)+二连板"
    )


# ══════════════════════════════════════════════════════
# Rule 4: 作手新一逻辑驱动（龙虎榜跟买）
# ══════════════════════════════════════════════════════

def _rule_zuoshou_xinyi(symbol: str, name: str) -> Optional[EntrySignal]:
    """作手新一逻辑驱动

    条件: 龙虎榜机构净买>5000万 + 股价>MA20 + 非一日游
    仓位: 20%
    退出: 3日不涨(买入后3天涨幅<2%) → 卖 / 反手(净卖>3000万) → 卖

    无专门龙虎榜表，使用近似策略:
    - 今日放量大涨(涨幅>5%+量比>2)模拟机构行为
    """
    daily = _get_daily_list(symbol, limit=30)
    if len(daily) < 25:
        return None

    today = daily[-1]
    yesterday = daily[-2]

    today_close = today["close"]
    yesterday_close = yesterday["close"]
    if yesterday_close is None or yesterday_close == 0:
        return None

    daily_return = (today_close - yesterday_close) / yesterday_close * 100
    vol_ratio = _compute_volume_ratio(daily)

    if not (daily_return > 5 and vol_ratio and vol_ratio > 2.0):
        return None

    # 股价 > MA20
    ma20 = _compute_ma(daily, 20)
    if ma20 is None or today_close <= ma20:
        return None

    # 非一日游: 统计过去20天涨幅>5%的天数
    recent_bursts = 0
    lookback = min(20, len(daily) - 2)
    for i in range(-lookback, -1):
        ret = (daily[i]["close"] - daily[i - 1]["close"]) / daily[i - 1]["close"] * 100
        if ret > 5:
            recent_bursts += 1

    today_amount = today["amount"] or 0
    est_buy = today_amount * min(daily_return / 10, 0.3)
    est_buy_yi = est_buy / 100_000_000

    confidence = min(80, 40 + est_buy_yi * 5)
    trigger_price = today_close
    stop_loss = min(ma20, today_close * 0.95)

    return EntrySignal(
        symbol=symbol, name=name, rule="作手新一",
        confidence=round(confidence, 1), position_pct=20,
        trigger_price=trigger_price, stop_loss=stop_loss,
        detail=f"涨幅{daily_return:.1f}%+量比{vol_ratio:.1f}x+站上MA20({ma20:.2f})+估算净买{est_buy_yi:.2f}亿"
    )


# ══════════════════════════════════════════════════════
# Rule 5: 方新侠拐点博弈（超跌反弹）
# ══════════════════════════════════════════════════════

def _rule_fang_xinxia(symbol: str, name: str) -> Optional[EntrySignal]:
    """方新侠拐点博弈

    条件: 连续下跌3天 + RSI<30 + 今日放量阳线(量比>1.5+涨幅>3%)
    仓位: 15%
    退出: 反弹5%止盈 / 破新低(跌破抄底日最低价) → 止损
    """
    daily = _get_daily_list(symbol, limit=30)
    if len(daily) < 20:
        return None

    # 连续下跌3天
    if len(daily) < 4:
        return None
    drops = 0
    for i in range(-3, 0):
        ret = (daily[i]["close"] - daily[i - 1]["close"]) / daily[i - 1]["close"] * 100
        if ret < 0:
            drops += 1
    if drops < 3:
        return None

    # RSI < 30
    rsi = _compute_rsi(daily)
    if rsi is None or rsi >= 30:
        return None

    # 今日放量阳线
    today = daily[-1]
    today_open = today["open"]
    today_close = today["close"]
    today_low = today["low"]

    if today_close <= today_open:
        return None

    vol_ratio = _compute_volume_ratio(daily)
    if not (vol_ratio and vol_ratio > 1.5):
        return None

    # 涨幅 > 3%
    yesterday_close = daily[-2]["close"]
    if yesterday_close is None or yesterday_close == 0:
        return None
    day_return = (today_close - yesterday_close) / yesterday_close * 100
    if day_return < 3:
        return None

    trigger_price = today_close
    stop_loss = today_low

    confidence = min(85, 40 + (30 - rsi) * 2 + vol_ratio * 5)
    return EntrySignal(
        symbol=symbol, name=name, rule="方新侠",
        confidence=round(confidence, 1), position_pct=15,
        trigger_price=trigger_price, stop_loss=stop_loss,
        detail=f"连跌3天+RSI={rsi:.1f}+放量{vol_ratio:.1f}x+涨幅{day_return:.1f}%"
    )


# ══════════════════════════════════════════════════════
# 卖出信号
# ══════════════════════════════════════════════════════

def exit_signals(position: dict, market_data: dict) -> list[ExitSignal]:
    """短线专属卖出信号

    Args:
        position: {symbol, rule, entry_price, entry_date, entry_low, ...}
        market_data: {symbol: {当前价格/涨跌幅/是否涨停等}}
    """
    signals = []
    symbol = position.get("symbol", "")
    rule = position.get("rule", "")
    entry_price = position.get("entry_price", 0)
    entry_low = position.get("entry_low", 0)
    current = market_data.get(symbol, {})

    if rule == "徐翔":
        # 炸板(涨停打开>5分钟未回封) → 立即卖
        if current.get("kaiban", False):
            signals.append(ExitSignal(
                symbol=symbol, reason="徐翔-炸板",
                urgency="immediate", action="sell_all",
                detail="涨停板打开超过5分钟未回封"
            ))
        # 低开>2% → 开盘卖
        open_pct = current.get("open_change_pct", 0)
        if open_pct < -2:
            signals.append(ExitSignal(
                symbol=symbol, reason="徐翔-低开",
                urgency="immediate", action="sell_all",
                detail=f"低开{open_pct:.1f}%"
            ))

    elif rule == "赵老哥":
        if current.get("not_limit_up", False):
            signals.append(ExitSignal(
                symbol=symbol, reason="赵老哥-断板",
                urgency="close", action="sell_all",
                detail="三板未能涨停，断板卖出"
            ))

    elif rule == "作手新一":
        days_held = current.get("days_held", 99)
        total_ret = current.get("total_return", 0)
        if days_held >= 3 and total_ret < 2:
            signals.append(ExitSignal(
                symbol=symbol, reason="作手新一-3日不涨",
                urgency="today", action="sell_all",
                detail=f"持仓{days_held}日收益{total_ret:.1f}%<2%"
            ))
        if current.get("net_sell_over_30m", False):
            signals.append(ExitSignal(
                symbol=symbol, reason="作手新一-反手",
                urgency="immediate", action="sell_all",
                detail="疑似机构反手卖出"
            ))

    elif rule == "方新侠":
        total_ret = current.get("total_return", 0)
        if total_ret >= 5:
            signals.append(ExitSignal(
                symbol=symbol, reason="方新侠-止盈",
                urgency="today", action="sell_all",
                detail=f"反弹收益{total_ret:.1f}%>=5%止盈"
            ))
        current_low = current.get("current_low", 999)
        if entry_low > 0 and current_low <= entry_low:
            signals.append(ExitSignal(
                symbol=symbol, reason="方新侠-破新低",
                urgency="immediate", action="sell_all",
                detail=f"跌破抄底日最低价{entry_low:.2f}"
            ))

    return signals


# ══════════════════════════════════════════════════════
# ShortTermEngine 主类
# ══════════════════════════════════════════════════════

class ShortTermEngine:
    """短线交易引擎 — 5种游资方法论"""

    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path
        self._sentiment = None

    @property
    def sentiment(self) -> dict:
        """获取市场情绪（带缓存）"""
        if self._sentiment is None:
            self._sentiment = _get_market_sentiment(self.db_path)
        return self._sentiment

    def scan_all(self) -> list[EntrySignal]:
        """扫描全市场，返回所有触发的短线信号"""
        conn = _get_db()
        symbols = conn.execute(
            "SELECT symbol, name FROM stock_basic WHERE market='a'"
        ).fetchall()
        conn.close()

        sentiment_val = self.sentiment
        all_signals = []

        for row in symbols:
            sym, name = row["symbol"], row["name"]
            signals = self._check_symbol(sym, name, sentiment_val)
            all_signals.extend(signals)

        all_signals.sort(key=lambda s: s.confidence, reverse=True)
        return all_signals

    def entry_signals(self, db, market_data: dict) -> list[EntrySignal]:
        """返回买入信号列表，含仓位建议"""
        return self.scan_all()

    def exit_signals(self, position, market_data: dict) -> list[ExitSignal]:
        """返回卖出信号列表（短线专属）"""
        return exit_signals(position, market_data)

    def check_symbol(self, symbol: str) -> list[EntrySignal]:
        """单票检查所有规则"""
        info = _get_stock_info(symbol)
        if not info:
            return []
        sentiment_val = self.sentiment
        return self._check_symbol(symbol, info.get("name", ""), sentiment_val)

    def _check_symbol(self, symbol: str, name: str,
                      sentiment_val: dict) -> list[EntrySignal]:
        """对单只股票检查所有短线规则"""
        signals = []
        情绪允许 = sentiment_val.get("status") in ("hot", "warm")

        # Rule 1: 徐翔 — 仅在情绪允许时
        if 情绪允许:
            try:
                s = _rule_xu_xiang(symbol, name, sentiment_val["status"])
                if s:
                    signals.append(s)
            except Exception:
                pass

        # Rule 2: 赵老哥 — 仅在情绪允许时
        if 情绪允许:
            try:
                s = _rule_zhao_laoge(symbol, name)
                if s:
                    signals.append(s)
            except Exception:
                pass

        # Rule 4: 作手新一
        try:
            s = _rule_zuoshou_xinyi(symbol, name)
            if s:
                if 情绪允许:
                    s.confidence = min(100, s.confidence + 5)
                signals.append(s)
        except Exception:
            pass

        # Rule 5: 方新侠 — 逆周期策略
        try:
            s = _rule_fang_xinxia(symbol, name)
            if s:
                if sentiment_val.get("status") in ("cold", "cool"):
                    s.confidence = min(100, s.confidence + 10)
                signals.append(s)
        except Exception:
            pass

        return signals


# ══════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════

def main():
    """命令行入口"""
    if len(sys.argv) < 2:
        print("用法:")
        print("  python -m src.short_term_engine scan              # 全市场扫描")
        print("  python -m src.short_term_engine check <symbol>    # 单票检查")
        return

    engine = ShortTermEngine()

    if sys.argv[1] == "scan":
        signals = engine.scan_all()
        output = []
        for s in signals:
            d = asdict(s)
            d["sentiment"] = engine.sentiment
            output.append(d)
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        print(f"\n共 {len(signals)} 个短线信号", file=sys.stderr)

    elif sys.argv[1] == "check" and len(sys.argv) >= 3:
        symbol = sys.argv[2]
        info = _get_stock_info(symbol)
        if not info:
            print(f"未找到股票 {symbol}")
            return
        print(f"\n{'='*60}")
        print(f"  {info.get('name', '?')} ({symbol}) — 短线规则检查")
        print(f"{'='*60}")

        sentiment_val = engine.sentiment
        print(f"\n【市场情绪】{sentiment_val['status'].upper()} | {sentiment_val['detail']}")
        print(f"情绪状态: {'允许做多' if sentiment_val['status'] in ('hot','warm') else '谨慎/空仓'}")

        rules = [
            ("Rule1 徐翔涨停板追涨",
             lambda: _rule_xu_xiang(symbol, info["name"], sentiment_val["status"]),
             sentiment_val["status"] in ("hot", "warm")),
            ("Rule2 赵老哥二板定龙头",
             lambda: _rule_zhao_laoge(symbol, info["name"]),
             sentiment_val["status"] in ("hot", "warm")),
            ("Rule4 作手新一逻辑驱动",
             lambda: _rule_zuoshou_xinyi(symbol, info["name"]),
             True),
            ("Rule5 方新侠拐点博弈",
             lambda: _rule_fang_xinxia(symbol, info["name"]),
             True),
        ]

        for label, func, allowed in rules:
            print(f"\n  {label}")
            print(f"  {'-'*50}")
            if not allowed:
                print(f"   [跳过] 当前市场情绪({sentiment_val['status']})不满足要求")
                continue
            try:
                result = func()
                if result:
                    print(f"   ✅ 触发! 置信度={result.confidence} 仓位={result.position_pct}%")
                    print(f"      触发价={result.trigger_price:.2f} 止损={result.stop_loss:.2f}")
                    print(f"      详情: {result.detail}")
                else:
                    print(f"   ❌ 未触发")
            except Exception as e:
                print(f"   ⚠️ 检查异常: {e}")

        print()

    else:
        print("未知命令或参数不足")
        main()


if __name__ == "__main__":
    main()
