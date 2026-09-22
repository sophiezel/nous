"""Meituan multi-factor reversal — unit tests (synthetic, no network)."""

from __future__ import annotations

from nous.engine.screening.meituan_reversal import (
    LayerScore,
    compose,
    render_markdown,
    run_meituan_reversal,
    score_technical,
    _synthetic_ohlcv,
    load_config,
)


def test_load_config_has_symbol():
    cfg = load_config()
    assert cfg["symbol"] == "03690"
    assert "technical" in cfg["weights"]


def test_compose_renormalizes_skipped():
    layers = [
        LayerScore("a", 50, 50),
        LayerScore("b", None, 50, skipped_reason="x"),
    ]
    assert compose(layers) == 50


def test_technical_score_range_on_synthetic():
    cfg = load_config()
    df = _synthetic_ohlcv()
    ly = score_technical(df, cfg)
    assert ly.active
    assert -100 <= float(ly.score) <= 100


def test_run_demo_smoke():
    res = run_meituan_reversal(demo=True)
    assert res.symbol == "03690"
    assert res.composite is None or -100 <= res.composite <= 100
    assert res.signal
    md = render_markdown(res)
    assert "美团" in md
    assert "technical" in md


def test_fundamentals_csv_loads():
    from nous.engine.screening.meituan_reversal import _load_fundamentals_csv, load_config, score_fundamental
    cfg = load_config()
    df = _load_fundamentals_csv(cfg)
    assert df is not None and not df.empty
    ly = score_fundamental(cfg, as_of="2026-09-09")
    assert ly.active
    assert -100 <= float(ly.score) <= 100


def test_classify_alert():
    from nous.engine.screening.meituan_reversal import classify_alert, load_config
    cfg = load_config()
    assert classify_alert(80, cfg) == "STRONG_LONG"
    assert classify_alert(25, cfg) == "LEAN_LONG"
    assert classify_alert(35, cfg) == "STRONG_LONG"
    assert classify_alert(-80, cfg) == "STRONG_SHORT"
    assert classify_alert(0, cfg) is None


def test_backtest_demo_or_live_short():
    from nous.engine.screening.meituan_reversal import backtest_meituan_reversal
    # live if data present; otherwise just ensure function returns structure
    out = backtest_meituan_reversal(hold_days=5, max_points=12)
    assert "summary" in out
    if out.get("error"):
        return
    s = out["summary"]
    assert s["n_events"] >= 0
    assert "long" in s and "short" in s
