"""6模型集成训练: LightGBM + XGBoost + CatBoost + Ridge + MLP + VotingRegressor

每个模型独立 MLflow run (nested under a parent run), 最后集成报告。
自动检查 data/hyperopt/ 下是否存在 Optuna 搜索到的最优参数并加载。

Usage:
    python -c "from nous.engine.ml.model_ensemble import run_ensemble_pipeline; run_ensemble_pipeline(limit=200)"
"""
from __future__ import annotations

import sys
import time
import json
import logging
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import mlflow
import joblib
import lightgbm as lgb
import xgboost as xgb
from catboost import CatBoostRegressor
from sklearn.linear_model import Ridge
from sklearn.neural_network import MLPRegressor
from sklearn.ensemble import VotingRegressor
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

# ── 超参加载 ──
HYP_DIR = data_dir() / "hyperopt"

def _load_optimized_params(model_name: str) -> dict | None:
    """加载 Optuna 搜索到的最优参数 (如有).

    Args:
        model_name: "lightgbm", "xgboost", 或 "catboost"

    Returns:
        dict 或 None (未找到)
    """
    files = sorted(HYP_DIR.glob(f"best_params_{model_name}_*.json"))
    if not files:
        return None
    latest = files[-1]
    try:
        with open(latest) as f:
            data = json.load(f)
        params = data.get("best_params")
        if params:
            logger.info(f"  加载 Optuna 最优参数 [{model_name}]: IC={data.get('best_ic', 'N/A')}")
            return params
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"  解析 {latest} 失败: {e}")
    return None

from nous.core.paths import data_dir, factor_dir, model_dir, screener_db

FACTOR_DIR = factor_dir()
MODEL_DIR = model_dir()
IC_DIR = factor_dir().parent / "ic_analysis"
DB_PATH = screener_db()


# ──────────────────────────────────────────────
# 数据准备（复用 model_train.py 的逻辑）
# ──────────────────────────────────────────────


def _prepare_data(forward_period: int = 5, limit: int = 0) -> dict | None:
    """加载因子 → 合并 close → 计算 forward returns → 划分 → 清洗 → 标准化

    Returns:
        {X_train, y_train, X_test, y_test, factor_names, scaler_info} 或 None
    """
    # 1. 加载因子快照
    factor_path = FACTOR_DIR / "latest.parquet"
    if not factor_path.exists():
        logger.error(f"因子快照不存在: {factor_path}")
        return None
    df = pd.read_parquet(factor_path)
    logger.info(f"加载因子: {len(df)}行, {len([c for c in df.columns if c.startswith('K')])}个因子")

    # 2. 验证必要列存在（因子数据自带 OHLCV）
    required = ["close", "symbol", "trade_date"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.error(f"因子快照缺少必要列: {missing}")
        return None
    if "close" not in df.columns:
        # 兜底: 极端情况下因子数据没有 close
        logger.error("因子快照无 close 列，无法计算 forward returns")
        return None

    # 3. 计算 forward returns
    df = df.sort_values(["symbol", "trade_date"])
    df["forward_ret"] = df.groupby("symbol")["close"].shift(-forward_period) / df["close"] - 1

    # 可选: limit 限制股票数量 (取前 N 只股票)
    if limit > 0:
        symbols = sorted(df["symbol"].unique())[:limit]
        df = df[df["symbol"].isin(symbols)]
        logger.info(f"limit={limit}: 保留 {len(df)} 行 ({len(symbols)} 只股票)")

    # 4. 按时间划分 (前60%训练，后40%测试)
    dates = sorted(df["trade_date"].unique())
    split_idx = int(len(dates) * 0.6)
    train_end_date = dates[split_idx]
    test_start_date = dates[split_idx]

    train_mask = df["trade_date"] <= train_end_date
    test_mask = df["trade_date"] >= test_start_date

    factor_names = [c for c in df.columns if c.startswith("K")]

    # 5. 清洗
    X_all = df[factor_names].copy()
    y_all = df["forward_ret"].copy()
    # 至少50%的因子非NaN才保留 (放宽条件, 避免早期数据被K1_ret_60d等长周期因子过滤掉)
    min_valid = int(len(factor_names) * 0.5)
    valid = X_all.notna().sum(axis=1) >= min_valid
    valid = valid & y_all.notna() & ~np.isinf(X_all).any(axis=1)
    X_all = X_all[valid]
    y_all = y_all[valid]
    train_mask_np = train_mask.values[valid.values] if hasattr(valid, 'values') else train_mask[valid]
    test_mask_np = test_mask.values[valid.values] if hasattr(valid, 'values') else test_mask[valid]

    X_train = X_all[train_mask_np]
    y_train = y_all[train_mask_np]
    X_test = X_all[test_mask_np]
    y_test = y_all[test_mask_np]

    logger.info(f"训练集: {len(X_train)}样本, 测试集: {len(X_test)}样本")

    if len(X_train) < 100 or len(X_test) < 20:
        logger.error("数据量不足, 无法训练")
        return None

    # 6. 填充 NaN → 训练集中位数
    train_medians = X_train.median()
    X_train = X_train.fillna(train_medians)
    X_test = X_test.fillna(train_medians)

    # 7. Z-score 标准化
    train_mean = X_train.mean()
    train_std = X_train.std().replace(0, 1)
    X_train_scaled = (X_train - train_mean) / train_std
    X_test_scaled = (X_test - train_mean) / train_std

    return {
        "X_train": X_train_scaled,
        "y_train": y_train,
        "X_test": X_test_scaled,
        "y_test": y_test,
        "factor_names": factor_names,
        "scaler_info": {"mean": train_mean, "std": train_std},
        "n_stocks": df["symbol"].nunique(),
    }


# ──────────────────────────────────────────────
# 各模型训练函数
# ──────────────────────────────────────────────


def _train_lightgbm(
    X_train, y_train, X_test, y_test,
    factor_names: list[str],
    optimized_params: dict | None = None,
) -> tuple:
    """训练 LightGBM 并返回 (model, ic, rank_ic, params)

    如果 optimized_params 提供, 合并到默认参数中。
    """
    t0 = time.time()
    default_params = {
        "n_estimators": 100, "max_depth": 5, "num_leaves": 31,
        "learning_rate": 0.05, "subsample": 0.8, "colsample_bytree": 0.8,
        "feature_fraction": 0.8,
        "reg_alpha": 1.0, "reg_lambda": 1.0,
        "min_child_samples": 20,
        "random_state": 42, "n_jobs": 4, "verbose": -1,
    }
    if optimized_params:
        # Optuna 参数覆盖默认, 同时保留 n_estimators/random_state/n_jobs 等固定参数
        merged = {**default_params, **optimized_params}
        merged["random_state"] = 42
        merged["n_jobs"] = 4
        merged["verbose"] = -1
        logger.info(f"  LightGBM 使用 Optuna 参数 (IC={optimized_params.get('_best_ic', 'N/A')})")
    else:
        merged = default_params

    model = lgb.LGBMRegressor(**merged)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        eval_metric="rmse",
    )
    train_time = time.time() - t0
    y_pred = model.predict(X_test)
    ic = float(np.corrcoef(y_pred, y_test)[0, 1])
    rank_ic_val, _ = spearmanr(y_pred, y_test)
    rank_ic_val = float(rank_ic_val) if not np.isnan(rank_ic_val) else 0.0

    params = {
        "n_estimators": merged.get("n_estimators", 100),
        "max_depth": merged.get("max_depth", 5),
        "num_leaves": merged.get("num_leaves", 31),
        "learning_rate": merged.get("learning_rate", 0.05),
        "subsample": merged.get("subsample", 0.8),
        "colsample_bytree": merged.get("colsample_bytree", 0.8),
        "reg_alpha": merged.get("reg_alpha", 1.0),
        "reg_lambda": merged.get("reg_lambda", 1.0),
        "n_jobs": 4,
        "optuna_optimized": optimized_params is not None,
    }

    logger.info(f"  LightGBM: IC={ic:.4f}, Rank IC={rank_ic_val:.4f}, {train_time:.1f}s")
    return model, ic, rank_ic_val, params, train_time


def _train_xgboost(
    X_train, y_train, X_test, y_test,
    factor_names: list[str],
    optimized_params: dict | None = None,
) -> tuple:
    t0 = time.time()
    default_params = {
        "n_estimators": 100, "max_depth": 5, "learning_rate": 0.05,
        "subsample": 0.8, "colsample_bytree": 0.8,
        "reg_alpha": 1.0, "reg_lambda": 1.0,
        "min_child_weight": 1, "gamma": 0.0,
        "random_state": 42, "n_jobs": 4, "verbosity": 0,
    }
    if optimized_params:
        merged = {**default_params, **optimized_params}
        merged["random_state"] = 42
        merged["n_jobs"] = 4
        merged["verbosity"] = 0
        logger.info(f"  XGBoost 使用 Optuna 参数 (IC={optimized_params.get('_best_ic', 'N/A')})")
    else:
        merged = default_params

    model = xgb.XGBRegressor(**merged)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )
    train_time = time.time() - t0
    y_pred = model.predict(X_test)
    ic = float(np.corrcoef(y_pred, y_test)[0, 1])
    rank_ic_val, _ = spearmanr(y_pred, y_test)
    rank_ic_val = float(rank_ic_val) if not np.isnan(rank_ic_val) else 0.0

    logger.info(f"  XGBoost:  IC={ic:.4f}, Rank IC={rank_ic_val:.4f}, {train_time:.1f}s")
    return model, ic, rank_ic_val, {
        "n_estimators": merged.get("n_estimators", 100),
        "max_depth": merged.get("max_depth", 5),
        "learning_rate": merged.get("learning_rate", 0.05),
        "subsample": merged.get("subsample", 0.8),
        "colsample_bytree": merged.get("colsample_bytree", 0.8),
        "reg_alpha": merged.get("reg_alpha", 1.0),
        "reg_lambda": merged.get("reg_lambda", 1.0),
        "n_jobs": 4,
        "optuna_optimized": optimized_params is not None,
    }, train_time


def _train_catboost(
    X_train, y_train, X_test, y_test,
    factor_names: list[str],
    optimized_params: dict | None = None,
) -> tuple:
    t0 = time.time()
    default_params = {
        "iterations": 100, "depth": 5, "learning_rate": 0.05,
        "l2_leaf_reg": 3.0,
        "random_seed": 42, "thread_count": 4,
        "verbose": False, "allow_writing_files": False,
    }
    if optimized_params:
        merged = {**default_params, **optimized_params}
        merged["random_seed"] = 42
        merged["thread_count"] = 4
        merged["verbose"] = False
        merged["allow_writing_files"] = False
        logger.info(f"  CatBoost 使用 Optuna 参数 (IC={optimized_params.get('_best_ic', 'N/A')})")
    else:
        merged = default_params

    model = CatBoostRegressor(**merged)
    model.fit(X_train, y_train, eval_set=(X_test, y_test), verbose=False)
    train_time = time.time() - t0
    y_pred = model.predict(X_test)
    ic = float(np.corrcoef(y_pred, y_test)[0, 1])
    rank_ic_val, _ = spearmanr(y_pred, y_test)
    rank_ic_val = float(rank_ic_val) if not np.isnan(rank_ic_val) else 0.0

    logger.info(f"  CatBoost: IC={ic:.4f}, Rank IC={rank_ic_val:.4f}, {train_time:.1f}s")
    return model, ic, rank_ic_val, {
        "iterations": merged.get("iterations", 100),
        "depth": merged.get("depth", 5),
        "learning_rate": merged.get("learning_rate", 0.05),
        "l2_leaf_reg": merged.get("l2_leaf_reg", 3.0),
        "thread_count": 4,
        "optuna_optimized": optimized_params is not None,
    }, train_time


def _train_ridge(
    X_train, y_train, X_test, y_test,
    factor_names: list[str],
) -> tuple:
    t0 = time.time()
    model = Ridge(alpha=1.0, random_state=42)
    model.fit(X_train, y_train)
    train_time = time.time() - t0
    y_pred = model.predict(X_test)
    ic = float(np.corrcoef(y_pred, y_test)[0, 1])
    rank_ic_val, _ = spearmanr(y_pred, y_test)
    rank_ic_val = float(rank_ic_val) if not np.isnan(rank_ic_val) else 0.0

    logger.info(f"  Ridge:    IC={ic:.4f}, Rank IC={rank_ic_val:.4f}, {train_time:.1f}s")
    return model, ic, rank_ic_val, {}, train_time


def _train_mlp(
    X_train, y_train, X_test, y_test,
    factor_names: list[str],
) -> tuple:
    t0 = time.time()
    model = MLPRegressor(
        hidden_layer_sizes=(64, 32, 16),
        activation="relu",
        alpha=0.001,
        max_iter=500,
        early_stopping=True,
        validation_fraction=0.15,
        random_state=42,
        verbose=False,
    )
    model.fit(X_train, y_train)
    train_time = time.time() - t0
    y_pred = model.predict(X_test)
    ic = float(np.corrcoef(y_pred, y_test)[0, 1])
    rank_ic_val, _ = spearmanr(y_pred, y_test)
    rank_ic_val = float(rank_ic_val) if not np.isnan(rank_ic_val) else 0.0

    logger.info(f"  MLP:      IC={ic:.4f}, Rank IC={rank_ic_val:.4f}, {train_time:.1f}s")
    return model, ic, rank_ic_val, {}, train_time


# ──────────────────────────────────────────────
# 训练全部 6 模型
# ──────────────────────────────────────────────


def train_all_models(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    factor_names: list[str],
    experiment_name: str = "diy_factors",
    optimized_params: dict | None = None,
) -> dict:
    """训练全部 6 个模型, 返回 {model_name: {model, ic, rank_ic, train_time_s, ...}}

    每个模型在一个嵌套的 MLflow run 中独立追踪。
    VotingRegressor 在子模型全部训练完后创建。

    Args:
        optimized_params: 可选, {model_name: params_dict}
            model_name 可以是 "lightgbm", "xgboost", "catboost"
            由 hyperopt_search 提供, 自动合并到默认参数。
    """
    mlflow.set_tracking_uri(f"file:{Path.home() / 'code' / 'stock-screener' / 'data' / 'mlruns'}")
    mlflow.set_experiment(experiment_name)

    results = {}
    today_str = date.today().isoformat()

    n_features = len(factor_names)

    # ── 加载 Optuna 最优参数 (如果未提供) ──
    if optimized_params is None:
        optimized_params = {}
        for model_name in ["lightgbm", "xgboost", "catboost"]:
            opt = _load_optimized_params(model_name)
            if opt:
                optimized_params[model_name] = opt

    lgb_opt = optimized_params.get("lightgbm")
    xgb_opt = optimized_params.get("xgboost")
    cat_opt = optimized_params.get("catboost")

    # ── 1. LightGBM ──
    with mlflow.start_run(run_name=f"lightgbm_{today_str}", nested=True):
        model, ic, rank_ic_val, params, train_time = _train_lightgbm(
            X_train, y_train, X_test, y_test, factor_names,
            optimized_params=lgb_opt,
        )
        mlflow.log_params({"n_features": n_features, **params})
        mlflow.log_metrics({
            "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
        })
        mlflow.sklearn.log_model(model, "lightgbm_model")
        results["lightgbm"] = {
            "model": model, "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
        }

    # ── 2. XGBoost ──
    with mlflow.start_run(run_name=f"xgboost_{today_str}", nested=True):
        model, ic, rank_ic_val, _, train_time = _train_xgboost(
            X_train, y_train, X_test, y_test, factor_names,
            optimized_params=xgb_opt,
        )
        mlflow.log_params({"n_features": n_features})
        mlflow.log_metrics({
            "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
        })
        mlflow.sklearn.log_model(model, "xgboost_model")
        results["xgboost"] = {
            "model": model, "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
        }

    # ── 3. CatBoost ──
    with mlflow.start_run(run_name=f"catboost_{today_str}", nested=True):
        model, ic, rank_ic_val, _, train_time = _train_catboost(
            X_train, y_train, X_test, y_test, factor_names,
            optimized_params=cat_opt,
        )
        mlflow.log_params({"n_features": n_features})
        mlflow.log_metrics({
            "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
        })
        mlflow.sklearn.log_model(model, "catboost_model")
        results["catboost"] = {
            "model": model, "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
        }

    # ── 4. Ridge ──
    with mlflow.start_run(run_name=f"ridge_{today_str}", nested=True):
        model, ic, rank_ic_val, _, train_time = _train_ridge(
            X_train, y_train, X_test, y_test, factor_names,
        )
        mlflow.log_params({"alpha": 1.0, "n_features": n_features})
        mlflow.log_metrics({
            "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
        })
        mlflow.sklearn.log_model(model, "ridge_model")
        results["ridge"] = {
            "model": model, "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
        }

    # ── 5. MLP ──
    with mlflow.start_run(run_name=f"mlp_{today_str}", nested=True):
        model, ic, rank_ic_val, _, train_time = _train_mlp(
            X_train, y_train, X_test, y_test, factor_names,
        )
        mlflow.log_params({
            "hidden_layer_sizes": "(64,32,16)", "activation": "relu",
            "alpha": 0.001, "max_iter": 500, "early_stopping": True,
            "n_features": n_features,
        })
        mlflow.log_metrics({
            "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
        })
        mlflow.sklearn.log_model(model, "mlp_model")
        results["mlp"] = {
            "model": model, "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
        }

    # ── 6. VotingRegressor (等权集成) ──
    with mlflow.start_run(run_name=f"voting_{today_str}", nested=True):
        t0 = time.time()
        estimators = [
            ("lgb", results["lightgbm"]["model"]),
            ("xgb", results["xgboost"]["model"]),
            ("cat", results["catboost"]["model"]),
            ("ridge", results["ridge"]["model"]),
            ("mlp", results["mlp"]["model"]),
        ]
        voting = VotingRegressor(estimators)
        voting.fit(X_train, y_train)
        train_time = time.time() - t0
        y_pred = voting.predict(X_test)
        ic = float(np.corrcoef(y_pred, y_test)[0, 1])
        rank_ic_val, _ = spearmanr(y_pred, y_test)
        rank_ic_val = float(rank_ic_val) if not np.isnan(rank_ic_val) else 0.0

        mlflow.log_params({"n_estimators": 5, "weights": "equal", "n_features": n_features})
        mlflow.log_metrics({
            "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
        })
        mlflow.sklearn.log_model(voting, "voting_model")
        results["voting"] = {
            "model": voting, "ic": ic, "rank_ic": rank_ic_val,
            "train_time_s": round(train_time, 1),
        }
        logger.info(f"  Voting:   IC={ic:.4f}, Rank IC={rank_ic_val:.4f}, {train_time:.1f}s")

    return results


# ──────────────────────────────────────────────
# 保存 & 报告
# ──────────────────────────────────────────────


def save_ensemble_results(results: dict, data_info: dict = None):
    """保存 VotingRegressor 模型 + 报告 JSON

    模型保存为 data/models/lgb_YYYY-MM-DD.pkl (兼容 predict.py 的 load_latest_model)
    报告保存为 data/ic_analysis/ensemble_YYYY-MM-DD.json
    """
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    IC_DIR.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()

    # 保存 VotingRegressor (兼容现有命名约定)
    model_path = MODEL_DIR / f"lgb_{today}.pkl"
    joblib.dump(results["voting"]["model"], str(model_path))
    logger.info(f"模型已保存: {model_path}")

    # 保存报告
    report = {
        "date": today,
        "model_counts": 6,
        "models": {},
        "best_model": max(results.items(), key=lambda x: x[1]["ic"])[0],
        "voting_model_path": str(model_path),
    }

    for name, r in results.items():
        report["models"][name] = {
            "ic": round(float(r["ic"]), 4),
            "rank_ic": round(float(r["rank_ic"]), 4),
            "train_time_s": r["train_time_s"],
        }

    if data_info:
        report["data_info"] = data_info

    report_path = IC_DIR / f"ensemble_{today}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"报告已保存: {report_path}")

    # 打印对比
    print(f"\n{'='*60}")
    print(f"  6模型集成训练对比 ({today})")
    print(f"{'='*60}")
    print(f"  {'Model':<12s} {'IC':>8s} {'Rank IC':>8s} {'Time(s)':>8s}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8}")
    for name in ["lightgbm", "xgboost", "catboost", "ridge", "mlp", "voting"]:
        r = results[name]
        print(f"  {name:<12s} {r['ic']:>8.4f} {r['rank_ic']:>8.4f} {r['train_time_s']:>7.1f}s")
    print(f"{'='*60}\n")

    return report_path


# ──────────────────────────────────────────────
# 集成入口
# ──────────────────────────────────────────────


def run_ensemble_pipeline(
    limit: int = 500,
    forward_period: int = 5,
    experiment_name: str = "diy_factors",
) -> dict | None:
    """完整集成训练管线: 因子 → 数据准备 → 6模型训练 → 对比报告 → 保存

    Args:
        limit: 限制股票数量 (0=全量, 推荐调试时设 200-500)
        forward_period: 预测未来 N 日收益率

    Returns:
        {model_name: {model, ic, rank_ic, train_time_s}} 或 None
    """
    print(f"\n{'='*60}")
    print(f"  6模型集成训练管线 (limit={limit}, forward={forward_period}d)")
    print(f"{'='*60}")

    # 1. 数据准备
    data = _prepare_data(forward_period=forward_period, limit=limit)
    if data is None:
        logger.error("数据准备失败")
        return None

    X_train, y_train = data["X_train"], data["y_train"]
    X_test, y_test = data["X_test"], data["y_test"]
    factor_names = data["factor_names"]

    print(f"\n  数据: {len(X_train)}训练, {len(X_test)}测试, {len(factor_names)}因子\n")

    # 2. 训练全部 6 个模型 (含 MLflow 追踪)
    results = train_all_models(
        X_train, y_train, X_test, y_test, factor_names,
        experiment_name=experiment_name,
    )

    # 3. 保存 VotingRegressor + 报告
    data_info = {
        "n_train": len(X_train),
        "n_test": len(X_test),
        "n_features": len(factor_names),
        "n_stocks": data.get("n_stocks", 0),
        "forward_period": forward_period,
        "limit": limit,
    }
    save_ensemble_results(results, data_info=data_info)

    return results


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    import argparse
    parser = argparse.ArgumentParser(description="6模型集成训练管线")
    parser.add_argument("--limit", type=int, default=500, help="限制股票数量 (0=全量)")
    parser.add_argument("--forward", type=int, default=5, help="预测未来N日收益率")
    parser.add_argument("--experiment", type=str, default="diy_factors", help="MLflow experiment name")
    args = parser.parse_args()

    results = run_ensemble_pipeline(
        limit=args.limit,
        forward_period=args.forward,
        experiment_name=args.experiment,
    )

    if results:
        print(f"\n✅ 集成训练完成")
        best = max(results.items(), key=lambda x: x[1]["ic"])
        print(f"   最佳模型: {best[0]} (IC={best[1]['ic']:.4f})")
    else:
        print(f"\n❌ 集成训练失败")
