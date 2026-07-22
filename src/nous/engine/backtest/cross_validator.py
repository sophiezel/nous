"""Combinatorial Purged Cross-Validation (CPCV) — López de Prado method.

Generates multiple backtest paths by combinatorially grouping time folds.
Each path gives a different IS/OOS split, producing a distribution of
Sharpe ratios rather than a single point estimate.

Key insight: the VARIANCE of the Sharpe distribution tells you about
overfitting risk. A wide distribution = unstable model.

Usage:
    from nous.engine.backtest.cross_validator import CombinatorialPurgedCV
    cpcv = CombinatorialPurgedCV(n_splits=6, n_paths=15)
    paths = cpcv.generate_paths(trading_days)
    for path in paths:
        train, test = path.in_sample, path.out_of_sample
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterator

import numpy as np


@dataclass
class CPCVPath:
    """A single CPCV backtest path."""
    path_id: int
    in_sample_dates: list[str]    # training dates for this path
    out_of_sample_dates: list[str]  # testing dates for this path
    embargo_dates: list[str] = field(default_factory=list)


@dataclass
class CPCVResult:
    """Aggregated CPCV results across all paths."""
    paths: list[CPCVPath]
    sharpe_ratios: list[float]    # one SR per path (OOS)
    mean_sharpe: float = 0.0
    std_sharpe: float = 0.0
    min_sharpe: float = 0.0
    max_sharpe: float = 0.0
    sharpe_ratio_distribution: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"CPCV {len(self.paths)} paths: "
            f"SR mean={self.mean_sharpe:.3f} std={self.std_sharpe:.3f} "
            f"range=[{self.min_sharpe:.3f}, {self.max_sharpe:.3f}]"
        )


class CombinatorialPurgedCV:
    """Combinatorial Purged Cross-Validation.

    Args:
        n_splits: Number of time groups to split into (default 6).
        n_paths: Maximum number of combinatorial paths (default 15).
        embargo_days: Trading days to purge between IS and OOS (default 0,
                      purging handled separately by PurgedWalkForward).
        min_is_groups: Minimum number of groups in IS (default 2).
        max_is_groups: Maximum number of groups in IS (default n_splits - 1).
    """

    def __init__(
        self,
        n_splits: int = 6,
        n_paths: int = 15,
        embargo_days: int = 0,
        min_is_groups: int = 2,
        max_is_groups: int | None = None,
    ):
        self.n_splits = n_splits
        self.n_paths = n_paths
        self.embargo_days = embargo_days
        self.min_is_groups = min_is_groups
        self.max_is_groups = max_is_groups or n_splits - 1

    def generate_paths(
        self,
        trading_days: list[str],
    ) -> list[CPCVPath]:
        """Generate CPCV backtest paths from trading days.

        Splits time into ``n_splits`` equal groups, then forms
        combinatorial IS/OOS assignments.

        Example with n_splits=6, min_is=3:
          Groups: [G1] [G2] [G3] [G4] [G5] [G6]
          Path 0: IS=[G1,G2,G3] OOS=[G4,G5,G6]
          Path 1: IS=[G1,G2,G4] OOS=[G3,G5,G6]
          Path 2: IS=[G1,G3,G5] OOS=[G2,G4,G6]
          ...
        """
        if len(trading_days) < self.n_splits * 10:
            return []

        # Split into equal groups
        group_size = len(trading_days) // self.n_splits
        groups = []
        for i in range(self.n_splits):
            start = i * group_size
            end = (i + 1) * group_size if i < self.n_splits - 1 else len(trading_days)
            groups.append(trading_days[start:end])

        group_indices = list(range(self.n_splits))
        paths = []
        path_id = 0

        # Generate all combinations of IS group sizes
        for n_is in range(self.min_is_groups, self.max_is_groups + 1):
            for is_idx_tuple in combinations(group_indices, n_is):
                if path_id >= self.n_paths:
                    break

                is_idx = list(is_idx_tuple)
                oos_idx = [i for i in group_indices if i not in is_idx]

                # Collect dates
                is_dates = []
                for idx in is_idx:
                    is_dates.extend(groups[idx])

                oos_dates = []
                embargo_dates = []
                for idx in oos_idx:
                    oos_dates.extend(groups[idx])

                # If embargo_days > 0, separate IS and OOS by removing
                # embargo_days worth of dates from the end of IS
                if self.embargo_days > 0 and is_dates:
                    embargo_start = max(0, len(is_dates) - self.embargo_days)
                    embargo_dates = is_dates[embargo_start:]
                    is_dates = is_dates[:embargo_start]

                paths.append(CPCVPath(
                    path_id=path_id,
                    in_sample_dates=is_dates,
                    out_of_sample_dates=oos_dates,
                    embargo_dates=embargo_dates,
                ))
                path_id += 1

            if path_id >= self.n_paths:
                break

        return paths

    def aggregate_results(
        self,
        oos_returns_per_path: list[np.ndarray],
    ) -> CPCVResult:
        """Aggregate OOS returns from all CPCV paths into summary statistics.

        Args:
            oos_returns_per_path: List of daily return arrays, one per path.

        Returns:
            CPCVResult with Sharpe distribution statistics.
        """
        if not oos_returns_per_path:
            return CPCVResult(paths=[], sharpe_ratios=[])

        sharpe_ratios = []
        for returns in oos_returns_per_path:
            if returns is not None and len(returns) > 1:
                mean = np.mean(returns)
                std = np.std(returns, ddof=1)
                sr = (mean / std * np.sqrt(252)) if std > 0 else 0.0
                sharpe_ratios.append(float(sr))
            else:
                sharpe_ratios.append(0.0)

        sr_arr = np.array(sharpe_ratios)
        mean_sr = float(np.mean(sr_arr))
        std_sr = float(np.std(sr_arr, ddof=1))

        # Distribution percentiles
        percentiles = {}
        for p in [5, 25, 50, 75, 95]:
            percentiles[f"p{p}"] = float(np.percentile(sr_arr, p))

        return CPCVResult(
            paths=[],  # populating paths requires original CPCVPath objects
            sharpe_ratios=sharpe_ratios,
            mean_sharpe=round(mean_sr, 4),
            std_sharpe=round(std_sr, 4),
            min_sharpe=round(float(np.min(sr_arr)), 4),
            max_sharpe=round(float(np.max(sr_arr)), 4),
            sharpe_ratio_distribution=percentiles,
        )
