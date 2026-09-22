"""拐点信号引擎 — 把散落的指标压成"是否真的在修复"的判读。

信号定义在 config/pvglass_indicators.yaml 的 signals 段，本模块只负责求值。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field as dc_field

from nous.research.pvglass import store
from nous.research.pvglass.registry import Condition, Registry, Signal

PASS = "pass"
FAIL = "fail"
NO_DATA = "no_data"

TRIGGERED = "triggered"
NOT_TRIGGERED = "not_triggered"
PENDING = "pending"
INSUFFICIENT = "insufficient_data"


@dataclass
class ConditionResult:
    condition: Condition
    indicator_name: str
    status: str
    actual: float | None = None
    detail: str = ""


@dataclass
class SignalResult:
    signal: Signal
    status: str
    conditions: list[ConditionResult] = dc_field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.conditions if c.status == PASS)

    @property
    def with_data(self) -> int:
        return sum(1 for c in self.conditions if c.status != NO_DATA)


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _values(
    conn: sqlite3.Connection,
    indicator_id: str,
    window_days: int,
    as_of: str | None = None,
) -> list[float]:
    rows = store.window(conn, indicator_id, window_days, as_of=as_of)
    out: list[float] = []
    for row in rows:
        number = _to_float(row["value"])
        if number is not None:
            out.append(number)
    return out


def _mom_streak(values: list[float], positive: bool) -> int:
    """末尾连续环比同向的次数。"""
    streak = 0
    for prev, cur in zip(reversed(values[:-1]), reversed(values[1:])):
        if (cur > prev) if positive else (cur < prev):
            streak += 1
        else:
            break
    return streak


def _compare(latest: float, op: str, target: float) -> bool:
    if op == "gte":
        return latest >= target
    if op == "lte":
        return latest <= target
    if op == "gt":
        return latest > target
    if op == "lt":
        return latest < target
    return False


def evaluate_condition(
    conn: sqlite3.Connection,
    registry: Registry,
    condition: Condition,
    as_of: str | None = None,
) -> ConditionResult:
    indicator = registry.indicators.get(condition.indicator)
    name = indicator.name if indicator else condition.indicator
    values = _values(conn, condition.indicator, condition.window_days, as_of=as_of)
    if not values:
        return ConditionResult(
            condition,
            name,
            NO_DATA,
            None,
            f"窗口{condition.window_days}天内无数据（auto={indicator.auto if indicator else '?'}）",
        )

    latest = values[-1]
    op = condition.op
    if op in ("gte", "lte", "gt", "lt"):
        ok = _compare(latest, op, condition.value)
        detail = f"最新 {latest:g} {op} {condition.value:g}"
    elif op in ("pct_chg_gte", "pct_chg_lte"):
        if len(values) < 2 or values[0] == 0:
            return ConditionResult(condition, name, NO_DATA, latest, "窗口内不足两点，无法算变化率")
        chg = (latest - values[0]) / abs(values[0]) * 100
        threshold = condition.value
        ok = chg >= threshold if op == "pct_chg_gte" else chg <= threshold
        detail = f"窗口变化 {chg:+.1f}%（{values[0]:g} → {latest:g}）目标 {op[-3:]} {threshold:g}%"
    elif op in ("mom_streak_gte", "mom_streak_lte"):
        streak = _mom_streak(values, positive=(op == "mom_streak_gte"))
        ok = streak >= condition.value
        detail = f"连续{streak}期环比{'正' if op == 'mom_streak_gte' else '负'}增长，要求≥{condition.value:g}"
    else:  # pragma: no cover - registry 已校验
        return ConditionResult(condition, name, NO_DATA, latest, f"不支持的算子 {op}")

    return ConditionResult(condition, name, PASS if ok else FAIL, latest, detail)


def evaluate_signal(
    conn: sqlite3.Connection,
    registry: Registry,
    signal: Signal,
    as_of: str | None = None,
) -> SignalResult:
    results = [evaluate_condition(conn, registry, c, as_of=as_of) for c in signal.conditions]
    data_results = [r for r in results if r.status != NO_DATA]
    if not data_results:
        status = INSUFFICIENT
    elif signal.logic == "any":
        status = TRIGGERED if any(r.status == PASS for r in data_results) else NOT_TRIGGERED
    else:  # all
        if all(r.status == PASS for r in data_results):
            status = TRIGGERED if len(data_results) == len(results) else PENDING
        else:
            status = NOT_TRIGGERED
    return SignalResult(signal=signal, status=status, conditions=results)


def evaluate(
    conn: sqlite3.Connection,
    registry: Registry,
    signal_id: str | None = None,
    as_of: str | None = None,
) -> list[SignalResult]:
    signals = [s for s in registry.signals if signal_id is None or s.id == signal_id]
    return [evaluate_signal(conn, registry, s, as_of=as_of) for s in signals]


def verdict(results: list[SignalResult]) -> tuple[str, str]:
    """汇总判读：返回 (level, 结论)。"""
    if not results:
        return "no_signal", "未配置信号"
    triggered = [r for r in results if r.status == TRIGGERED]
    pending = [r for r in results if r.status == PENDING]
    if len(triggered) >= 3:
        return "bullish", f"{len(triggered)}/{len(results)} 信号确认 → 修复成立，可上调假设"
    if triggered:
        ids = ", ".join(r.signal.id for r in triggered)
        return "watch", f"{len(triggered)}/{len(results)} 信号确认（{ids}）→ 修复初期，继续观察"
    if pending:
        return "pending", "条件部分满足但数据不全 → 先补齐人工指标"
    return "bearish", "无信号确认 → 底部未确认，维持防御"
