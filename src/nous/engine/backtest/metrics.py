"""回测绩效指标计算 — 含完整性旗标与去尖刺 Sharpe"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any
import numpy as np


SPIKE_THRESHOLD = 0.10  # |daily return| > 10% counted as spike
TRUST_SPIKE_THRESHOLD = 0.50  # |r| > 50% → TRUSTED=false for A-share book


@dataclass
class BacktestResult:
    """回测结果数据类"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    sharpe_winsorized: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    turnover_rate: float = 0.0
    daily_returns: Optional[list] = field(default_factory=list)
    equity_curve: Optional[list] = field(default_factory=list)
    trades_log: Optional[list] = field(default_factory=list)
    label: str = ""
    fold_details: Optional[list] = field(default_factory=list)
    initial_capital: float = 1_000_000
    n_trading_days: int = 0
    max_daily_return: float = 0.0
    min_daily_return: float = 0.0
    n_return_spikes: int = 0
    integrity_flags: dict = field(default_factory=dict)
    rf_annual: float = 0.02


def _dedupe_equity_curve(daily_values: list[dict]) -> list[dict]:
    """Keep last equity point per date (end-of-sim may append same last_date)."""
    by_date: dict[str, dict] = {}
    order: list[str] = []
    for pt in daily_values:
        d = pt.get("date")
        if d is None:
            continue
        if d not in by_date:
            order.append(d)
        by_date[d] = pt
    return [by_date[d] for d in order]


def _safe_daily_returns(equities: np.ndarray) -> np.ndarray:
    """Compute daily returns with denominator protection; invalid → NaN."""
    rets = np.full(len(equities) - 1, np.nan, dtype=np.float64)
    for i in range(len(equities) - 1):
        prev = equities[i]
        cur = equities[i + 1]
        if not np.isfinite(prev) or not np.isfinite(cur) or prev <= 0:
            continue
        rets[i] = (cur - prev) / prev
    return rets


def _sharpe(rets: np.ndarray, rf_daily: float, ddof: int = 1) -> float:
    valid = rets[np.isfinite(rets)]
    if len(valid) < 2:
        return 0.0
    excess = valid - rf_daily
    std = np.std(excess, ddof=ddof)
    if not np.isfinite(std) or std < 1e-8:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(252.0))


def _sortino(rets: np.ndarray, rf_daily: float) -> float:
    valid = rets[np.isfinite(rets)]
    if len(valid) < 2:
        return 0.0
    excess = valid - rf_daily
    downside = excess[excess < 0]
    if len(downside) == 0:
        return 0.0
    downside_std = np.std(downside, ddof=1)
    if not np.isfinite(downside_std) or downside_std < 1e-8:
        return 0.0
    return float(np.mean(excess) / downside_std * np.sqrt(252.0))


def calc_metrics(
    daily_values: list[dict],
    trades: list[dict],
    initial_capital: float,
    n_trading_days: int,
    label: str = "",
    fold_details: list = None,
    rf_annual: float = 0.02,
) -> BacktestResult:
    """计算回测绩效指标（含 integrity_flags / winsorized Sharpe）"""
    result = BacktestResult()
    result.trades_log = trades
    result.label = label
    result.fold_details = fold_details or []
    result.initial_capital = initial_capital
    result.n_trading_days = n_trading_days
    result.rf_annual = rf_annual
    result.integrity_flags = {"TRUSTED": True, "reasons": []}

    if not daily_values or n_trading_days <= 0:
        result.integrity_flags["TRUSTED"] = False
        result.integrity_flags["reasons"].append("empty_equity_curve")
        return result

    try:
        daily_values = _dedupe_equity_curve(daily_values)
        result.equity_curve = daily_values

        equities = np.array([v["equity"] for v in daily_values], dtype=np.float64)
        if len(equities) < 2:
            return result

        daily_rets = _safe_daily_returns(equities)
        valid_rets = daily_rets[np.isfinite(daily_rets)]
        result.daily_returns = [
            round(float(r), 6) if np.isfinite(r) else None for r in daily_rets.tolist()
        ]

        final_equity = equities[-1]
        if initial_capital > 0 and np.isfinite(final_equity):
            result.total_return = round(
                (final_equity - initial_capital) / initial_capital, 4
            )
        else:
            result.total_return = 0.0
            result.integrity_flags["TRUSTED"] = False
            result.integrity_flags["reasons"].append("invalid_final_equity")

        if result.total_return > -1 and n_trading_days > 0:
            result.annual_return = round(
                (1 + result.total_return) ** (252.0 / n_trading_days) - 1, 4
            )
        else:
            result.annual_return = -1.0

        rf_daily = rf_annual / 252.0
        result.sharpe_ratio = round(_sharpe(daily_rets, rf_daily), 4)
        result.sortino_ratio = round(_sortino(daily_rets, rf_daily), 4)

        if len(valid_rets) > 1:
            lo, hi = np.percentile(valid_rets, [1, 99])
            winsor = np.clip(valid_rets, lo, hi)
            result.sharpe_winsorized = round(_sharpe(winsor, rf_daily), 4)
            result.max_daily_return = round(float(np.max(valid_rets)), 6)
            result.min_daily_return = round(float(np.min(valid_rets)), 6)
            result.n_return_spikes = int(np.sum(np.abs(valid_rets) > SPIKE_THRESHOLD))

            if np.any(np.abs(valid_rets) > TRUST_SPIKE_THRESHOLD):
                result.integrity_flags["TRUSTED"] = False
                result.integrity_flags["reasons"].append(
                    f"daily_spike_gt_{TRUST_SPIKE_THRESHOLD:.0%}"
                )

            # Sharpe vs winsorized relative gap
            if abs(result.sharpe_winsorized) > 1e-6:
                gap = abs(result.sharpe_ratio - result.sharpe_winsorized) / abs(
                    result.sharpe_winsorized
                )
                result.integrity_flags["sharpe_vs_winsorized_gap"] = round(float(gap), 4)
                if gap >= 0.30:
                    result.integrity_flags["reasons"].append("sharpe_winsor_gap_ge_30pct")
                    # Suspicious but not auto-untrusted if no hard spikes
                    result.integrity_flags["SUSPICIOUS"] = True

        # Max drawdown
        peak = equities[0]
        max_dd = 0.0
        drawdown_start = 0
        max_dd_duration = 0
        current_dd_duration = 0

        for i, eq in enumerate(equities):
            if not np.isfinite(eq) or eq <= 0:
                result.integrity_flags["TRUSTED"] = False
                result.integrity_flags["reasons"].append(f"nonpositive_equity_at_{i}")
                continue
            if eq > peak:
                peak = eq
                current_dd_duration = 0
                drawdown_start = i
            else:
                dd = (peak - eq) / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd
                    max_dd_duration = i - drawdown_start
                if eq < peak:
                    current_dd_duration = i - drawdown_start

        result.max_drawdown = round(max_dd, 4)
        result.max_drawdown_duration = max(max_dd_duration, current_dd_duration)

        total = len(trades)
        result.total_trades = total
        if total > 0:
            profitable = sum(1 for t in trades if t.get("pnl", 0) > 0)
            result.win_rate = round(profitable / total, 4)
            gains = sum(t["pnl"] for t in trades if t.get("pnl", 0) > 0)
            losses = sum(t["pnl"] for t in trades if t.get("pnl", 0) < 0)
            if losses < 0:
                result.profit_factor = round(gains / abs(losses), 4)
            elif gains > 0:
                result.profit_factor = float("inf")
            else:
                result.profit_factor = 1.0

        if len(daily_values) > 0:
            avg_equity = float(np.mean(equities[np.isfinite(equities)]))
            total_traded = sum(
                t.get("shares", 0) * t.get("price", 0) for t in trades
            )
            if avg_equity > 0:
                periods = max(1, n_trading_days // 5)
                result.turnover_rate = round(total_traded / avg_equity / periods, 4)

        # Dedupe reasons
        result.integrity_flags["reasons"] = list(
            dict.fromkeys(result.integrity_flags.get("reasons", []))
        )

    except Exception as e:
        result.integrity_flags["TRUSTED"] = False
        result.integrity_flags["reasons"].append(f"calc_error:{e}")

    return result
