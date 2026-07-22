"""多维度筛选引擎：价值 + 趋势 + 量价 综合打分 — K1数据质量门禁"""
import logging
import sqlite3
from datetime import date

from nous.data import storage
from nous.engine.indicators import trend, value, volume
from nous.data.query_engine import get_multi_daily_df

logger = logging.getLogger(__name__)


def _compute_k1_penalty(v: dict, t: dict, vol: dict, dq_cfg: dict) -> float:
    """计算K1指标缺失惩罚系数。0=完全排除，1=全部真实。"""
    if not dq_cfg.get("enabled", False):
        return 1.0
    
    k1_missing_count = 0
    # 价值K1: PE/PB/ROE
    for k in ["pe", "pb", "roe"]:
        prov = v.get(f"{k}_provenance", "missing")
        if prov != "real":
            k1_missing_count += 1
    # 趋势K1: MA金叉/MACD金叉
    for k in v.get("k1_missing", []):  # no-op, trend K1 is binary
        pass
    if not t.get("k1_ready", False):
        k1_missing_count += 1
    # 量价K1: volume_ratio
    if not vol.get("k1_ready", False):
        k1_missing_count += 1
    
    table = dq_cfg.get("penalty_table", {0: 1.0, 1: 0.5, 2: 0.3, 3: 0.0})
    penalty = table.get(k1_missing_count, table.get(max(table.keys()), 0.0))
    return min(1.0, max(0.0, penalty))


def screen_single(symbol: str, name: str, market: str, cfg: dict, daily_df=None) -> dict:
    """
    对单只股票进行多维度打分 + K1数据质量门禁。
    v2.0: 同时计算短线/中线/长线三套分数。
    daily_df: 预加载的日线 DataFrame (可选, 避免逐只 DuckDB 查询)
    """
    v = value.value_scores(symbol, cfg)     # 价值
    t = trend.trend_scores(symbol, cfg, daily_df=daily_df)  # 趋势
    vol = volume.volume_scores(symbol, cfg, daily_df=daily_df)  # 量价

    # ── 质量检测 ──
    quality_warnings = []
    try:
        from nous.engine.indicators import quality
        q_warnings = quality.check_all(symbol, market)
        quality_warnings = [
            {"pattern": w.pattern, "description": w.description, "risk": w.risk.value}
            for w in q_warnings
        ]
    except Exception:
        pass

    # ── K1 数据质量门禁 ──
    dq_cfg = cfg.get("scoring", {}).get("data_quality", {"enabled": False})
    k1_penalty = _compute_k1_penalty(v, t, vol, dq_cfg)

    # 收集K1溯源信息
    k1_missing_all = list(v.get("k1_missing", []))
    if not t.get("k1_ready", False):
        k1_missing_all.extend(t.get("k1_missing", []))
    if not vol.get("k1_ready", False):
        k1_missing_all.extend(vol.get("k1_missing", []))
    k1_all_ready = v.get("k1_ready", False) and t.get("k1_ready", False) and vol.get("k1_ready", False)

    # ── 计算三套周期分数 ──
    w = cfg["scoring"]
    period_weights = w.get("period_weights", {})

    def _compute_period_score(pw: dict) -> float:
        """用指定周期的权重计算分数"""
        scores = []
        weights = []
        if v["available"]:
            vs = v["score"] * (k1_penalty if dq_cfg.get("enabled") else 1.0)
            scores.append(vs)
            weights.append(pw.get("value_weight", w["value_weight"]))
        if t["score"] is not None:
            scores.append(t["score"])
            weights.append(pw.get("trend_weight", w["trend_weight"]))
        if vol["score"] is not None:
            scores.append(vol["score"])
            weights.append(pw.get("volume_weight", w["volume_weight"]))
        total_w = sum(weights)
        if total_w == 0:
            return 0
        normalized = [wt / total_w for wt in weights]
        return round(sum(s * nw for s, nw in zip(scores, normalized)), 1)

    # 默认分数（向后兼容）
    final_score = _compute_period_score(w)

    # 周期分层分数
    scores_period = {}
    for period in ("short", "mid", "long"):
        if period in period_weights:
            scores_period[f"score_{period}"] = _compute_period_score(period_weights[period])

    return {
        "screen_date": date.today().isoformat(),
        "symbol": symbol,
        "name": name,
        "market": market,
        "score": final_score,
        **scores_period,  # score_short, score_mid, score_long
        "pe": v["pe"],
        "pb": v["pb"],
        "roe": v["roe"],
        "ma_cross": 1 if t["ma_cross"] else 0,
        "macd_signal": 1 if t.get("macd_golden") else 0,
        "volume_ratio": vol["volume_ratio"],
        "rsi": t.get("rsi"),
        "quality_warnings": quality_warnings,
        "data_quality": {
            "k1_ready": k1_all_ready,
            "k1_missing": k1_missing_all,
            "k1_penalty": k1_penalty,
            "pe_provenance": v.get("pe_provenance", "missing"),
            "pb_provenance": v.get("pb_provenance", "missing"),
            "roe_provenance": v.get("roe_provenance", "missing"),
        },
    }


def screen_all(market: str = "a", cfg: dict = None, save: bool = False) -> list[dict]:
    """
    对指定市场的所有股票进行筛选打分。
    market: 'a', 'hk', None=全市场
    返回: 按得分降序排列的结果列表
    """
    if cfg is None:
        from nous.core.config import load_config
        cfg = load_config()

    # ── 数据覆盖率门禁：拒绝在数据不足时运行全量筛选 ──
    if market in (None, "a"):
        try:
            from datetime import date as _date, timedelta as _timedelta
            from nous.data import storage as _storage
            _td = _date.today()
            # 回退到最近交易日（跳过周末）
            for _ in range(5):
                _td -= _timedelta(days=1)
                if _td.weekday() < 5:
                    break
            _db = _storage.get_db(write=False)
            _universe = _db.execute("SELECT COUNT(*) FROM stock_basic WHERE market='a'").fetchone()[0]
            _coverage = _db.execute(
                "SELECT COUNT(DISTINCT symbol) FROM stock_daily WHERE trade_date >= ?",
                (_td - _timedelta(days=2),)
            ).fetchone()[0]
            _db.close()
            _pct = _coverage / _universe if _universe > 0 else 0
            if _pct < 0.80:
                raise RuntimeError(
                    f"stock_daily A股覆盖率仅{_pct:.0%}（{_coverage}/{_universe}），"
                    f"拒绝全量筛选——数据不足时回退缓存产出的是过期结果。"
                    f"请先运行 update_all_daily('a') 或 full_daily_update.py。"
                )
            logger.info("数据覆盖率门禁通过: A股 %.1f%% (%d/%d)", _pct*100, _coverage, _universe)
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("覆盖率门禁检查失败（非致命）: %s", e)

    # ── 市场状态自适应：盘前判断当前市场状态 ──
    regime_info = {"regime": "SIDEWAYS", "confidence": 0.0, "config": {}}
    regime_cfg = cfg.get("regime_adaptive", {"enabled": True})
    if regime_cfg.get("enabled", True):
        try:
            from nous.engine.ml.market_regime import predict_current_regime
            from nous.engine.ml.adaptive_weights import get_regime_config, apply_regime_to_screener

            regime_result = predict_current_regime()
            regime_info["regime"] = regime_result["regime"]
            regime_info["confidence"] = regime_result["confidence"]
            regime_info["config"] = get_regime_config(regime_result["regime"])
            regime_info["probabilities"] = regime_result.get("probabilities", {})

            # 注入到 scoring 权重
            regime_adjust = apply_regime_to_screener(regime_result["regime"])
            scoring = cfg.setdefault("scoring", {})
            prev_value = scoring.get("value_weight", 1.0)
            prev_trend = scoring.get("trend_weight", 1.0)
            prev_volume = scoring.get("volume_weight", 1.0)
            # 叠加调整
            scoring["value_weight"] = prev_value * regime_adjust.get("value_weight", 1.0)
            scoring["trend_weight"] = prev_trend * regime_adjust.get("trend_weight", 1.0)
            scoring["volume_weight"] = prev_volume * regime_adjust.get("volume_weight", 1.0)

            logger.info(
                "市场状态: %s (置信度 %.1f%%) — 评分权重已调整: "
                "value=%.2f trend=%.2f volume=%.2f 仓位上限=%.0f%%",
                regime_info["regime"],
                regime_info["confidence"] * 100,
                scoring["value_weight"],
                scoring["trend_weight"],
                scoring["volume_weight"],
                regime_info["config"].get("position_limit", 1.0) * 100,
            )
        except Exception as e:
            logger.warning("市场状态预测降级 (非致命): %s", e)

    # 加载停牌黑名单
    suspended = _load_suspended_set()

    # ── ML 模型增强：预计算全市场模型排序 ──
    ml_cfg = cfg.get("ml_model", {})
    model_ranks = {}  # {symbol: rank_pct}
    model_scores = {}  # {symbol: model_score_norm}
    if ml_cfg.get("enabled", False):
        try:
            logger.info("加载 ML 模型排序...")
            from pathlib import Path
            from nous.engine.ml.predict import get_model_ranks, get_all_stock_scores, load_latest_model
            from nous.engine.ml.factor_compute import compute_all_factors, save_factor_snapshot

            # 尝试从最新因子快照加载，如果没有则计算
            factor_path = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "factors" / "latest.parquet"
            if factor_path.exists():
                import pandas as pd
                factors_df = pd.read_parquet(factor_path)
            else:
                logger.info("计算全市场因子...")
                factors_df = compute_all_factors()
                save_factor_snapshot(factors_df)

            model = load_latest_model()
            model_ranks = get_model_ranks(factors_df, model)
            model_scores = get_all_stock_scores(factors_df, model)
            logger.info(f"模型排序已加载: {len(model_ranks)} 只股票")
        except Exception as e:
            logger.warning(f"ML 模型增强降级 (非致命): {e}")

    rank_boost_top10 = ml_cfg.get("rank_boost_top10", 2.0)
    rank_boost_top30 = ml_cfg.get("rank_boost_top30", 1.0)

    stocks = storage.list_symbols(market if market != "all" else None)
    results = []
    total = len(stocks)
    skipped_suspended = 0
    skipped_st = 0

    # ── 预热: 批量预加载全部日线数据（避免 trend/volume 逐只 DuckDB 查询）──
    daily_cache = {}
    try:
        from nous.data.query_engine import get_multi_daily_df
        symbols_all = [s["symbol"] for s in stocks
                       if s["symbol"] not in suspended and not _is_st(s.get("name", ""))]
        logger.info("批量预加载日线: %d 只...", len(symbols_all))
        df_all = get_multi_daily_df(symbols_all, days=120)
        if not df_all.empty:
            for sym, grp in df_all.groupby("symbol"):
                daily_cache[sym] = grp.sort_values("trade_date")
        logger.info(f"日线缓存: {len(daily_cache)} 只")
    except Exception as e:
        logger.warning("日线预加载失败 (非致命): %s", e)

    # ── 预热: 批量预加载基本面 + Soul L2 过滤 ──
    financial_cache = {}
    soul_filtered = set()
    try:
        logger.info("批量预加载基本面...")
        conn = storage.get_db(write=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM stock_fundamental").fetchall()
        conn.close()
        for row in rows:
            financial_cache[row["symbol"]] = dict(row)
        logger.info(f"基本面缓存: {len(financial_cache)} 只")

        from nous.engine.soul_engine import qiuguolu_hard_filter
        for sym, fin in financial_cache.items():
            if fin:
                q = qiuguolu_hard_filter(fin)
                if q and not q.passed:
                    soul_filtered.add(sym)
        logger.info(f"Soul L2 过滤: {len(soul_filtered)} 只不通过")
    except Exception as e:
        logger.warning("基本面预加载失败 (非致命): %s", e)

    for i, s in enumerate(stocks):
        sym = s["symbol"]

        # K0: 交易状态检测
        if sym in suspended:
            skipped_suspended += 1
            continue
        if _is_st(s["name"]):
            skipped_st += 1
            continue

        # Soul L2: 邱国鹭四不碰硬过滤（用预加载缓存）
        if sym in soul_filtered:
            continue

        try:
            r = screen_single(sym, s["name"], s["market"], cfg,
                            daily_df=daily_cache.get(sym))

            # ── ML 模型排序加分 ──
            rank_pct = model_ranks.get(sym)
            if rank_pct is not None:
                if rank_pct >= 90:  # TOP 10%
                    ml_boost = rank_boost_top10
                elif rank_pct >= 70:  # TOP 30%
                    ml_boost = rank_boost_top30
                else:
                    ml_boost = 0.0
                r["ml_boost"] = ml_boost
                r["model_score_norm"] = round(float(model_scores.get(sym, 0)), 1)
                r["ml_rank_pct"] = round(float(rank_pct), 1)
                r["score"] = round(r.get("score", 0) + ml_boost, 1)

            results.append(r)
        except Exception as e:
            pass  # 跳过数据不足的股票

    results.sort(key=lambda x: x["score"], reverse=True)

    if save and results:
        storage.save_screen_results(results)

    # ── 结果数量异常告警 ──
    n = len(results)
    if n == 0:
        logger.error(
            "筛选结果为空！universe=%d, skipped_suspended=%d, skipped_st=%d, market=%s — "
            "请检查数据源、覆盖率门禁或筛选条件",
            total, skipped_suspended, skipped_st, market,
        )
    elif n < 10 and market in ("a", None):
        logger.warning(
            "筛选结果偏少: %d 只 (market=%s, universe=%d, skipped_suspended=%d, skipped_st=%d) — "
            "可能过滤条件过于严格或数据覆盖不足",
            n, market, total, skipped_suspended, skipped_st,
        )

    return results


def _load_suspended_set() -> set:
    """加载停牌黑名单"""
    import json
    from pathlib import Path
    path = Path.home() / "wiki" / "finance" / "raw" / "suspended_stocks.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return set(data.get("stocks", {}).keys())
    except Exception:
        return set()


def _is_st(name: str) -> bool:
    """检测是否为ST/退市股"""
    return "ST" in name or "退" in name


def enrich_candidates(candidates: list[dict], cfg: dict) -> list[dict]:
    """
    第二阶段：对趋势初筛候选股拉取基本面，重新完整打分。
    每只股票最多一次新浪财务API调用。
    """
    from nous.data.collectors.fetchers.finance import sync_fundamentals
    import time

    enriched = []
    for i, c in enumerate(candidates):
        sym = c["symbol"]
        try:
            sync_fundamentals(sym)
            r = screen_single(sym, c["name"], c["market"], cfg)
            enriched.append(r)
        except Exception:
            enriched.append(c)  # 失败则保留原结果

        if (i + 1) % 10 == 0:
            time.sleep(0.3)  # 温和限速

    return enriched
