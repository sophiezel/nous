"""批量策略对比 — 真实策略差异化对比, 集成WF+统计验证

每个策略 = 因子集 + 模型 + 组合规则 — 不是超参摇号。
"""

from __future__ import annotations

import sys
import time
import json
import numpy as np
from datetime import date
from pathlib import Path

from nous.engine.backtest.strategies import list_strategies, get_strategy, Strategy
from nous.engine.backtest.metrics import BacktestResult


def run_batch(
    strategies: list[str] | None = None,
    start: str = "2022-01-01",
    end: str = str(date.today()),
    initial_capital: float = 1_000_000,
    wf_folds: int = 5,
    market: str = "a",
) -> dict[str, BacktestResult]:
    """Run all strategies, compare with WF + statistical validation.

    Returns:
        {strategy_name: BacktestResult}
    """
    from nous.engine.backtest.engine import BacktestEngine

    if strategies is None:
        strategies = list_strategies()
    
    results = {}
    
    for i, name in enumerate(strategies):
        print(f"\n{'='*70}")
        print(f"[{i+1}/{len(strategies)}] {name}")
        print(f"{'='*70}")
        
        try:
            strat = get_strategy(name)
            engine = BacktestEngine(
                strategy=name,
                start_date=start,
                end_date=end,
                initial_capital=initial_capital,
                wf_folds=wf_folds,
                market=market,
                do_walk_forward=True,
            )
            result = engine.run()
            results[name] = result
            
            # Per-strategy summary
            folds_info = ""
            if result.fold_details:
                fold_returns = [f["return"] for f in result.fold_details]
                folds_info = f" | Folds: {len(fold_returns)} ({min(fold_returns):+.1f}%~{max(fold_returns):+.1f}%)"
            
            print(f"  Total: {result.total_return*100:+.2f}% | "
                  f"Ann: {result.annual_return*100:+.2f}% | "
                  f"SR: {result.sharpe_ratio:.3f} | "
                  f"DD: {result.max_drawdown*100:.1f}% | "
                  f"WR: {result.win_rate*100:.0f}%{folds_info}")
            
            engine.close()
            
        except Exception as e:
            print(f"  [FAILED] {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    
    return results


def print_summary(results: dict[str, BacktestResult]):
    """Print comparison table with statistical validation."""
    from nous.engine.backtest.validator import BacktestValidator
    
    print(f"\n{'='*90}")
    print(f"{'策略对比 (Purged Walk-Forward)':^90}")
    print(f"{'='*90}")
    
    # Header
    print(f"{'Strategy':<18} {'Folds':>5} {'Total':>9} {'Ann':>8} {'SR':>7} {'DD':>7} {'WR':>6} {'Trades':>6}")
    print(f"{'-'*90}")
    
    sorted_results = sorted(results.items(), key=lambda x: x[1].sharpe_ratio, reverse=True)
    
    for name, r in sorted_results:
        n_folds = len(r.fold_details) if r.fold_details else 1
        color_sr = "✓" if r.sharpe_ratio > 0.5 else " " if r.sharpe_ratio > 0 else "✗"
        print(f"{name:<18} {n_folds:>5} {r.total_return*100:>+8.2f}% {r.annual_return*100:>+7.2f}% "
              f"{r.sharpe_ratio:>7.3f} {r.max_drawdown*100:>6.1f}% {r.win_rate*100:>5.0f}% {r.total_trades:>6}")
    
    print(f"{'='*90}")
    
    # Statistical validation
    v = BacktestValidator()
    best_name, best_result = sorted_results[0]
    worst_name, worst_result = sorted_results[-1]
    
    n_total = sum(len(r.fold_details) or 1 for _, r in sorted_results)
    dsr = v.deflated_sharpe_ratio(best_result.sharpe_ratio, n_trials=n_total)
    
    print(f"\n  最佳: {best_name} (SR={best_result.sharpe_ratio:.3f}, Ann={best_result.annual_return*100:.1f}%)")
    print(f"  Deflated SR: {dsr:.4f} {'[green]significant' if dsr < 0.05 else '[red]not significant'}")
    
    # Fold-by-fold consistency
    if best_result.fold_details and len(best_result.fold_details) > 1:
        fold_rets = [f["return"] for f in best_result.fold_details]
        positive_folds = sum(1 for r in fold_rets if r > 0)
        print(f"  Fold一致性: {positive_folds}/{len(fold_rets)} folds profitable, "
              f"mean={np.mean(fold_rets)*100:.1f}%, std={np.std(fold_rets)*100:.1f}%")
    
    # Worst strategy analysis
    print(f"\n  最差: {worst_name} (SR={worst_result.sharpe_ratio:.3f}, DD={worst_result.max_drawdown*100:.1f}%)")
    print(f"  策略间距: ΔSR={best_result.sharpe_ratio - worst_result.sharpe_ratio:.3f}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="批量策略对比回测")
    p.add_argument("--strategies", nargs="*", default=None, 
                   help=f"策略列表, 默认全部: {list_strategies()}")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default=str(date.today()))
    p.add_argument("--capital", type=float, default=1_000_000)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--market", default="a")
    args = p.parse_args()
    
    results = run_batch(
        strategies=args.strategies,
        start=args.start,
        end=args.end,
        initial_capital=args.capital,
        wf_folds=args.folds,
        market=args.market,
    )
    
    if results:
        print_summary(results)
    else:
        print("No results. All strategies failed.")
