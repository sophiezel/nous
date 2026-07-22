"""Statistical validation for backtest results — prevents overfitting deception.

Implements López de Prado's methods:
  - Deflated Sharpe Ratio (DSR): corrects for multiple testing
  - Probability of Backtest Overfitting (PBO): CSCV-based
  - Combinatorial Symmetrical Cross-Validation (CSCV)

Usage:
    from nous.engine.backtest.validator import BacktestValidator
    v = BacktestValidator()
    dsr = v.deflated_sharpe_ratio(observed_sr=1.5, n_trials=100)
    pbo = v.compute_pbo(equity_curves, n_splits=10)
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Callable

import numpy as np
from scipy import stats


@dataclass
class ValidationResult:
    """Complete statistical validation of a backtest."""
    observed_sharpe: float = 0.0
    deflated_sharpe_ratio: float = 0.0
    pbo: float = 0.0                    # Probability of Backtest Overfitting
    is_significant: bool = False          # DSR < 0.05
    is_overfit: bool = False              # PBO > 0.20
    haircut: float = 0.0                  # Recommended Sharpe haircut %
    n_trials_implicit: int = 1            # Estimated number of trials
    n_paths: int = 0
    n_splits: int = 0

    def summary(self) -> str:
        lines = [
            f"Observed Sharpe:      {self.observed_sharpe:.3f}",
            f"Deflated Sharpe Ratio: {self.deflated_sharpe_ratio:.3f} {'✓ significant' if self.is_significant else '✗ not significant'}",
            f"PBO (Overfit Prob):   {self.pbo:.3f} {'✓ low overfit' if not self.is_overfit else '✗ HIGH OVERFIT'}",
            f"Recommended haircut:   {self.haircut:.0%}",
            f"Implicit trials:      {self.n_trials_implicit}",
        ]
        return "\n".join(lines)


class BacktestValidator:
    """Statistical backtest validator — prevents overfitting self-deception.

    Key insight from López de Prado: if you try 100 strategy variations
    and pick the best one, the observed Sharpe is inflated by selection bias.
    DSR corrects for this. PBO tells you the probability that the "best"
    in-sample strategy performs poorly out-of-sample.
    """

    def __init__(self):
        pass

    # ── Deflated Sharpe Ratio ─────────────────────────────────────────

    def deflated_sharpe_ratio(
        self,
        observed_sr: float,
        n_trials: int,
        skew: float = -0.5,
        kurtosis: float = 5.0,
        t: int = 1250,
    ) -> float:
        """Compute Deflated Sharpe Ratio (López de Prado & Bailey, 2014).

        Corrects for the fact that the maximum Sharpe from N trials
        is always higher than any individual Sharpe due to selection bias.

        Args:
            observed_sr: The Sharpe ratio of the selected strategy.
            n_trials: Total number of strategy variations tried.
            skew: Return skewness (default -0.5 for equities).
            kurtosis: Return excess kurtosis (default 5.0 for equities).
            t: Number of observations (default 1250 ≈ 5 years daily).

        Returns:
            DSR ∈ [0, 1]. < 0.05 → strategy is truly significant.
                          > 0.10 → likely overfit/selection bias.
        """
        if observed_sr <= 0:
            return 1.0
        if n_trials <= 1:
            return 0.0

        # Expected maximum SR under null hypothesis (μ=0)
        # From Extreme Value Theory
        euler_gamma = 0.5772156649
        
        # Standard deviation of SR under null
        sr_std = np.sqrt(
            (1 / t) * (
                1 
                + (skew ** 2 / 4) * (observed_sr ** 2)
                + (kurtosis / (3 * t)) * (observed_sr ** 2)
            )
        )

        # Expected maximum of N standard normals
        if n_trials > 1:
            expected_max = sr_std * (
                (1 - euler_gamma) * stats.norm.ppf(1 - 1.0 / n_trials)
                + euler_gamma * stats.norm.ppf(1 - 1.0 / (n_trials * np.e))
            )
        else:
            expected_max = 0.0

        # DSR = P(SR_observed > E[max SR under null])
        if sr_std > 0:
            dsr = 1 - stats.norm.cdf(
                (observed_sr - expected_max) / sr_std
            )
        else:
            dsr = 1.0

        return float(np.clip(dsr, 0.0, 1.0))

    def estimate_implicit_trials(self, observed_sr: float, t: int = 1250) -> int:
        """Estimate how many trials would produce this SR by chance.

        If observed_sr = 2.0 and n_trials_implicit = 5, you'd need to try
        only 5 random strategies to get one with SR=2.0 — not impressive.
        If n_trials_implicit = 10000, much more convincing.
        """
        for n in [1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 5000, 10000]:
            dsr = self.deflated_sharpe_ratio(observed_sr, n, t=t)
            if dsr < 0.05:
                return n
        return 10000

    # ── PBO via CSCV ──────────────────────────────────────────────────

    def compute_pbo(
        self,
        returns_matrix: np.ndarray,  # shape: (n_periods, n_strategies)
        n_splits: int = 10,
    ) -> float:
        """Compute Probability of Backtest Overfitting via CSCV.

        CSCV (Combinatorial Symmetrical Cross-Validation):
        1. Split time into N groups
        2. Form all combinations of N/2 groups as IS, remaining as OOS
        3. For each combination, rank strategies by IS performance
        4. Check if top-IS strategies also top-OOS
        5. PBO = fraction of combinations where top-IS fails in OOS

        Args:
            returns_matrix: Shape (n_periods, n_strategies).
                           Each column is a strategy's return series.
            n_splits: Number of time splits (default 10).

        Returns:
            PBO ∈ [0, 1]. < 0.10 → low overfit risk.
                          > 0.20 → HIGH overfit risk.
        """
        if returns_matrix is None or returns_matrix.size == 0:
            return 0.0

        n_periods, n_strategies = returns_matrix.shape

        if n_periods < n_splits * 2:
            # Not enough data, use simplified method
            return self._pbo_simple(returns_matrix)

        # Split time into S equal groups
        group_size = n_periods // n_splits
        groups = []
        for i in range(n_splits):
            start = i * group_size
            end = (i + 1) * group_size if i < n_splits - 1 else n_periods
            groups.append(returns_matrix[start:end])

        # Generate all IS/OOS combinations
        # IS = n_splits/2 groups, OOS = remaining
        half = n_splits // 2
        n_combinations = min(100, len(list(combinations(range(n_splits), half))))

        is_sharpe_logits = []
        oos_sharpe_logits = []

        for is_idx in combinations(range(n_splits), half):
            oos_idx = [i for i in range(n_splits) if i not in is_idx]

            # Concatenate IS periods
            is_returns = np.concatenate([groups[i] for i in is_idx])
            oos_returns = np.concatenate([groups[i] for i in oos_idx])

            # Sharpe for each strategy
            is_sr = self._compute_sharpe_vector(is_returns)
            oos_sr = self._compute_sharpe_vector(oos_returns)

            # Rank by IS Sharpe (logit transform for normality)
            is_ranks = stats.rankdata(is_sr) / (n_strategies + 1)
            oos_ranks = stats.rankdata(oos_sr) / (n_strategies + 1)

            # Find best IS strategy
            best_is = np.argmax(is_sr)
            is_sharpe_logits.append(stats.norm.ppf(np.clip(is_ranks[best_is], 0.001, 0.999)))
            oos_sharpe_logits.append(stats.norm.ppf(np.clip(oos_ranks[best_is], 0.001, 0.999)))

            if len(is_sharpe_logits) >= n_combinations:
                break

        # PBO = fraction where OOS logit < IS logit (best IS underperforms OOS)
        is_arr = np.array(is_sharpe_logits)
        oos_arr = np.array(oos_sharpe_logits)

        # Fit logit distribution and compute PBO
        from scipy.stats import norm as norm_dist
        mu, std = norm_dist.fit(oos_arr)
        pbo = float(np.mean(norm_dist.cdf(is_arr, loc=mu, scale=std)))

        return float(np.clip(pbo, 0.0, 1.0))

    def _pbo_simple(self, returns_matrix: np.ndarray) -> float:
        """Simplified PBO for small samples."""
        n_periods, n_strategies = returns_matrix.shape
        if n_strategies < 2:
            return 0.0

        # Split in half
        mid = n_periods // 2
        is_returns = returns_matrix[:mid]
        oos_returns = returns_matrix[mid:]

        is_sr = self._compute_sharpe_vector(is_returns)
        oos_sr = self._compute_sharpe_vector(oos_returns)

        # Correlation between IS and OOS Sharpe rankings
        from scipy.stats import spearmanr
        corr, _ = spearmanr(is_sr, oos_sr)

        # PBO ≈ 1 - correlation (perfect correlation = 0 PBO)
        return float(np.clip(1.0 - abs(corr), 0.0, 1.0))

    def _compute_sharpe_vector(self, returns: np.ndarray) -> np.ndarray:
        """Compute Sharpe ratio for each column (strategy) in returns matrix."""
        n = returns.shape[0]
        if n < 2:
            return np.zeros(returns.shape[1])

        mean = np.mean(returns, axis=0)
        std = np.std(returns, axis=0, ddof=1)
        with np.errstate(divide='ignore', invalid='ignore'):
            sr = np.where(std > 0, mean / std * np.sqrt(252), 0.0)
        return np.nan_to_num(sr, nan=0.0)

    # ── Haircut ───────────────────────────────────────────────────────

    def recommended_haircut(self, dsr: float, pbo: float) -> float:
        """Recommended Sharpe ratio haircut based on DSR and PBO.

        Combines multiple testing correction (DSR) with overfitting risk (PBO).
        """
        dsr_haircut = max(0, 1 - dsr / 0.05) if dsr > 0 else 1.0
        pbo_haircut = pbo
        return float(np.clip(max(dsr_haircut, pbo_haircut), 0.0, 1.0))

    # ── Full validation ───────────────────────────────────────────────

    def validate(
        self,
        observed_sr: float,
        returns_matrix: np.ndarray | None = None,
        n_trials: int | None = None,
        n_splits: int = 10,
    ) -> ValidationResult:
        """Run complete statistical validation.

        Args:
            observed_sr: Observed out-of-sample Sharpe ratio.
            returns_matrix: Shape (n_periods, n_strategies) for PBO.
            n_trials: Number of strategy variations tried (estimate if None).
            n_splits: CSCV splits for PBO computation.

        Returns:
            ValidationResult with DSR, PBO, significance, and overfit flags.
        """
        # Estimate trials if not provided
        if n_trials is None:
            n_trials = self.estimate_implicit_trials(observed_sr)

        # DSR
        dsr = self.deflated_sharpe_ratio(observed_sr, n_trials)

        # PBO
        pbo = 0.0
        if returns_matrix is not None and returns_matrix.size > 0:
            pbo = self.compute_pbo(returns_matrix, n_splits)

        # Haircut
        haircut = self.recommended_haircut(dsr, pbo)

        return ValidationResult(
            observed_sharpe=round(observed_sr, 3),
            deflated_sharpe_ratio=round(dsr, 4),
            pbo=round(pbo, 4),
            is_significant=dsr < 0.05,
            is_overfit=pbo > 0.20,
            haircut=round(haircut, 2),
            n_trials_implicit=n_trials,
            n_paths=0,
            n_splits=n_splits,
        )
