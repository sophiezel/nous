"""Week 4: Optuna 超参自动搜索 (LightGBM/XGBoost/CatBoost)

对 3 个 GBDT 模型分别用 TPE sampler 搜索最优超参数，
结果保存为 JSON 供后续训练复用。

Usage:
    # 快速验证 (10 trials, 200 只股票)
    PYTHONPATH=. python -c "
    from nous.engine.ml.hyperopt_search import search_all_models
    search_all_models(limit=200, n_trials=10)
    "

    # 正式搜索 (50 trials)
    PYTHONPATH=. python -c "
    from nous.engine.ml.hyperopt_search import search_all_models
    search_all_models(limit=300, n_trials=50)
    "
"""

from __future__ import annotations

import sys
import json
import logging
import warnings
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Optuna 导入 (优雅降级)
# ──────────────────────────────────────────────

try:
    import optuna
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False
    logger.warning("optuna 未安装. 运行 'pip install optuna' 来启用超参搜索")

# ──────────────────────────────────────────────
# 路径
# ──────────────────────────────────────────────

HYP_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "hyperopt"
HYP_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────
# 数据准备 (复用 model_ensemble)
# ──────────────────────────────────────────────


def _get_data(limit=300, forward_period=5):
    """加载数据并返回 (X_train, y_train, X_test, y_test, factor_names).

    复用 model_ensemble._prepare_data 的输出格式.
    """
    from nous.engine.ml.model_ensemble import _prepare_data
    data = _prepare_data(forward_period=forward_period, limit=limit)
    if data is None:
        raise RuntimeError("数据准备失败, 无法进行超参搜索")
    return (
        data["X_train"].values,
        data["y_train"].values,
        data["X_test"].values,
        data["y_test"].values,
        data["factor_names"],
    )


# ──────────────────────────────────────────────
# Objective 函数 (每个模型各自独立)
# ──────────────────────────────────────────────


def objective_lgb(trial, X_train, y_train, X_val, y_val):
    """LightGBM 超参搜索 objective: 最大化 Rank IC."""
    import lightgbm as lgb

    params = {
        "num_leaves": trial.suggest_int("num_leaves", 15, 255),
        "max_depth": trial.suggest_int("max_depth", 3, 12),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
        "lambda_l1": trial.suggest_float("lambda_l1", 1e-8, 10.0, log=True),
        "lambda_l2": trial.suggest_float("lambda_l2", 1e-8, 10.0, log=True),
        "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
    }
    # 额外参数: subsample (bagging)
    if trial.suggest_categorical("use_subsample", [True, False]):
        params["subsample"] = trial.suggest_float("subsample", 0.5, 1.0)
        params["subsample_freq"] = trial.suggest_int("subsample_freq", 1, 10)
    else:
        params["subsample"] = 1.0
        params["subsample_freq"] = 0

    model = lgb.LGBMRegressor(
        **params,
        n_estimators=100,
        random_state=42,
        n_jobs=4,
        verbose=-1,
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_val)
    ic, _ = spearmanr(y_pred, y_val)
    return float(ic) if not np.isnan(ic) else 0.0


def objective_xgb(trial, X_train, y_train, X_val, y_val):
    """XGBoost 超参搜索 objective: 最大化 Rank IC."""
    import xgboost as xgb

    params = {
        "max_depth": trial.suggest_int("max_depth", 3, 12),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
        "min_child_weight": trial.suggest_float("min_child_weight", 1, 10, log=False),
        "gamma": trial.suggest_float("gamma", 1e-8, 1.0, log=True),
    }
    model = xgb.XGBRegressor(
        **params,
        n_estimators=100,
        random_state=42,
        n_jobs=4,
        verbosity=0,
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_val)
    ic, _ = spearmanr(y_pred, y_val)
    return float(ic) if not np.isnan(ic) else 0.0


def objective_cat(trial, X_train, y_train, X_val, y_val):
    """CatBoost 超参搜索 objective: 最大化 Rank IC."""
    from catboost import CatBoostRegressor

    params = {
        "depth": trial.suggest_int("depth", 3, 10),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1e-8, 10.0, log=True),
        "random_strength": trial.suggest_float("random_strength", 1e-8, 10.0, log=True),
        "bagging_temperature": trial.suggest_float("bagging_temperature", 0.0, 1.0),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 1, 50),
    }
    # CatBoost 用 grow_policy 做更灵活的分裂
    grow_policy = trial.suggest_categorical("grow_policy", ["SymmetricTree", "Depthwise", "Lossguide"])
    params["grow_policy"] = grow_policy
    if grow_policy == "Lossguide":
        params["max_leaves"] = trial.suggest_int("max_leaves", 15, 255)

    model = CatBoostRegressor(
        **params,
        iterations=100,
        random_seed=42,
        thread_count=4,
        verbose=False,
        allow_writing_files=False,
    )
    model.fit(X_train, y_train, eval_set=(X_val, y_val), verbose=False)
    y_pred = model.predict(X_val)
    ic, _ = spearmanr(y_pred, y_val)
    return float(ic) if not np.isnan(ic) else 0.0


# ──────────────────────────────────────────────
# 单模型搜索
# ──────────────────────────────────────────────


def search_model(
    name: str,
    objective_fn,
    X_train, y_train, X_val, y_val,
    n_trials: int = 50,
    show_progress_bar: bool = True,
) -> dict:
    """对单个模型运行 Optuna 超参搜索.

    Returns:
        {"best_params": dict, "best_ic": float, "n_trials": int,
         "study_name": str, "n_trials_completed": int}
    """
    study_name = f"{name}_search_{date.today().isoformat()}"

    # 尝试带进度条, 如果不支持则降级
    try:
        study = optuna.create_study(
            direction="maximize",
            study_name=study_name,
            sampler=optuna.samplers.TPESampler(seed=42),
        )
        study.optimize(
            lambda trial: objective_fn(trial, X_train, y_train, X_val, y_val),
            n_trials=n_trials,
            show_progress_bar=show_progress_bar,
        )
    except Exception:
        # 降级: 无进度条
        logger.info(f"进度条不可用, 降级到静默模式")
        study = optuna.create_study(
            direction="maximize",
            study_name=study_name,
            sampler=optuna.samplers.TPESampler(seed=42),
        )
        study.optimize(
            lambda trial: objective_fn(trial, X_train, y_train, X_val, y_val),
            n_trials=n_trials,
            show_progress_bar=False,
        )

    result = {
        "best_params": study.best_params,
        "best_ic": round(float(study.best_value), 6),
        "n_trials": n_trials,
        "n_trials_completed": len(study.trials),
        "study_name": study_name,
        "search_date": date.today().isoformat(),
    }
    return result


# ──────────────────────────────────────────────
# 全模型搜索入口
# ──────────────────────────────────────────────


def search_all_models(limit=300, n_trials=50, show_progress_bar=True):
    """对 3 个 GBDT 模型分别搜索最优参数, 保存 JSON.

    Args:
        limit: 限制股票数量 (0=全量, 推荐 200-300 用于调试)
        n_trials: 每个模型搜索轮数 (默认 50, 快速验证用 10)
        show_progress_bar: 是否显示进度条 (后台运行设为 False)

    Returns:
        {model_name: {best_params, best_ic, ...}}
    """
    if not HAS_OPTUNA:
        print("❌ optuna 未安装. 运行: pip install optuna")
        return {}

    # 获取数据
    print(f"\n{'='*50}")
    print(f"  Optuna 超参自动搜索")
    print(f"  限制股票: {limit}, 每模型 trials: {n_trials}")
    print(f"{'='*50}")
    print("  加载数据...")
    X_train, y_train, X_test, y_test, factor_names = _get_data(limit=limit)
    print(f"  数据: {len(X_train)} 训练样本, {len(X_test)} 验证样本, {len(factor_names)} 因子")

    # 定义要搜索的模型
    models_to_search = [
        ("lightgbm", objective_lgb, "LightGBM"),
        ("xgboost", objective_xgb, "XGBoost"),
        ("catboost", objective_cat, "CatBoost"),
    ]

    results = {}
    all_success = True

    for key, objective_fn, display_name in models_to_search:
        print(f"\n{'─'*50}")
        print(f"  搜索 {display_name} ({n_trials} trials)...")
        print(f"{'─'*50}")

        try:
            result = search_model(
                key, objective_fn,
                X_train, y_train, X_test, y_test,
                n_trials=n_trials,
                show_progress_bar=show_progress_bar,
            )
            results[key] = result

            # 保存 JSON
            out_path = HYP_DIR / f"best_params_{key}_{date.today().isoformat()}.json"
            with open(out_path, "w") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            logger.info(f"  最优参数已保存: {out_path}")

            print(f"  ✅ {display_name}: Best IC={result['best_ic']:.4f}")
            print(f"     Best params: {result['best_params']}")

        except Exception as e:
            logger.error(f"  ❌ {display_name} 搜索失败: {e}", exc_info=True)
            results[key] = {"error": str(e)}
            all_success = False

    # 汇总
    print(f"\n{'='*50}")
    print(f"  搜索完成")
    print(f"{'='*50}")
    for key, r in results.items():
        if "best_ic" in r:
            print(f"  {key:<12s} IC={r['best_ic']:.4f}  n_trials={r['n_trials_completed']}")
        else:
            print(f"  {key:<12s} ❌ {r.get('error', 'unknown error')}")
    print(f"{'='*50}")

    return results


# ──────────────────────────────────────────────
# 加载最优参数 (供 model_ensemble 调用)
# ──────────────────────────────────────────────


def load_best_params(model_name: str) -> dict | None:
    """加载最近一次搜索得到的最优参数.

    Args:
        model_name: "lightgbm", "xgboost", 或 "catboost"

    Returns:
        dict of best params, 或 None (如果找不到)
    """
    pattern = f"best_params_{model_name}_*.json"
    files = sorted(HYP_DIR.glob(pattern))
    if not files:
        logger.info(f"  未找到 {model_name} 的超参搜索结果 (data/hyperopt/ 为空)")
        return None
    # 取最新的文件
    latest = files[-1]
    try:
        with open(latest) as f:
            data = json.load(f)
        params = data.get("best_params")
        if params:
            logger.info(f"  加载 {model_name} 最优参数 (IC={data.get('best_ic', 'N/A')})")
            return params
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"  解析 {latest} 失败: {e}")
    return None


# ──────────────────────────────────────────────
# 搜索所有模型并返回合并参数 (供 train_all_models 调用)
# ──────────────────────────────────────────────


def get_all_optimized_params() -> dict:
    """一次性加载所有 3 个 GBDT 模型的最优参数.

    Returns:
        {"lightgbm": {...} | None, "xgboost": {...} | None, "catboost": {...} | None}
    """
    return {
        "lightgbm": load_best_params("lightgbm"),
        "xgboost": load_best_params("xgboost"),
        "catboost": load_best_params("catboost"),
    }


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    import argparse
    parser = argparse.ArgumentParser(description="Optuna 超参自动搜索")
    parser.add_argument("--limit", type=int, default=300, help="限制股票数量 (0=全量)")
    parser.add_argument("--trials", type=int, default=50, help="每模型搜索轮数")
    parser.add_argument("--no-progress", action="store_true", help="不显示进度条")
    args = parser.parse_args()

    results = search_all_models(
        limit=args.limit,
        n_trials=args.trials,
        show_progress_bar=not args.no_progress,
    )
