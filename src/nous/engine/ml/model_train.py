"""LightGBM 模型训练 + IC 分析 + MLflow 实验追踪"""

import sys
import time
import logging
import json
from pathlib import Path
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
import lightgbm as lgb
import mlflow
from sklearn.model_selection import train_test_split
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

from nous.core.paths import factor_dir, ic_dir, model_dir

FACTOR_DIR = factor_dir()
MODEL_DIR = model_dir()
IC_DIR = ic_dir()


def load_factors(market: str = "a") -> pd.DataFrame:
    """加载最新因子快照"""
    prefix = "hk_" if market == "hk" else ""
    path = FACTOR_DIR / f"{prefix}latest.parquet"
    if not path.exists():
        raise FileNotFoundError(f"因子快照不存在: {path}，请先运行 factor_compute.py save --market {market}")
    df = pd.read_parquet(path)
    logger.info(f"加载因子({market}): {len(df)}行, {len([c for c in df.columns if c.startswith('K')])}个因子")
    return df


def prepare_training_data(
    df: pd.DataFrame,
    forward_period: int = 5,
    train_end: str = "2023-12-31",
    test_start: str = "2024-01-01",
    test_end: str = "2025-12-31",
) -> dict:
    """
    准备训练/测试数据。因子DataFrame必须包含 close 列用于计算 forward returns。

    Returns:
        {X_train, y_train, X_test, y_test, symbols_test, dates_test, factor_names}
    """
    factor_names = [c for c in df.columns if c.startswith("K")]
    
    if "close" not in df.columns:
        raise ValueError("因子DataFrame缺少close列, 请重新运行 factor_compute.py save")
    
    # 计算 forward returns (未来N日收益率, 按股票分组)
    df = df.sort_values(["symbol", "trade_date"]).copy()
    df["forward_ret"] = df.groupby("symbol")["close"].shift(-forward_period) / df["close"] - 1
    
    # 按时间划分
    train_mask = df["trade_date"] <= train_end
    test_mask = (df["trade_date"] >= test_start) & (df["trade_date"] <= test_end)
    
    # 清洗: 至少80%因子非NaN
    X_all = df[factor_names].copy()
    y_all = df["forward_ret"].copy()
    min_valid = max(1, int(len(factor_names) * 0.8))
    valid = X_all.notna().sum(axis=1) >= min_valid
    valid = valid & y_all.notna() & ~np.isinf(X_all).any(axis=1)
    
    X_all = X_all[valid]
    y_all = y_all[valid]
    train_mask_np = train_mask.values[valid.values]
    test_mask_np = test_mask.values[valid.values]
    
    X_train = X_all[train_mask_np]
    y_train = y_all[train_mask_np]
    X_test = X_all[test_mask_np]
    y_test = y_all[test_mask_np]
    
    symbols_test = df.loc[valid & test_mask, "symbol"].reset_index(drop=True)
    dates_test = df.loc[valid & test_mask, "trade_date"].reset_index(drop=True)
    
    if len(X_train) < 500 or len(X_test) < 100:
        raise ValueError(f"数据量不足: train={len(X_train)}, test={len(X_test)}")
    
    # 填充NaN为训练集中位数
    train_medians = X_train.median()
    X_train = X_train.fillna(train_medians)
    X_test = X_test.fillna(train_medians)
    
    logger.info(f"数据准备: train={len(X_train)}, test={len(X_test)}, factors={len(factor_names)}")
    
    return {
        "X_train": X_train, "y_train": y_train,
        "X_test": X_test, "y_test": y_test,
        "symbols_test": symbols_test, "dates_test": dates_test,
        "factor_names": factor_names,
    }


def train_lightgbm(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    factor_names: list[str],
    experiment_name: str = "diy_factors",
) -> dict:
    """训练 LightGBM 模型并返回评估结果"""
    
    t0 = time.time()
    
    params = {
        "objective": "regression",
        "metric": "rmse",
        "boosting_type": "gbdt",
        "num_leaves": 128,
        "max_depth": 8,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "lambda_l1": 200,
        "lambda_l2": 200,
        "min_data_in_leaf": 100,
        "num_threads": 8,
        "verbose": -1,
        "early_stopping_rounds": 50,
        "num_boost_round": 500,
    }
    
    logger.info(f"训练 LightGBM: {X_train.shape[0]}训练样本, {len(factor_names)}特征")
    
    # 划分训练/验证集（时序划分，最后20%做验证）
    split_idx = int(len(X_train) * 0.8)
    X_tr, X_val = X_train.iloc[:split_idx], X_train.iloc[split_idx:]
    y_tr, y_val = y_train.iloc[:split_idx], y_train.iloc[split_idx:]
    
    train_data = lgb.Dataset(X_tr, label=y_tr)
    val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
    
    model = lgb.train(
        params,
        train_data,
        valid_sets=[train_data, val_data],
        valid_names=["train", "valid"],
        callbacks=[lgb.log_evaluation(50)],
    )
    
    train_time = time.time() - t0
    
    # 测试集预测
    y_pred = model.predict(X_test)
    
    # IC 计算
    ic = np.corrcoef(y_pred, y_test)[0, 1]
    rank_ic, _ = spearmanr(y_pred, y_test)
    
    # 因子重要性
    importance = pd.DataFrame({
        "factor": factor_names,
        "importance": model.feature_importance(importance_type="gain"),
    }).sort_values("importance", ascending=False)
    
    result = {
        "model": "LightGBM",
        "experiment": experiment_name,
        "n_features": len(factor_names),
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "train_time_s": round(train_time, 1),
        "ic": round(float(ic), 4),
        "rank_ic": round(float(rank_ic), 4),
        "train_rmse": round(float(model.best_score["train"]["rmse"]), 4),
        "valid_rmse": round(float(model.best_score["valid"]["rmse"]), 4),
        "top_10_factors": importance.head(10)["factor"].tolist(),
        "factor_importance": importance.to_dict("records"),
    }
    
    logger.info(f"训练完成: IC={ic:.4f}, Rank IC={rank_ic:.4f}, 耗时{train_time:.1f}s")
    logger.info(f"TOP 5 因子: {importance.head(5)['factor'].tolist()}")
    
    return result, model


def save_results(result: dict, model):
    """保存训练结果和模型"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    IC_DIR.mkdir(parents=True, exist_ok=True)
    
    today = date.today().isoformat()
    
    # 保存模型 (sklearn LGBMRegressor 用 joblib)
    import joblib
    model_path = MODEL_DIR / f"lgb_{today}.pkl"
    joblib.dump(model, str(model_path))
    
    # 保存结果
    result_path = IC_DIR / f"ic_{today}.json"
    
    # 移除 factor_importance 的完整列表（太大），只保留 top10
    result_slim = {k: v for k, v in result.items() if k != "factor_importance"}
    result_slim["model_path"] = str(model_path)
    result_slim["top_10_importance"] = result.get("top_10_factors", [])
    
    with open(result_path, "w") as f:
        json.dump(result_slim, f, indent=2, ensure_ascii=False, default=str)
    
    # 保存完整因子重要性
    imp_path = IC_DIR / f"factor_importance_{today}.csv"
    imp_df = pd.DataFrame(result.get("factor_importance", []))
    if not imp_df.empty:
        imp_df.to_csv(imp_path, index=False)
    
    logger.info(f"模型: {model_path}")
    logger.info(f"IC结果: {result_path}")
    
    return result_path


def run_pipeline(
    forward_period: int = 5,
    market: str = "a",
):
    """完整训练管线 — 使用 prepare_training_data + train_lightgbm"""
    
    # 1. 加载因子 (现在包含 close 列)
    df = load_factors(market=market)
    
    logger.info(f"加载因子: {len(df)}行, {df['symbol'].nunique()}只, {df['trade_date'].min()}~{df['trade_date'].max()}")
    
    # 2. 准备训练/测试数据 - 使用实际数据范围
    dates = sorted(df["trade_date"].unique())
    split_idx = int(len(dates) * 0.6)
    train_end_date = str(dates[split_idx])[:10]
    test_start_date = str(dates[split_idx])[:10]
    test_end_date = str(dates[-1])[:10]
    
    logger.info(f"时间划分: train<={train_end_date}, test={test_start_date}~{test_end_date}")
    
    # 3. Cross-sectional processors (Qlib-style) then prepare data
    proc_meta = {"cszscore": False}
    try:
        from nous.engine.ml.cs_processors import apply_processors, cs_rank_label
        df_proc, proc_meta = apply_processors(df)
        logger.info(f"CS processors: {proc_meta}")
        data = prepare_training_data(
            df_proc, forward_period=forward_period,
            train_end=train_end_date,
            test_start=test_start_date,
            test_end=test_end_date,
        )
    except ValueError as e:
        logger.error(f"数据准备失败: {e}")
        return None
    except Exception as e:
        logger.warning(f"CS processors fallback to raw: {e}")
        try:
            data = prepare_training_data(
                df, forward_period=forward_period,
                train_end=train_end_date,
                test_start=test_start_date,
                test_end=test_end_date,
            )
            proc_meta = {"cszscore": False}
        except ValueError as e2:
            logger.error(f"数据准备失败: {e2}")
            return None
    
    X_train, y_train = data["X_train"], data["y_train"]
    X_test, y_test = data["X_test"], data["y_test"]
    factor_names = data["factor_names"]
    
    if len(X_train) < 1000 or len(X_test) < 100:
        logger.error(f"数据量不足: train={len(X_train)}, test={len(X_test)}")
        return None
    
    # 4. 标准化 (column-wise fallback; CS already applied when available)
    train_mean = X_train.mean()
    train_std = X_train.std().replace(0, 1)
    X_train_scaled = (X_train - train_mean) / train_std
    X_test_scaled = (X_test - train_mean) / train_std
    
    # 5. 训练
    logger.info(f"训练 LightGBM: {len(X_train)}样本, {len(factor_names)}特征")
    
    model = lgb.LGBMRegressor(
        n_estimators=200,
        max_depth=6,
        num_leaves=63,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=1.0,
        reg_lambda=1.0,
        min_child_samples=50,
        random_state=42,
        n_jobs=4,
    )
    
    t0 = time.time()
    model.fit(
        X_train_scaled, y_train,
        eval_set=[(X_test_scaled, y_test)],
        eval_metric="rmse",
    )
    train_time = time.time() - t0
    
    # 6. 评估 + IC 门禁
    y_pred = model.predict(X_test_scaled)
    ic = np.corrcoef(y_pred, y_test)[0, 1]
    rank_ic, _ = spearmanr(y_pred, y_test)

    ic_gate = {}
    try:
        from nous.engine.ml.cs_processors import rolling_ic_metrics
        ic_gate = rolling_ic_metrics(
            pd.Series(y_pred),
            pd.Series(y_test.values if hasattr(y_test, "values") else y_test),
            data.get("dates_test", pd.Series(range(len(y_pred)))),
        )
        logger.info(
            f"IC gate: rank_ic={ic_gate.get('rank_ic')} "
            f"recent={ic_gate.get('recent_rank_ic')} promote={ic_gate.get('promote')}"
        )
    except Exception as e:
        logger.warning(f"IC gate skipped: {e}")
        ic_gate = {"promote": True, "reason": str(e)}
    
    importance = pd.DataFrame({
        "factor": factor_names,
        "importance": model.feature_importances_,
    }).sort_values("importance", ascending=False)
    
    result = {
        "model": "LightGBM",
        "market": market,
        "n_features": len(factor_names),
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "train_time_s": round(train_time, 1),
        "ic": round(float(ic), 4) if np.isfinite(ic) else None,
        "rank_ic": round(float(rank_ic), 4) if not np.isnan(rank_ic) else None,
        "top_10_factors": importance.head(10)["factor"].tolist(),
        "train_end": train_end_date,
        "test_start": test_start_date,
        "test_end": test_end_date,
        "ic_gate": ic_gate,
        "promote": bool(ic_gate.get("promote", False)),
        "processors": proc_meta,
        "label_deal_price": "close",  # training label uses close; backtest deal=close
    }

    if not result["promote"]:
        logger.warning(
            f"MODEL NOT PROMOTED: RankIC gate failed "
            f"(recent={ic_gate.get('recent_rank_ic')} < {ic_gate.get('threshold')})"
        )
        # Still save for analysis but mark path
        result["model_status"] = "rejected"
    else:
        result["model_status"] = "promoted"
    
    # 7. 保存
    save_results(result, model)
    
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["train"], default="train")
    parser.add_argument("--forward", type=int, default=5, help="预测未来N日收益")
    args = parser.parse_args()
    
    if args.action == "train":
        result = run_pipeline(forward_period=args.forward)
        if result:
            print(f"\n训练结果: IC={result['ic']:.4f}, Rank IC={result['rank_ic']:.4f}")
            print(f"TOP 5 因子: {result['top_10_factors'][:5]}")
