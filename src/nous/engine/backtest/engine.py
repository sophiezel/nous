"""回测引擎 V2 — 因子管道 → 模型训练 → 组合优化 → 成本模拟

集成 PointInTimeDataHandler + WalkForward + Portfolio Optimizer + 真实A股成本
每个WF fold自动重训模型, 严格PIT时间门禁。

架构:
  Strategy → FactorSpec(α来源) + ModelSpec(模型) + PortfolioSpec(组合) + CostSpec(成本)
  BacktestEngine → 日循环(MTM + 换仓) + WF折(训练+预测)
  BacktestResult → equity_curve + trades + metrics + fold_details
"""

from __future__ import annotations

import time
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from nous.engine.backtest.data_handler import PointInTimeDataHandler
from nous.engine.backtest.strategies import (
    Strategy, FactorSpec, ModelSpec, PortfolioSpec, CostSpec,
    get_strategy, list_strategies,
)
from nous.engine.backtest.metrics import BacktestResult, calc_metrics

logger = logging.getLogger(__name__)

STALE_PRICE_FORCE_EXIT_DAYS = 3
WEIGHT_ASSERT_EPS = 0.02  # allow ~2% lot-rounding slack vs max_single


class BacktestEngine:
    """回测引擎 V2 — 因子驱动的WF回测"""

    def __init__(self, strategy: str | Strategy, **overrides):
        """
        Args:
            strategy: strategy name or Strategy object
            **overrides: override any strategy attribute
                e.g., start_date='2025-01-01', end_date='2026-01-01',
                      initial_capital=1_000_000, wf_folds=5
        """
        if isinstance(strategy, str):
            self.strategy = get_strategy(strategy)
        else:
            self.strategy = strategy

        # Apply overrides
        self.start_date = overrides.get("start_date", "2022-01-01")
        self.end_date = overrides.get("end_date", str(date.today()))
        self.initial_capital = float(overrides.get("initial_capital", 1_000_000))
        self.wf_folds = int(overrides.get("wf_folds", 5))
        self.do_walk_forward = overrides.get("do_walk_forward", True)
        self.market = overrides.get("market", self.strategy.market)
        
        # Internal state
        self._dh: PointInTimeDataHandler | None = None
        self._factor_df: pd.DataFrame | None = None

    # ── Main entry ──────────────────────────────────────────────────

    def run(self) -> BacktestResult:
        """Run backtest. If do_walk_forward=True, runs WF with retraining."""
        if not self.do_walk_forward:
            return self._run_single()
        return self._run_walk_forward()

    def _run_single(self) -> BacktestResult:
        """Simple single-period backtest (no retraining)."""
        dh = self._get_dh(self.end_date)
        trade_dates = dh.get_trading_days(self.start_date, self.end_date)
        
        if len(trade_dates) < self.strategy.rebalance_freq + 5:
            raise ValueError(f"交易日不足: got {len(trade_dates)}")
        
        factor_df = self._load_factors()
        if factor_df is None or len(factor_df) < 100:
            logger.warning("因子数据不足, 退回简单打分 (FALLBACK_MOMENTUM)")
            return self._run_simple_screen(trade_dates, dh)
        
        model = self._train_model(factor_df, trade_dates[:len(trade_dates)//2])
        
        return self._simulate(
            trade_dates=trade_dates,
            dh=dh,
            model=model,
            factor_df=factor_df,
            label=f"{self.strategy.name}(单期)",
        )

    def _run_walk_forward(self) -> BacktestResult:
        """Walk-Forward: 每折重训模型, OOS预测"""
        from nous.engine.backtest.walk_forward import PurgedWalkForward
        
        dh = self._get_dh(self.end_date)
        all_dates = dh.get_trading_days(self.start_date, self.end_date)
        
        if len(all_dates) < 60:
            raise ValueError(f"交易日不足: {len(all_dates)} (最少60天)")
        
        factor_df = self._load_factors()
        use_factors = factor_df is not None and len(factor_df) >= 500
        
        # Dynamic min_train_years: use at most 1/3 of available data
        available_years = len(all_dates) / 252
        min_train = min(0.2, available_years / self.wf_folds * 0.5)
        
        wf = PurgedWalkForward(
            n_splits=self.wf_folds,
            embargo_days=min(self.strategy.wf_embargo_days, 3),
            min_train_years=min_train,
        )
        folds = wf.split(self.start_date, self.end_date, all_dates)
        
        if len(folds) < 2:
            logger.warning(f"WF折数不足({len(folds)}), 回退到单期")
            return self._run_single()
        
        logger.info(f"WF回测: {len(folds)}折, 策略={self.strategy.name}")
        
        # Aggregate across folds
        all_equity = []
        all_trades = []
        fold_results = []
        equity_offset = 0.0
        cumulative_equity = self.initial_capital

        for i, fold in enumerate(folds):
            fold_start = fold.test_start
            fold_end = fold.test_end
            fold_dates = [d for d in all_dates if fold_start <= d <= fold_end]
            
            if len(fold_dates) < 5:
                continue
            
            # Train on in-sample data
            train_dates = [d for d in all_dates if d < fold_start]
            train_end_date = train_dates[-1] if train_dates else fold_start
            
            model = None
            if use_factors:
                fold_train_df = self._subset_factors(factor_df, train_end_date)
                if fold_train_df is not None and len(fold_train_df) >= 500:
                    try:
                        model = self._train_model(fold_train_df, train_dates[-252:] if len(train_dates) > 252 else train_dates)
                    except Exception as e:
                        logger.warning(f"模型训练失败@{train_end_date}: {e}, 回退简单打分")
            
            # Simulate fold — use FULL factor_df for predictions (not just train subset)
            result = self._simulate(
                trade_dates=fold_dates,
                dh=dh,
                model=model,
                factor_df=factor_df if model else None,
                initial_capital=cumulative_equity,
                label=f"{self.strategy.name}(Fold {i+1}/{len(folds)})",
            )
            
            # Offset equity curve to be continuous
            if all_equity and result.equity_curve:
                first_eq = result.equity_curve[0]["equity"]
                ratio = cumulative_equity / first_eq if first_eq > 0 else 1.0
                for pt in result.equity_curve:
                    pt["equity"] = round(pt["equity"] * ratio, 2)
            
            all_equity.extend(result.equity_curve)
            all_trades.extend(result.trades_log)
            
            if result.equity_curve:
                cumulative_equity = result.equity_curve[-1]["equity"]
            
            fold_results.append({
                "fold": i + 1,
                "start": fold_start,
                "end": fold_end,
                "train_end": train_end_date,
                "model_trained": model is not None,
                "n_trades": len(result.trades_log),
                "return": result.total_return if result.total_return else 0,
                "sharpe": result.sharpe_ratio if result.sharpe_ratio else 0,
            })
            
            logger.info(f"  Fold {i+1}: {fold_start}→{fold_end}, "
                       f"return={fold_results[-1]['return']:+.2f}%, "
                       f"model={'✓' if model else '✗'}")

        # Compute aggregate metrics
        result = calc_metrics(
            daily_values=all_equity,
            trades=all_trades,
            initial_capital=self.initial_capital,
            n_trading_days=len(all_dates),
            fold_details=fold_results,
            label=f"{self.strategy.name} (WF {len(folds)}折)",
        )
        if not use_factors:
            flags = dict(result.integrity_flags or {})
            flags["FALLBACK_MOMENTUM"] = True
            flags["TRUSTED"] = False
            reasons = list(flags.get("reasons") or [])
            reasons.append("FALLBACK_MOMENTUM")
            flags["reasons"] = list(dict.fromkeys(reasons))
            result.integrity_flags = flags
            result.label = f"{self.strategy.name} (WF {len(folds)}折·FALLBACK_MOMENTUM)"
        return result

    # ── Simulation loop ──────────────────────────────────────────────

    def _mark_to_market(
        self,
        positions: dict[str, dict],
        cash: float,
        dt: str,
        dh: PointInTimeDataHandler,
        last_known: dict[str, float],
        stale_days: dict[str, int],
    ) -> tuple[float, float]:
        """MTM with last_known_price — never value a held position at zero."""
        total_mv = 0.0
        if not positions:
            return cash, cash

        prices = self._get_close_prices(list(positions.keys()), dt, dh)
        for sym, pos in positions.items():
            p = prices.get(sym)
            if p and p > 0:
                last_known[sym] = float(p)
                stale_days[sym] = 0
                total_mv += pos["shares"] * p
            else:
                stale_days[sym] = stale_days.get(sym, 0) + 1
                lk = last_known.get(sym)
                if lk and lk > 0:
                    total_mv += pos["shares"] * lk
                    logger.debug(
                        "MTM stale %s@%s day=%d using last_known=%.4f",
                        sym, dt, stale_days[sym], lk,
                    )
                else:
                    # No price ever — carry cost basis rather than zero
                    cost = float(pos.get("cost") or 0)
                    if cost > 0:
                        last_known[sym] = cost
                        total_mv += pos["shares"] * cost
                    logger.warning(
                        "MTM missing price for %s@%s with no last_known; using cost=%.4f",
                        sym, dt, cost,
                    )
        return cash + total_mv, cash + total_mv

    def _resolve_sell_price(
        self,
        sym: str,
        dt: str,
        dh: PointInTimeDataHandler,
        last_known: dict[str, float],
        live_prices: dict[str, float] | None = None,
    ) -> float | None:
        """Sell price: live close → last_known. Never silently skip with 0 proceeds."""
        if live_prices and live_prices.get(sym) and live_prices[sym] > 0:
            return float(live_prices[sym])
        prices = self._get_close_prices([sym], dt, dh)
        p = prices.get(sym)
        if p and p > 0:
            last_known[sym] = float(p)
            return float(p)
        lk = last_known.get(sym)
        if lk and lk > 0:
            return float(lk)
        return None

    def _sell_position(
        self,
        sym: str,
        pos: dict,
        dt: str,
        cash: float,
        costs,
        last_known: dict[str, float],
        dh: PointInTimeDataHandler,
        trades_log: list,
        live_prices: dict[str, float] | None = None,
        discount: float = 1.0,
    ) -> float:
        sp = self._resolve_sell_price(sym, dt, dh, last_known, live_prices)
        if sp is None or sp <= 0:
            logger.warning("Cannot sell %s@%s: no price; holding", sym, dt)
            return cash
        sp = sp * discount
        sell_eff = costs.effective_sell_price(sp)
        proceeds = pos["shares"] * sell_eff
        cash += proceeds
        pnl = proceeds - pos["shares"] * pos["cost"]
        trades_log.append({
            "date": dt, "symbol": sym, "side": "SELL",
            "shares": pos["shares"], "price": round(sell_eff, 2),
            "pnl": round(pnl, 2),
        })
        return cash

    def _topk_drop_targets(
        self,
        rankings: list[dict],
        current_symbols: set[str],
        k: int,
        drop_n: int,
    ) -> tuple[list[str], list[str], list[str]]:
        """
        Qlib-style Topk-Drop:
        - Target hold set ≈ top K by score
        - Sell up to drop_n worst holdings that fall outside top K (or lowest score)
        - Buy same count of best unheld names
        Returns (keep, sell, buy) symbol lists.
        """
        ranked_syms = [r["symbol"] for r in rankings]
        score_map = {r["symbol"]: r.get("score", 0.0) for r in rankings}
        topk = set(ranked_syms[:k])

        if not current_symbols:
            return [], [], ranked_syms[:k]

        # Holdings ranked worse than K (or missing from ranking)
        outsiders = [
            s for s in current_symbols
            if s not in topk or s not in score_map
        ]
        outsiders.sort(key=lambda s: score_map.get(s, -1e18))
        sell = outsiders[:drop_n]

        keep = [s for s in current_symbols if s not in sell]
        slots = max(0, k - len(keep))
        buy_candidates = [s for s in ranked_syms if s not in keep and s not in sell]
        buy = buy_candidates[:slots]
        return keep, sell, buy

    def _simulate(
        self,
        trade_dates: list[str],
        dh: PointInTimeDataHandler,
        model=None,
        factor_df: pd.DataFrame | None = None,
        initial_capital: float = None,
        label: str = "",
    ) -> BacktestResult:
        """Daily MTM + Topk-Drop rebalance simulation loop."""
        if initial_capital is None:
            initial_capital = self.initial_capital

        cash = float(initial_capital)
        positions: dict[str, dict] = {}
        equity_curve = []
        trades_log = []
        costs = self.strategy.costs
        ps = self.strategy.portfolio
        last_known: dict[str, float] = {}
        stale_days: dict[str, int] = {}
        universe_cache: dict[str, list] = {}

        for i, dt in enumerate(trade_dates):
            # Force-exit chronically stale names (liquidity discount)
            for sym in list(positions.keys()):
                if stale_days.get(sym, 0) >= STALE_PRICE_FORCE_EXIT_DAYS:
                    logger.info(
                        "Force exit stale %s@%s after %d days",
                        sym, dt, stale_days[sym],
                    )
                    cash = self._sell_position(
                        sym, positions[sym], dt, cash, costs,
                        last_known, dh, trades_log, discount=0.95,
                    )
                    positions.pop(sym, None)
                    stale_days.pop(sym, None)

            equity, _ = self._mark_to_market(
                positions, cash, dt, dh, last_known, stale_days
            )
            equity_curve.append({"date": dt, "equity": round(equity, 2)})

            if i == 0 or i % self.strategy.rebalance_freq != 0:
                continue

            if dt not in universe_cache:
                dh_dt = PointInTimeDataHandler(dt)
                universe_cache[dt] = dh_dt.get_universe(self.market)
                dh_dt.close()
            universe = universe_cache[dt]
            if len(universe) < 10:
                continue

            rankings = self._rank_stocks(dt, universe, model, factor_df, dh)
            if not rankings:
                continue

            k = ps.max_positions
            drop_n = max(1, getattr(ps, "drop_n", 3))
            # Honor turnover_limit roughly: drop_n <= turnover_limit * K
            max_drop_by_to = max(1, int(ps.turnover_limit * k))
            drop_n = min(drop_n, max_drop_by_to)

            keep, sell_syms, buy_syms = self._topk_drop_targets(
                rankings, set(positions.keys()), k, drop_n,
            )

            live_prices = self._get_close_prices(
                list(set(list(positions.keys()) + buy_syms)), dt, dh
            )
            for sym in sell_syms:
                if sym not in positions:
                    continue
                cash = self._sell_position(
                    sym, positions[sym], dt, cash, costs,
                    last_known, dh, trades_log, live_prices=live_prices,
                )
                positions.pop(sym, None)
                stale_days.pop(sym, None)

            # Target book = keep + buy (up to K), ranked for weight construction
            score_map = {r["symbol"]: r for r in rankings}
            target_syms = keep + buy_syms
            # Prefer higher-score names if over K
            target_syms = sorted(
                target_syms,
                key=lambda s: score_map.get(s, {}).get("score", -1e18),
                reverse=True,
            )[:k]
            candidates = []
            for s in target_syms:
                if s in score_map:
                    candidates.append(score_map[s])
                elif s in positions:
                    candidates.append({
                        "symbol": s,
                        "score": 0.0,
                        "close": last_known.get(s, 0),
                    })

            positions, cash, new_trades = self._rebalance_to_targets(
                dt, cash, positions, candidates, dh, last_known, equity,
            )
            trades_log.extend(new_trades)

        # Liquidate at end — update last equity point in place (no duplicate date)
        if positions:
            last_date = trade_dates[-1]
            live_prices = self._get_close_prices(list(positions.keys()), last_date, dh)
            for sym, pos in list(positions.items()):
                cash = self._sell_position(
                    sym, pos, last_date, cash, costs,
                    last_known, dh, trades_log, live_prices=live_prices,
                )
            positions = {}
            if equity_curve and equity_curve[-1]["date"] == last_date:
                equity_curve[-1]["equity"] = round(cash, 2)
            else:
                equity_curve.append({"date": last_date, "equity": round(cash, 2)})

        return calc_metrics(
            daily_values=equity_curve,
            trades=trades_log,
            initial_capital=initial_capital,
            n_trading_days=len(trade_dates),
            label=label,
        )

    # ── Stock ranking ────────────────────────────────────────────────

    def _rank_stocks(
        self,
        dt: str,
        universe: list[str],
        model,
        factor_df: pd.DataFrame | None,
        dh: PointInTimeDataHandler,
    ) -> list[dict]:
        """Rank stocks for a given date. Returns [{symbol, score, close}, ...] sorted by score desc."""
        
        rankings = []
        
        if model is not None and factor_df is not None:
            # Factor-based prediction
            try:
                # Get factor values for universe at this date
                # Convert dt string to match factor_df's trade_date type
                date_factors = factor_df[
                    (factor_df["trade_date"].astype(str).str[:10] == dt) & 
                    (factor_df["symbol"].isin(universe))
                ]
                if len(date_factors) < 5:
                    return []
                
                # Get feature columns (K* prefixed)
                feature_cols = [c for c in date_factors.columns if c.startswith("K")]
                if not feature_cols:
                    return []
                
                X = date_factors[feature_cols].fillna(0)
                
                # Get top K features — keep DataFrame so LGBM feature names match training
                top_cols = feature_cols[:self.strategy.model.top_k_features]
                X_sub = X[top_cols]
                
                preds = model.predict(X_sub)
                
                # Build rankings
                for idx, row in date_factors.iterrows():
                    rankings.append({
                        "symbol": row["symbol"],
                        "score": float(preds[date_factors.index.get_loc(idx)]),
                        "close": float(row.get("close", 0)),
                    })
                
            except Exception as e:
                logger.warning(f"模型预测失败@{dt}: {e}")
                return self._fallback_screen(dt, universe, dh)
        else:
            # Fallback: simple scoring
            rankings = self._fallback_screen(dt, universe, dh)
        
        rankings.sort(key=lambda x: x["score"], reverse=True)
        return rankings

    def _fallback_screen(self, dt: str, universe: list[str], dh) -> list[dict]:
        """Simple scoring fallback when no factor model available."""
        rankings = []
        dh_dt = PointInTimeDataHandler(dt)
        
        # Sample from universe to keep it fast
        sample = universe[:200] if len(universe) > 200 else universe
        
        for sym in sample:
            try:
                close = dh_dt.get_close(sym)
                if close is None or close <= 0:
                    continue
                
                # Simple momentum score
                daily = dh_dt.get_daily(sym, days=30)
                if len(daily) < 10:
                    continue
                
                closes = daily["close"].values
                mom_5d = (closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0
                mom_20d = (closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0
                
                # Volume ratio
                vol_5d = daily["volume"].values[-5:].mean() if len(daily) >= 5 else 0
                vol_20d = daily["volume"].values[-20:].mean() if len(daily) >= 20 else 0
                vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0
                
                score = mom_5d * 0.4 + mom_20d * 0.3 + (vol_ratio - 1) * 0.3
                
                rankings.append({
                    "symbol": sym,
                    "score": round(float(score), 4),
                    "close": float(close),
                })
            except Exception:
                pass
        
        dh_dt.close()
        rankings.sort(key=lambda x: x["score"], reverse=True)
        return rankings

    # ── Portfolio construction ──────────────────────────────────────

    def _compute_raw_weights(
        self,
        valid: list[dict],
        dh: PointInTimeDataHandler,
        dt: str,
    ) -> dict[str, float]:
        """Raw weights by portfolio method; HRP/max_sharpe wired to optimizer."""
        ps = self.strategy.portfolio
        method = ps.method
        n = len(valid)
        if n == 0:
            return {}

        if method in ("hrp", "max_sharpe", "risk_parity"):
            try:
                from nous.engine.portfolio.optimizer import (
                    optimize_hrp, optimize_max_sharpe,
                )
                symbols = [v["symbol"] for v in valid]
                prices_df = self._get_price_matrix(symbols, dt, dh, days=60)
                if prices_df is not None and prices_df.shape[1] >= 3 and len(prices_df) >= 20:
                    if method == "max_sharpe":
                        weights = optimize_max_sharpe(prices_df)
                    else:
                        # hrp and risk_parity → HRP
                        weights = optimize_hrp(prices_df)
                    # Keep only valid symbols; fill missing with tiny equal slice
                    if weights:
                        missing = [s for s in symbols if s not in weights]
                        if missing:
                            rem = max(0.0, 1.0 - sum(weights.values()))
                            fill = rem / len(missing) if missing else 0.0
                            for s in missing:
                                weights[s] = fill
                        return {s: float(weights.get(s, 0.0)) for s in symbols}
                logger.warning(
                    "Optimizer %s failed/insufficient data@%s → equal_weight",
                    method, dt,
                )
            except Exception as e:
                logger.warning("Optimizer %s error@%s: %s → equal_weight", method, dt, e)

        if method == "score_weighted":
            total_score = sum(max(v["score"], 0.001) for v in valid)
            return {
                v["symbol"]: max(v["score"], 0.001) / total_score for v in valid
            }

        # equal_weight (default)
        w = 1.0 / n
        return {v["symbol"]: w for v in valid}

    def _get_price_matrix(
        self, symbols: list[str], as_of: str, dh: PointInTimeDataHandler, days: int = 60,
    ) -> pd.DataFrame | None:
        """PIT price matrix for optimizer (last `days` rows ending at as_of)."""
        if not symbols:
            return None
        try:
            conn = dh.conn
            placeholders = ",".join(["?"] * len(symbols))
            from nous.data.storage.daily_bars import (
                approx_start_for_lookback,
                daily_relation_sql,
            )

            start = approx_start_for_lookback(as_of, days * 2)
            rel = daily_relation_sql(start, as_of, conn=conn)
            rows = conn.execute(
                f"""SELECT trade_date, symbol, close FROM {rel}
                    WHERE symbol IN ({placeholders}) AND trade_date <= ?
                    ORDER BY trade_date""",
                (*symbols, as_of),
            ).fetchall()
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=["trade_date", "symbol", "close"])
            pivot = df.pivot(index="trade_date", columns="symbol", values="close")
            pivot = pivot.ffill().dropna(axis=1, how="all").tail(days)
            # Drop columns with too many NaN
            pivot = pivot.dropna(axis=1, thresh=max(10, days // 3))
            if pivot.shape[1] < 3:
                return None
            return pivot
        except Exception as e:
            logger.debug("price matrix failed: %s", e)
            return None

    def _apply_weight_constraints(self, weights: dict[str, float]) -> dict[str, float]:
        """Clip via apply_constraints; do NOT renormalize to sum=1 (cash residual OK)."""
        from nous.engine.portfolio.optimizer import apply_constraints

        ps = self.strategy.portfolio
        max_single = ps.effective_max_single()
        if not weights:
            return {}
        constrained = apply_constraints(weights, max_single=max_single)
        # Drop tiny weights
        return {
            k: v for k, v in constrained.items()
            if v >= ps.min_single_weight
        }

    def _build_portfolio(
        self, dt: str, cash: float, candidates: list[dict], dh,
        last_known: dict[str, float] | None = None,
        book_equity: float | None = None,
    ) -> tuple[dict, float, list[dict]]:
        """Build portfolio from ranked candidates with sticky max_single constraints."""
        ps = self.strategy.portfolio
        costs = self.strategy.costs
        last_known = last_known or {}

        if not candidates:
            return {}, cash, []

        symbols = [c["symbol"] for c in candidates[: ps.max_positions * 2]]
        prices = self._get_close_prices(symbols, dt, dh)

        valid = []
        for c in candidates:
            sym = c["symbol"]
            p = prices.get(sym) or last_known.get(sym)
            if p and p > 0:
                last_known[sym] = float(p)
                valid.append({"symbol": sym, "price": float(p), "score": c.get("score", 0)})
            if len(valid) >= ps.max_positions:
                break

        if len(valid) < 3:
            return {}, cash, []

        raw = self._compute_raw_weights(valid, dh, dt)
        weights = self._apply_weight_constraints(raw)

        positions = {}
        trades = []
        remaining_cash = cash
        equity_ref = book_equity if book_equity and book_equity > 0 else cash
        available_cash = cash * (1 - ps.cash_buffer)
        max_single = ps.effective_max_single()

        for v in valid:
            sym = v["symbol"]
            w = weights.get(sym, 0)
            if w < ps.min_single_weight:
                continue

            alloc = available_cash * w
            # Cap absolute allocation by max_single of book equity
            alloc = min(alloc, equity_ref * (max_single + WEIGHT_ASSERT_EPS))
            buy_eff = costs.effective_buy_price(v["price"])
            max_shares = int(alloc / buy_eff)
            shares = (max_shares // 100) * 100
            if shares < 100:
                continue

            total_cost = shares * buy_eff + costs.buy_cost(shares * buy_eff)
            if total_cost > remaining_cash:
                continue

            remaining_cash -= total_cost
            positions[sym] = {"shares": shares, "cost": buy_eff}
            last_known[sym] = v["price"]
            trades.append({
                "date": dt, "symbol": sym, "side": "BUY",
                "shares": shares, "price": round(buy_eff, 2), "pnl": 0.0,
            })

            # Post-trade weight assertion
            w_actual = (shares * v["price"]) / equity_ref if equity_ref > 0 else 0
            if w_actual > max_single + WEIGHT_ASSERT_EPS:
                logger.warning(
                    "Weight assert fail %s: actual=%.3f > max=%.3f@%s",
                    sym, w_actual, max_single, dt,
                )

        return positions, remaining_cash, trades

    def _rebalance_to_targets(
        self,
        dt: str,
        cash: float,
        positions: dict[str, dict],
        candidates: list[dict],
        dh: PointInTimeDataHandler,
        last_known: dict[str, float],
        book_equity: float,
    ) -> tuple[dict, float, list[dict]]:
        """
        Resize book toward constrained target weights for `candidates`.
        Keeps existing lots when possible; buys/sells to approach targets.
        """
        ps = self.strategy.portfolio
        costs = self.strategy.costs
        trades: list[dict] = []

        if not candidates:
            # Sell everything leftover
            for sym, pos in list(positions.items()):
                cash = self._sell_position(
                    sym, pos, dt, cash, costs, last_known, dh, trades,
                )
                positions.pop(sym, None)
            return positions, cash, trades

        symbols = [c["symbol"] for c in candidates]
        prices = self._get_close_prices(symbols, dt, dh)
        valid = []
        for c in candidates:
            sym = c["symbol"]
            p = prices.get(sym) or last_known.get(sym)
            if p and p > 0:
                last_known[sym] = float(p)
                valid.append({"symbol": sym, "price": float(p), "score": c.get("score", 0)})

        if len(valid) < 1:
            return positions, cash, trades

        raw = self._compute_raw_weights(valid, dh, dt)
        weights = self._apply_weight_constraints(raw)
        max_single = ps.effective_max_single()
        target_set = {v["symbol"] for v in valid}

        # Sell names no longer in target
        for sym in list(positions.keys()):
            if sym not in target_set:
                cash = self._sell_position(
                    sym, positions[sym], dt, cash, costs, last_known, dh, trades,
                    live_prices=prices,
                )
                positions.pop(sym, None)

        equity_ref = max(book_equity, cash)
        available = cash * (1 - ps.cash_buffer)

        # First pass: reduce overweight holdings
        for v in valid:
            sym = v["symbol"]
            w = weights.get(sym, 0)
            target_value = equity_ref * w
            pos = positions.get(sym)
            if not pos:
                continue
            current_value = pos["shares"] * v["price"]
            if current_value > target_value * 1.15 and pos["shares"] >= 200:
                # Sell excess lots
                excess_value = current_value - target_value
                sell_shares = int(excess_value / v["price"] / 100) * 100
                sell_shares = min(sell_shares, pos["shares"] - 100)
                if sell_shares >= 100:
                    sell_eff = costs.effective_sell_price(v["price"])
                    proceeds = sell_shares * sell_eff
                    cash += proceeds
                    pnl = proceeds - sell_shares * pos["cost"]
                    pos["shares"] -= sell_shares
                    trades.append({
                        "date": dt, "symbol": sym, "side": "SELL",
                        "shares": sell_shares, "price": round(sell_eff, 2),
                        "pnl": round(pnl, 2),
                    })
                    if pos["shares"] < 100:
                        positions.pop(sym, None)

        available = cash * (1 - ps.cash_buffer)

        # Second pass: buy underweight / new names
        for v in valid:
            sym = v["symbol"]
            w = weights.get(sym, 0)
            if w < ps.min_single_weight:
                continue
            target_value = min(equity_ref * w, equity_ref * (max_single + WEIGHT_ASSERT_EPS))
            pos = positions.get(sym)
            current_value = pos["shares"] * v["price"] if pos else 0.0
            need = target_value - current_value
            if need < v["price"] * 100:
                continue
            buy_eff = costs.effective_buy_price(v["price"])
            max_shares = int(min(need, available) / buy_eff)
            shares = (max_shares // 100) * 100
            if shares < 100:
                continue
            total_cost = shares * buy_eff + costs.buy_cost(shares * buy_eff)
            if total_cost > cash:
                continue
            cash -= total_cost
            available -= total_cost
            if pos:
                # Average cost
                total_shares = pos["shares"] + shares
                pos["cost"] = (
                    (pos["shares"] * pos["cost"] + shares * buy_eff) / total_shares
                )
                pos["shares"] = total_shares
            else:
                positions[sym] = {"shares": shares, "cost": buy_eff}
            last_known[sym] = v["price"]
            trades.append({
                "date": dt, "symbol": sym, "side": "BUY",
                "shares": shares, "price": round(buy_eff, 2), "pnl": 0.0,
            })

            w_actual = (positions[sym]["shares"] * v["price"]) / equity_ref
            if w_actual > max_single + WEIGHT_ASSERT_EPS:
                logger.warning(
                    "Weight assert fail %s: actual=%.3f > max=%.3f@%s",
                    sym, w_actual, max_single, dt,
                )

        return positions, cash, trades

    # ── Model training ──────────────────────────────────────────────

    def _train_model(self, factor_df: pd.DataFrame, train_dates: list[str]):
        """Train LightGBM model on factor data."""
        try:
            import lightgbm as lgb
        except ImportError:
            logger.warning("LightGBM未安装, 退回简单打分")
            return None
        
        model_spec = self.strategy.model
        
        # Subset to train dates
        if train_dates:
            train_df = factor_df[pd.to_datetime(factor_df["trade_date"]).astype(str).str[:10].isin(train_dates)]
            if len(train_df) < model_spec.train_min_samples:
                train_df = factor_df
        else:
            train_df = factor_df
        
        if len(train_df) < model_spec.train_min_samples:
            return None
        
        # Feature columns
        feature_cols = [c for c in train_df.columns if c.startswith("K")]
        if not feature_cols:
            return None
        
        feature_cols = feature_cols[:model_spec.top_k_features]
        
        # Label: forward N-day return
        fwd = model_spec.forward_return_days
        train_df = train_df.copy()
        train_df["label"] = train_df.groupby("symbol")["close"].transform(
            lambda x: x.shift(-fwd) / x - 1 if len(x) > fwd else np.nan
        )
        train_df = train_df.dropna(subset=["label"] + feature_cols)
        
        if len(train_df) < model_spec.train_min_samples:
            return None
        
        X = train_df[feature_cols].fillna(0)
        y = train_df["label"].values
        
        # Clip extreme labels
        y = np.clip(y, -0.5, 0.5)
        
        # Train
        params = {
            "objective": "regression",
            "metric": "rmse",
            "num_leaves": model_spec.params.get("num_leaves", 31),
            "learning_rate": model_spec.params.get("learning_rate", 0.05),
            "min_child_samples": model_spec.params.get("min_child_samples", 20),
            "verbose": -1,
            "n_jobs": 2,
        }
        
        model = lgb.LGBMRegressor(**params)
        model.fit(X, y)  # DataFrame → preserves feature names for predict
        model._nous_features = list(feature_cols)  # type: ignore[attr-defined]
        
        logger.debug(f"  模型训练完成: {len(train_df)}样本, {len(feature_cols)}特征")
        return model

    # ── Factor data ─────────────────────────────────────────────────

    def _load_factors(self) -> pd.DataFrame | None:
        """Load pre-computed factor snapshot."""
        if self._factor_df is not None:
            return self._factor_df
        
        factor_dir = Path.home() / "nous-data" / "factors"
        if not factor_dir.exists():
            factor_dir = Path(__file__).resolve().parents[4] / "data" / "factors"
        
        factor_path = factor_dir / "latest.parquet"
        
        if not factor_path.exists():
            # Try alternative path
            factor_path = factor_dir / "a_latest.parquet"
        
        if not factor_path.exists():
            logger.info(f"因子快照不存在: {factor_path}")
            return None
        
        try:
            self._factor_df = pd.read_parquet(factor_path)
            logger.info(f"加载因子: {len(self._factor_df)}行, "
                       f"{len([c for c in self._factor_df.columns if c.startswith('K')])}因子")
            return self._factor_df
        except Exception as e:
            logger.warning(f"因子加载失败: {e}")
            return None

    def _subset_factors(self, df: pd.DataFrame, as_of_date: str) -> pd.DataFrame | None:
        """PIT-gated factor subset: only data ≤ as_of_date."""
        if "trade_date" not in df.columns:
            return df
        
        subset = df[pd.to_datetime(df["trade_date"]).astype(str).str[:10] <= as_of_date]
        if len(subset) < 500:
            return None
        return subset

    # ── Fallback simple screen (from old engine) ────────────────────

    def _run_simple_screen(self, trade_dates, dh) -> BacktestResult:
        """Fallback: simple momentum screener when no factor data available.

        Always marks integrity_flags.FALLBACK_MOMENTUM — never silent.
        """
        result = self._simulate(
            trade_dates=trade_dates,
            dh=dh,
            model=None,
            factor_df=None,
            label=f"{self.strategy.name} (简易动量·FALLBACK)",
        )
        flags = dict(result.integrity_flags or {})
        flags["FALLBACK_MOMENTUM"] = True
        flags["TRUSTED"] = False
        reasons = list(flags.get("reasons") or [])
        reasons.append("FALLBACK_MOMENTUM")
        flags["reasons"] = list(dict.fromkeys(reasons))
        result.integrity_flags = flags
        return result

    # ── Utilities ───────────────────────────────────────────────────

    def _get_dh(self, as_of: str) -> PointInTimeDataHandler:
        return PointInTimeDataHandler(as_of)

    @staticmethod
    def _get_close_prices(symbols: list[str], date: str, dh: PointInTimeDataHandler) -> dict[str, float]:
        """Get close prices for symbols on a given date."""
        if not symbols:
            return {}
        import sqlite3
        conn = dh.conn
        from nous.data.storage.daily_bars import daily_table_for, daily_relation_sql

        placeholders = ",".join(["?"] * len(symbols))
        try:
            tbl = daily_table_for(date)
            rows = conn.execute(
                f"SELECT symbol, close FROM {tbl} WHERE symbol IN ({placeholders}) AND trade_date=?",
                (*symbols, date),
            ).fetchall()
        except Exception:
            rel = daily_relation_sql(date, date, conn=conn)
            rows = conn.execute(
                f"SELECT symbol, close FROM {rel} WHERE symbol IN ({placeholders}) AND trade_date=?",
                (*symbols, date),
            ).fetchall()
        return {r[0]: r[1] for r in rows if r[1] is not None and r[1] > 0}

    def close(self):
        if self._dh:
            self._dh.close()
            self._dh = None


# ── Convenience functions ──────────────────────────────────────────

def run_backtest(
    strategy: str,
    start: str = "2022-01-01",
    end: str = "2026-07-01",
    capital: float = 1_000_000,
    wf_folds: int = 5,
    market: str = "a",
) -> BacktestResult:
    """Quick backtest runner."""
    engine = BacktestEngine(
        strategy=strategy,
        start_date=start,
        end_date=end,
        initial_capital=capital,
        wf_folds=wf_folds,
        market=market,
        do_walk_forward=True,
    )
    try:
        return engine.run()
    finally:
        engine.close()


def compare_strategies(
    strategies: list[str] | None = None,
    start: str = "2022-01-01",
    end: str = "2026-07-01",
    capital: float = 1_000_000,
    wf_folds: int = 5,
) -> list[BacktestResult]:
    """Compare multiple strategies."""
    if strategies is None:
        strategies = list_strategies()
    
    results = []
    for name in strategies:
        logger.info(f"\n{'='*60}\nStrategy: {name}\n{'='*60}")
        try:
            result = run_backtest(name, start=start, end=end, capital=capital, wf_folds=wf_folds)
            results.append(result)
            logger.info(f"  {name}: return={result.total_return:+.2f}% sharpe={result.sharpe_ratio:.3f}")
        except Exception as e:
            logger.error(f"  {name}: FAILED - {e}")
    
    return results
