"""Acceptance evaluator unit tests (no full market)."""
from nous.engine.screening.right_side_backtest import evaluate_acceptance, _metrics, TradeEvent


def test_metrics_empty():
    m = _metrics([])
    assert m["n"] == 0


def test_evaluate_fails_on_low_n():
    cal = {"metrics": {"n": 5, "win_rate": 0.6, "profit_factor": 1.5, "avg_ret": 0.01, "max_dd_proxy": -0.05}}
    oos = {"metrics": {"n": 5, "win_rate": 0.6, "profit_factor": 1.5, "avg_ret": 0.01, "max_dd_proxy": -0.05}}
    acc = evaluate_acceptance(cal, oos)
    assert acc["passed"] is False
    assert acc["trade_enabled"] is False


def test_metrics_wr_pf():
    trades = [
        TradeEvent("d","s","n","RS1",1, "e", 1.1, 0.1, 2, "time"),
        TradeEvent("d","s","n","RS1",1, "e", 0.95, -0.05, 2, "stop"),
        TradeEvent("d","s","n","RS2",1, "e", 1.08, 0.08, 3, "time"),
    ]
    m = _metrics(trades)
    assert m["n"] == 3
    assert m["win_rate"] == round(2/3, 4)
