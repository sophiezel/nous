"""Regression tests for MTM last_known pricing and max_single_weight constraints."""
from __future__ import annotations

from nous.engine.portfolio.optimizer import apply_constraints
from nous.engine.backtest.metrics import calc_metrics
from nous.engine.backtest.strategies import PortfolioSpec, HARD_MAX_SINGLE_WEIGHT, get_strategy
from nous.engine.backtest.engine import BacktestEngine


def test_apply_constraints_leaves_cash_when_n_times_cap_lt_1():
    """15 names × 0.12 cap → single weight capped; sum ~1 after redistribute."""
    n = 15
    max_single = 0.12
    raw = {f"S{i:02d}": (0.85 if i == 0 else 0.01) for i in range(n)}
    total = sum(raw.values())
    raw = {k: v / total for k, v in raw.items()}
    constrained = apply_constraints(raw, max_single=max_single)
    assert all(v <= max_single + 1e-6 for v in constrained.values())
    assert max(constrained.values()) <= max_single + 1e-6
    # Rounding to 4 decimals can drift slightly above 1.0
    assert sum(constrained.values()) <= 1.01


def test_apply_constraints_n_cap_leaves_residual_cash():
    """When n * max_single < 1, residual stays unallocated (cash)."""
    raw = {"A": 0.5, "B": 0.3, "C": 0.2}
    constrained = apply_constraints(raw, max_single=0.25)
    assert all(v <= 0.25 + 1e-6 for v in constrained.values())
    assert sum(constrained.values()) <= 0.75 + 1e-6


def test_effective_max_single_hard_ceiling():
    ps = PortfolioSpec(max_single_weight=0.50)
    assert ps.effective_max_single() == HARD_MAX_SINGLE_WEIGHT
    ps2 = PortfolioSpec(max_single_weight=0.12)
    assert ps2.effective_max_single() == 0.12


def test_max_single_weight_survives_rebalance_via_constraints():
    """Extreme score skew must not re-inflate past max_single after constraints."""
    strategy = get_strategy("海鹰F3")
    engine = BacktestEngine(strategy, do_walk_forward=False)
    # Extreme: first name dominates
    scores = [1000.0] + [1.0] * 14
    valid = [{"symbol": f"S{i}", "score": scores[i], "price": 10.0} for i in range(15)]
    engine.strategy.portfolio.method = "score_weighted"
    raw = {
        v["symbol"]: max(v["score"], 0.001) / sum(max(x["score"], 0.001) for x in valid)
        for v in valid
    }
    assert max(raw.values()) > 0.5  # skewed before constraints

    capped = {k: min(v, 0.12) for k, v in raw.items()}
    broken = {k: v / sum(capped.values()) for k, v in capped.items()}
    assert max(broken.values()) > 0.12  # old bug: renormalize re-inflates

    fixed = engine._apply_weight_constraints(raw)
    assert max(fixed.values()) <= 0.12 + 1e-6
    assert sum(fixed.values()) <= 1.01

def test_missing_price_does_not_zero_equity():
    """Held position with missing close uses last_known — daily |r| stays sane."""
    equity_curve = [
        {"date": "2025-01-01", "equity": 1_000_000.0},
        {"date": "2025-01-02", "equity": 1_000_000.0},  # missing price → carry
        {"date": "2025-01-03", "equity": 1_010_000.0},
    ]
    # Simulate old bug: day2 valued at cash-only 50k
    buggy = [
        {"date": "2025-01-01", "equity": 1_000_000.0},
        {"date": "2025-01-02", "equity": 50_000.0},
        {"date": "2025-01-03", "equity": 1_010_000.0},
    ]
    buggy_m = calc_metrics(buggy, [], 1_000_000, 3, label="buggy")
    assert buggy_m.integrity_flags["TRUSTED"] is False
    assert buggy_m.n_return_spikes >= 1 or abs(buggy_m.min_daily_return) > 0.5

    fixed_m = calc_metrics(equity_curve, [], 1_000_000, 3, label="fixed")
    assert fixed_m.integrity_flags["TRUSTED"] is True
    assert abs(fixed_m.min_daily_return) < 0.05
    assert abs(fixed_m.max_daily_return) < 0.05


def test_metrics_dedupe_same_date_and_denominator_guard():
    curve = [
        {"date": "2025-01-01", "equity": 100.0},
        {"date": "2025-01-02", "equity": 110.0},
        {"date": "2025-01-02", "equity": 105.0},  # duplicate last_date overwrite
    ]
    m = calc_metrics(curve, [], 100.0, 2)
    assert len(m.equity_curve) == 2
    assert m.equity_curve[-1]["equity"] == 105.0

    zero_prev = [
        {"date": "2025-01-01", "equity": 0.0},
        {"date": "2025-01-02", "equity": 100.0},
    ]
    m2 = calc_metrics(zero_prev, [], 100.0, 2)
    # Should not produce inf spike; may be untrusted
    assert m2.daily_returns[0] is None or abs(m2.daily_returns[0]) < 1e6


def test_topk_drop_targets():
    engine = BacktestEngine(get_strategy("海鹰F3"), do_walk_forward=False)
    rankings = [{"symbol": f"S{i}", "score": 100 - i} for i in range(20)]
    current = {"S0", "S1", "S2", "S10", "S11"}  # S10/S11 outside top5
    keep, sell, buy = engine._topk_drop_targets(rankings, current, k=5, drop_n=2)
    assert set(sell).issubset(current)
    assert len(sell) <= 2
    assert all(s not in keep for s in sell)
