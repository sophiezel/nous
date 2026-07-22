"""量价合理性校验 — Phase 4

检测可能导致假信号的量价异常模式:
1. 高开低走 → 利好出货
2. 异常放量 → 对倒 vs 真实突破
3. 低流动性 → 技术指标不可靠
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class RiskLevel(Enum):
    NORMAL = "normal"       # 正常
    SUSPICIOUS = "suspicious"  # 可疑，建议手工复核
    HIGH_RISK = "high_risk"    # 高风险，不建议推荐


@dataclass
class QualityWarning:
    pattern: str
    description: str
    risk: RiskLevel = RiskLevel.SUSPICIOUS
    metrics: dict = field(default_factory=dict)


# ── 日线数据获取 ──────────────────────────────

def _get_daily(symbol: str, days: int = 30) -> list[dict]:
    """从 screener.db 获取最近N天日线"""
    import sqlite3
    from pathlib import Path
    db = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, volume, amount "
        "FROM stock_daily WHERE symbol=? ORDER BY trade_date DESC LIMIT ?",
        (symbol, days)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]  # 升序


def _avg_volume(rows: list[dict], n: int = 20) -> float:
    """计算N日均量"""
    if not rows:
        return 0
    volumes = [r.get("volume", 0) or 0 for r in rows[-n:]]
    return sum(volumes) / len(volumes) if volumes else 0


def _avg_amount(rows: list[dict], n: int = 20) -> float:
    """计算N日均成交额"""
    if not rows:
        return 0
    amounts = [r.get("amount", 0) or 0 for r in rows[-n:]]
    return sum(amounts) / len(amounts) if amounts else 0


# ── 1. 高开低走检测 ──────────────────────────

def detect_gap_up_fade(
    symbol: str,
    daily_rows: list[dict] = None,
    gap_threshold: float = 0.03,     # 高开3%+
    fade_threshold: float = 0.01,    # 收盘仅剩1%涨幅
    intraday_drop_threshold: float = 0.02,  # 盘中回落2%+
) -> Optional[QualityWarning]:
    """
    检测高开低走模式。
    
    典型形态:
    - 开盘价 > 昨收 × (1 + gap_threshold)  — 大幅高开
    - 收盘价 < 昨收 × (1 + fade_threshold)  — 涨幅几乎回吐
    - 收盘 < 开盘 × (1 - intraday_drop)     — 盘中持续回落
    
    常见于: 利好兑现出货、题材退潮、主力诱多。
    """
    if daily_rows is None:
        daily_rows = _get_daily(symbol, 5)
    if len(daily_rows) < 2:
        return None

    today = daily_rows[-1]
    yesterday = daily_rows[-2]
    close = today.get("close", 0)
    open_p = today.get("open", 0)
    high = today.get("high", 0)
    prev_close = yesterday.get("close", 0)

    if not all([close, open_p, prev_close]) or prev_close <= 0:
        return None

    gap = (open_p - prev_close) / prev_close
    day_return = (close - prev_close) / prev_close
    intraday = (close - open_p) / open_p
    upper_shadow = (high - max(close, open_p)) / open_p if high > max(close, open_p) else 0

    if gap >= gap_threshold and day_return <= fade_threshold and intraday <= -intraday_drop_threshold:
        return QualityWarning(
            pattern="高开低走",
            description=f"开盘+{gap*100:.1f}% → 收盘仅+{day_return*100:.1f}%，盘中回落{abs(intraday)*100:.1f}%",
            risk=RiskLevel.HIGH_RISK,
            metrics={"gap_pct": round(gap*100,1), "day_return_pct": round(day_return*100,1),
                     "intraday_pct": round(intraday*100,1), "upper_shadow_pct": round(upper_shadow*100,1)},
        )

    # 次高模式: 高开+长上影线
    if gap >= gap_threshold and upper_shadow >= 0.03 and intraday <= 0:
        return QualityWarning(
            pattern="高开上影线",
            description=f"开盘+{gap*100:.1f}% → 上影线{upper_shadow*100:.1f}%，冲高回落",
            risk=RiskLevel.SUSPICIOUS,
            metrics={"gap_pct": round(gap*100,1), "upper_shadow_pct": round(upper_shadow*100,1)},
        )

    return None


# ── 2. 异常放量甄别 ──────────────────────────

def detect_abnormal_volume(
    symbol: str,
    daily_rows: list[dict] = None,
    vol_spike_mult: float = 5.0,      # 成交量是均量的N倍
    price_move_min: float = 0.02,     # 价格波动<2% → 可疑
    price_move_real: float = 0.05,    # 价格波动>5% → 真实突破
) -> Optional[QualityWarning]:
    """
    检测异常放量并甄别性质。

    放量+价格大幅波动 → 真实突破/破位
    放量+价格几乎不动 → 可疑（对倒/换仓/利益输送）

    返回:
    - None: 无异常
    - SUSPICIOUS: 放量但价格不动 → 可疑
    - NORMAL: 放量+真实波动 → 标记但不拦截
    """
    if daily_rows is None:
        daily_rows = _get_daily(symbol, 30)
    if len(daily_rows) < 21:
        return None

    today = daily_rows[-1]
    yesterday = daily_rows[-2]
    vol = today.get("volume", 0)
    prev_close = yesterday.get("close", 0)
    close = today.get("close", 0)

    if not vol or not prev_close:
        return None

    avg_vol = _avg_volume(daily_rows[:-1], 20)  # 不含今日的20日均量
    if avg_vol <= 0:
        return None

    vol_ratio = vol / avg_vol

    if vol_ratio >= vol_spike_mult:
        day_chg = abs(close - prev_close) / prev_close if close and prev_close else 0

        if day_chg <= price_move_min:
            # 放量+价格几乎不动 → 高度可疑
            return QualityWarning(
                pattern="异常放量(对倒?)",
                description=f"量比{vol_ratio:.1f}倍，价格仅波动{day_chg*100:.1f}%——疑似对倒或换仓",
                risk=RiskLevel.HIGH_RISK,
                metrics={"vol_ratio": round(vol_ratio,1), "day_chg_pct": round(day_chg*100,2)},
            )
        elif day_chg >= price_move_real:
            # 放量+真实波动 → 突破确认
            direction = "向上突破" if close > prev_close else "向下破位"
            return QualityWarning(
                pattern=f"放量{direction}",
                description=f"量比{vol_ratio:.1f}倍，价格波动{day_chg*100:.1f}%——量价配合",
                risk=RiskLevel.NORMAL,  # 不拦截，但标记
                metrics={"vol_ratio": round(vol_ratio,1), "day_chg_pct": round(day_chg*100,1)},
            )

    return None


# ── 3. 低流动性假信号过滤 ────────────────────

def detect_low_liquidity_false_signals(
    symbol: str,
    daily_rows: list[dict] = None,
    min_daily_amount: float = 10_000_000,  # 日均1000万
    market: str = "a",
) -> Optional[QualityWarning]:
    """
    检测低流动性导致的假信号风险。

    流动性越低，技术指标越不可靠:
    - MA金叉可能是几笔交易拉出来的
    - RSI在低流动性标的上波动极大
    - 量比在小基数上失真

    科创板股票尤其容易出假信号。
    """
    if market == "hk":
        min_daily_amount = 5_000_000  # 港股500万港币

    if daily_rows is None:
        daily_rows = _get_daily(symbol, 30)
    if len(daily_rows) < 20:
        return None

    avg_amt = _avg_amount(daily_rows, 20)

    if avg_amt < min_daily_amount:
        return QualityWarning(
            pattern="低流动性",
            description=f"日均成交额{avg_amt/1e4:.0f}万 < {min_daily_amount/1e4:.0f}万——技术指标可靠性低",
            risk=RiskLevel.SUSPICIOUS,
            metrics={"avg_amount_wan": round(avg_amt/1e4, 0)},
        )

    # 科创板额外严格（20%涨跌幅，假信号更多）
    if symbol.startswith("688") and avg_amt < 50_000_000:
        return QualityWarning(
            pattern="科创板低流动性",
            description=f"科创板日均{avg_amt/1e4:.0f}万 < 5000万——假信号高发区",
            risk=RiskLevel.HIGH_RISK,
            metrics={"avg_amount_wan": round(avg_amt/1e4, 0)},
        )

    return None


# ── 综合检测 ──────────────────────────────────

def check_all(
    symbol: str,
    market: str = "a",
    daily_rows: list[dict] = None,
) -> list[QualityWarning]:
    """
    一次性运行所有量价合理性检测。
    返回 QualityWarning 列表。
    """
    if daily_rows is None:
        daily_rows = _get_daily(symbol, 30)
    if not daily_rows:
        return []

    warnings = []

    # 1. 高开低走
    w = detect_gap_up_fade(symbol, daily_rows)
    if w:
        warnings.append(w)

    # 2. 异常放量
    w = detect_abnormal_volume(symbol, daily_rows)
    if w and w.risk != RiskLevel.NORMAL:  # 放量突破不拦，但可疑放量要拦
        warnings.append(w)

    # 3. 低流动性
    w = detect_low_liquidity_false_signals(symbol, daily_rows, market=market)
    if w:
        warnings.append(w)

    return warnings


def has_high_risk(warnings: list[QualityWarning]) -> bool:
    """是否有高风险警告"""
    return any(w.risk == RiskLevel.HIGH_RISK for w in warnings)


def format_warnings(warnings: list[QualityWarning]) -> str:
    """格式化警告为可读字符串"""
    if not warnings:
        return ""
    lines = []
    for w in warnings:
        emoji = {"HIGH_RISK": "🔴", "SUSPICIOUS": "🟡", "NORMAL": "ℹ️"}.get(w.risk.name, "")
        lines.append(f"  {emoji} {w.pattern}: {w.description}")
    return "\n".join(lines)
