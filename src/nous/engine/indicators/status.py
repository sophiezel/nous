"""交易状态检测 — 选股/荐股前必检

K0优先级最高。检测项: 停牌 / ST / 退市 / 涨跌停 / 流动性
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional


class Status(Enum):
    NORMAL = "normal"         # 正常交易
    SUSPENDED = "suspended"   # 停牌
    ST = "st"                 # ST股（标记风险）
    STAR_ST = "star_st"       # *ST（排除）
    DELISTING = "delisting"   # 退市（排除）
    LIMIT_UP = "limit_up"     # 涨停板
    LIMIT_DOWN = "limit_down" # 跌停板
    LOW_LIQUIDITY = "low_liquidity"  # 流动性不足


class Action(Enum):
    ALLOW = "allow"           # 允许
    EXCLUDE = "exclude"       # 直接排除
    MARK_RISK = "mark_risk"   # 标记风险但不排除
    BLOCK_DIRECTION = "block_direction"  # 禁止某个方向


@dataclass
class StatusResult:
    status: Status = Status.NORMAL
    action: Action = Action.ALLOW
    reason: str = ""
    blocked_direction: str = ""  # 'buy' / 'sell' / ''


# ── 停牌检测 ────────────────────────────────

SUSPENDED_PATH = Path.home() / "wiki" / "finance" / "raw" / "suspended_stocks.json"


def _load_suspended() -> set:
    """加载停牌黑名单"""
    if not SUSPENDED_PATH.exists():
        return set()
    try:
        data = json.loads(SUSPENDED_PATH.read_text())
        return set(data.get("stocks", {}).keys())
    except Exception:
        return set()


def is_suspended(symbol: str) -> bool:
    """检查股票是否在停牌黑名单中"""
    suspended = _load_suspended()
    return symbol in suspended


# ── ST/退市检测 ──────────────────────────────

def check_special(name: str) -> StatusResult:
    """检查ST/*ST/退市标记"""
    if "退" in name:
        return StatusResult(
            status=Status.DELISTING,
            action=Action.EXCLUDE,
            reason="退市股"
        )
    if "*ST" in name:
        return StatusResult(
            status=Status.STAR_ST,
            action=Action.EXCLUDE,
            reason="*ST风险警示"
        )
    if "ST" in name:
        return StatusResult(
            status=Status.ST,
            action=Action.MARK_RISK,
            reason="ST股"
        )
    return StatusResult(status=Status.NORMAL, action=Action.ALLOW)


# ── 涨跌停检测 ──────────────────────────────

def check_price_limit(
    symbol: str,
    close: float,
    high: float,
    low: float,
    prev_close: float,
    market: str = "a"
) -> StatusResult:
    """检测涨跌停板"""
    if market != "a" or prev_close <= 0:
        return StatusResult(status=Status.NORMAL, action=Action.ALLOW)

    # A股涨跌停幅度
    if symbol.startswith(("688", "689")):  # 科创板
        limit = 0.20
    elif symbol.startswith("3"):  # 创业板（部分注册制后也是20%？检查）
        limit = 0.20 if len(symbol) == 6 else 0.10
    elif symbol.startswith(("4", "8", "9")):  # 北交所
        limit = 0.30
    else:
        limit = 0.10

    chg = (close - prev_close) / prev_close

    if chg >= limit * 0.99:
        if close == high:  # 可能是封板
            return StatusResult(
                status=Status.LIMIT_UP,
                action=Action.BLOCK_DIRECTION,
                reason=f"涨停板({chg*100:.1f}%)",
                blocked_direction="buy",
            )
    elif chg <= -limit * 0.99:
        if close == low:
            return StatusResult(
                status=Status.LIMIT_DOWN,
                action=Action.BLOCK_DIRECTION,
                reason=f"跌停板({chg*100:.1f}%)",
                blocked_direction="sell",
            )

    return StatusResult(status=Status.NORMAL, action=Action.ALLOW)


# ── 流动性检测 ──────────────────────────────

def check_liquidity(avg_volume: float, avg_amount: float, market: str = "a") -> StatusResult:
    """检查流动性"""
    min_amount = 10_000_000 if market == "a" else 5_000_000  # 1000万/500万港币
    if avg_amount < min_amount:
        return StatusResult(
            status=Status.LOW_LIQUIDITY,
            action=Action.MARK_RISK,
            reason=f"日均成交额{avg_amount/1e4:.0f}万 < {min_amount/1e4:.0f}万",
        )
    return StatusResult(status=Status.NORMAL, action=Action.ALLOW)


# ── 综合检测 ────────────────────────────────

def check_all(
    symbol: str,
    name: str = "",
    market: str = "a",
    close: float = 0,
    high: float = 0,
    low: float = 0,
    prev_close: float = 0,
    avg_volume: float = 0,
    avg_amount: float = 0,
) -> list[StatusResult]:
    """
    一次性执行所有交易状态检测。
    返回检测结果列表，用于确定是否允许继续。
    """
    results = []

    # 1. 停牌
    if is_suspended(symbol):
        results.append(StatusResult(
            status=Status.SUSPENDED,
            action=Action.EXCLUDE,
            reason="停牌中",
        ))
        return results  # 停牌不必继续检查其他项

    # 2. ST/退市
    if name:
        st_result = check_special(name)
        if st_result.action == Action.EXCLUDE:
            results.append(st_result)
            return results
        if st_result.status != Status.NORMAL:
            results.append(st_result)

    # 3. 涨跌停
    if close > 0 and prev_close > 0:
        limit_result = check_price_limit(symbol, close, high, low, prev_close, market)
        if limit_result.action != Action.ALLOW:
            results.append(limit_result)

    # 4. 流动性
    if avg_amount > 0:
        liq_result = check_liquidity(avg_volume, avg_amount, market)
        if liq_result.status != Status.NORMAL:
            results.append(liq_result)

    return results


def should_exclude(results: list[StatusResult]) -> bool:
    """是否有任何结果要求排除该股票"""
    return any(r.action == Action.EXCLUDE for r in results)
