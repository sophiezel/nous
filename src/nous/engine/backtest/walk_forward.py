"""Purged Walk-Forward Cross-Validation — López de Prado style.

Eliminates train/test overlap by:
  1. Sequential time splits with expanding training window
  2. Purge: removing training samples whose label window overlaps test period
  3. Embargo: mandatory gap between train end and test start

Usage:
    splitter = PurgedWalkForward(n_splits=5, embargo_days=21)
    folds = splitter.split("2020-01-01", "2025-12-31")
    for fold in folds:
        model = train(fold.train_start, fold.train_end)  # purge inside
        result = backtest(model, fold.test_start, fold.test_end)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import numpy as np
import pandas as pd


@dataclass
class Fold:
    """A single walk-forward fold."""
    index: int
    train_start: str
    train_end: str          # last day of training data
    embargo_start: str      # first day of embargo
    embargo_end: str        # last day of embargo (test starts after this)
    test_start: str         # first day of out-of-sample testing
    test_end: str           # last day of out-of-sample testing


@dataclass
class FoldResult:
    """Result for a single fold."""
    fold: Fold
    in_sample_sharpe: float = 0.0
    out_of_sample_sharpe: float = 0.0
    out_of_sample_return: float = 0.0
    out_of_sample_vol: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    n_trades: int = 0
    equity_curve: list[dict] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward result."""
    folds: list[FoldResult]
    oos_sharpe: float = 0.0
    oos_return: float = 0.0
    oos_vol: float = 0.0
    oos_max_dd: float = 0.0
    is_oos_sharpe_ratio: float = 0.0   # IS Sharpe / OOS Sharpe (should be ~1)
    monthly_turnover: float = 0.0
    deflated_sharpe_ratio: float = 0.0  # from Phase 3
    pbo: float = 0.0                     # from Phase 3


class PurgedWalkForward:
    """Purged Walk-Forward Cross-Validation.

    Args:
        n_splits: Number of folds (default 5).
        embargo_days: Calendar days between train end and test start.
        label_horizon: Days forward used for label computation
                       (used to purge overlapping training samples).
        min_train_years: Minimum years of training data per fold.
    """

    def __init__(
        self,
        n_splits: int = 5,
        embargo_days: int = 21,
        label_horizon: int = 5,
        min_train_years: int = 2,
    ):
        self.n_splits = n_splits
        self.embargo_days = embargo_days
        self.label_horizon = label_horizon
        self.min_train_years = min_train_years

    def split(
        self,
        start: str,
        end: str,
        trading_days: list[str] | None = None,
    ) -> list[Fold]:
        """Generate purged walk-forward folds.

        Each fold expands the training window and shifts the test window forward.

        Timeline:
          |======== train ========|--embargo--|== test ==|
                          |======== train ========|--embargo--|== test ==|
        """
        if trading_days is None:
            trading_days = self._get_trading_days(start, end)

        if len(trading_days) < 40:
            raise ValueError(f"Not enough trading days: {len(trading_days)}")

        # min_train_years may be float (engine passes dynamic fraction)
        min_train_days = max(20, int(float(self.min_train_years) * 250))
        total_days = len(trading_days)

        # Adaptive fold count: each test window needs room; avoid collapsed identical folds
        usable = max(0, total_days - min_train_days)
        min_test = 20
        max_folds = max(1, usable // min_test)
        n_splits = min(int(self.n_splits), max_folds)
        if n_splits < 1:
            n_splits = 1

        test_size = max(min_test, usable // n_splits)
        embargo_td = max(0, int(self.embargo_days))

        folds: list[Fold] = []
        for i in range(n_splits):
            # Non-overlapping test tiles after min_train block
            test_start_idx = min_train_days + i * test_size
            if test_start_idx >= total_days - 5:
                break

            # Last fold eats the remainder so end-date is covered once
            if i == n_splits - 1:
                test_end_idx = total_days - 1
            else:
                test_end_idx = min(test_start_idx + test_size - 1, total_days - 1)

            if test_end_idx <= test_start_idx:
                break

            # Train ends just before test, minus embargo trading days
            train_end_idx = test_start_idx - 1 - embargo_td
            if train_end_idx < 10:
                train_end_idx = max(10, test_start_idx - 1)
            train_end_idx = min(train_end_idx, test_start_idx - 1)

            train_end = trading_days[train_end_idx]
            test_start = trading_days[test_start_idx]
            test_end = trading_days[test_end_idx]

            embargo_end = (
                trading_days[min(train_end_idx + embargo_td, test_start_idx)]
                if embargo_td > 0
                else train_end
            )

            folds.append(Fold(
                index=i,
                train_start=trading_days[0],
                train_end=train_end,
                embargo_start=train_end,
                embargo_end=embargo_end,
                test_start=test_start,
                test_end=test_end,
            ))

        # Hard dedupe by test window (guards against prior collapse bugs)
        seen: set[tuple[str, str]] = set()
        unique: list[Fold] = []
        for f in folds:
            key = (f.test_start, f.test_end)
            if key in seen:
                continue
            seen.add(key)
            unique.append(f)
        return unique


    def purge_labels(
        self,
        labels_df: pd.DataFrame,
        train_end: str,
        test_start: str,
        date_col: str = "trade_date",
    ) -> pd.DataFrame:
        """Remove training samples whose label horizon overlaps with test period.

        If label_horizon=5 and test_start is 2024-03-15, any training sample
        with trade_date >= 2024-03-10 (5 trading days before test) is removed
        because its label (5-day forward return) would peek into the test period.
        """
        if labels_df is None or labels_df.empty:
            return labels_df

        # Purge boundary: label_horizon trading days before test_start
        purge_boundary = self._subtract_trading_days(test_start, self.label_horizon)

        return labels_df[labels_df[date_col] < purge_boundary].copy()

    # ── Internal helpers ──────────────────────────────────────────────

    def _get_trading_days(self, start: str, end: str) -> list[str]:
        from nous.data.storage import get_db
        from nous.data.storage.daily_bars import daily_relation_sql

        conn = get_db(write=False)
        try:
            rel = daily_relation_sql(start, end, conn=conn)
            rows = conn.execute(
                f"SELECT DISTINCT trade_date FROM {rel} "
                f"WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
                (start, end),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def _find_first_after(self, days: list[str], after: str, start_idx: int) -> int:
        """Find first index where trading_days[idx] > after."""
        for i in range(start_idx, len(days)):
            if days[i] > after:
                return i
        return len(days)

    def _subtract_trading_days(self, from_date: str, n: int) -> str:
        """Subtract n trading days from a date."""
        from nous.data.storage import get_db
        from nous.data.storage.daily_bars import (
            approx_start_for_lookback,
            daily_relation_sql,
        )

        conn = get_db(write=False)
        try:
            start = approx_start_for_lookback(from_date, n * 3)
            rel = daily_relation_sql(start, from_date, conn=conn)
            r = conn.execute(
                f"SELECT DISTINCT trade_date FROM {rel} "
                f"WHERE trade_date < ? ORDER BY trade_date DESC LIMIT ?",
                (from_date, n + 1),
            ).fetchall()
            return r[-1][0] if len(r) > n else from_date
        finally:
            conn.close()


def aggregate_folds(fold_results: list[FoldResult]) -> WalkForwardResult:
    """Aggregate out-of-sample metrics across all folds."""
    if not fold_results:
        return WalkForwardResult(folds=[])

    # Concatenate all OOS equity curves
    all_returns = []
    for fr in fold_results:
        for pt in fr.equity_curve:
            all_returns.append(pt)

    if len(all_returns) < 2:
        return WalkForwardResult(folds=fold_results)

    # Compute daily returns
    equities = [pt.get("equity", 0) for pt in all_returns]
    daily_returns = np.diff(equities) / np.array(equities[:-1])

    oos_return = (equities[-1] / equities[0] - 1) if equities[0] else 0
    oos_vol = float(np.std(daily_returns) * np.sqrt(252)) if len(daily_returns) > 0 else 0
    oos_sharpe = (float(np.mean(daily_returns)) * 252) / oos_vol if oos_vol > 0 else 0

    # Max drawdown
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (eq - peak) / peak if peak > 0 else 0
        max_dd = min(max_dd, dd)

    # IS/OOS Sharpe ratio
    is_sharpes = [fr.in_sample_sharpe for fr in fold_results if fr.in_sample_sharpe]
    avg_is_sr = float(np.mean(is_sharpes)) if is_sharpes else 0
    is_oos_ratio = avg_is_sr / oos_sharpe if oos_sharpe else float("inf")

    return WalkForwardResult(
        folds=fold_results,
        oos_sharpe=round(oos_sharpe, 3),
        oos_return=round(oos_return * 100, 2),
        oos_vol=round(oos_vol * 100, 2),
        oos_max_dd=round(max_dd * 100, 2),
        is_oos_sharpe_ratio=round(is_oos_ratio, 2),
    )
