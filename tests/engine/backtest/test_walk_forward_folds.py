"""Walk-forward fold generation must not collapse to identical OOS windows."""
from nous.engine.backtest.walk_forward import PurgedWalkForward


def test_wf_folds_have_unique_non_overlapping_test_windows():
    # ~172 trading days like 2025-11 → 2026-07
    days = [f"2025-{(11 + i // 20):02d}-{(1 + i % 20):02d}" for i in range(40)]
    # Use monotonic ISO-ish dates without calendar validity pain
    from datetime import date, timedelta
    base = date(2025, 11, 3)
    days = [(base + timedelta(days=i)).isoformat() for i in range(172) if (base + timedelta(days=i)).weekday() < 5]

    wf = PurgedWalkForward(n_splits=5, embargo_days=2, min_train_years=0.15)
    folds = wf.split(days[0], days[-1], days)
    assert len(folds) >= 2
    windows = [(f.test_start, f.test_end) for f in folds]
    assert len(windows) == len(set(windows)), f"duplicate folds: {windows}"

    # Non-overlapping: each next test_start > previous test_end
    for a, b in zip(folds, folds[1:]):
        assert a.test_end < b.test_start, f"overlap {a.test_start}-{a.test_end} vs {b.test_start}-{b.test_end}"
