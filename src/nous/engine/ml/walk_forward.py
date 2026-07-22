"""
Walk-Forward 多区段训练管线
基于 Purged Walk-Forward Cross-Validation，划分训练/验证/测试区段。

业界标准实践:
- 每个训练窗口覆盖 >=1 个完整市场周期
- 训练/测试间 embargo=10交易日 防前视偏差
- 支持短线(forward=5日)和长线(forward=20日)两种策略
"""

import time
import logging
import json
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
import lightgbm as lgb
import joblib
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

FACTOR_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "factors"
MODEL_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "models"
IC_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "ic_analysis"

@dataclass
class TimeSplit:
    split_id: str
    market: str
    strategy: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    embargo_days: int = 10
    forward_period: int = 5

@dataclass 
class SplitResult:
    split: TimeSplit
    ic: float
    rank_ic: float
    train_samples: int
    test_samples: int
    n_features: int
    train_time_s: float
    model_path: str
    top_factors: list[str]
    success: bool = True
    error: str = ""


def generate_splits(market: str = "a", n_splits: int = 3, 
                    min_train_months: int = 6,
                    random_seed: int = 42,
                    strategy: str = "short",
                    forward_period: int = 5,
                    factor_df: pd.DataFrame = None) -> list[TimeSplit]:
    """生成时间区段。自动检测因子数据实际范围，避免硬编码窗口。
    如果factor_df传入则从中提取实际日期范围。"""
    rng = np.random.RandomState(random_seed)
    
    if strategy == "long":
        min_train_months = max(min_train_months, 9)
    
    # 从因子数据自动检测实际范围
    if factor_df is not None and len(factor_df) > 0:
        dates = pd.to_datetime(factor_df["trade_date"])
        actual_start = dates.min()
        actual_end = dates.max()
    elif market == "a":
        actual_start = datetime(2025, 5, 1)
        actual_end = datetime.today().replace(day=1)
    else:
        actual_start = datetime(2026, 2, 1)
        actual_end = datetime.today().replace(day=1)
    
    data_start = actual_start
    data_end = actual_end
    total_days = (data_end - data_start).days
    
    logger.info(f"generate_splits({market}/{strategy}): 数据范围={data_start.date()}~{data_end.date()} ({total_days}d), "
                f"请求{n_splits}split, min_train={min_train_months}月")
    
    # 自动缩减split数: 每个split至少需要 train_min + embargo + test_min
    min_split_days = min_train_months * 30 + 10 + 60  # train + embargo + test
    max_feasible = max(1, total_days // min_split_days)
    n_splits = min(n_splits, max_feasible)
    logger.info(f"实际可行split数: {n_splits} (请求{n_splits if n_splits != max_feasible else n_splits})")
    
    if market == "a":
        splits = []
        segment = total_days // (n_splits + 1)
        
        for i in range(n_splits):
            offset = rng.randint(-segment//3, segment//3) if i > 0 else 0
            train_end = data_start + timedelta(days=segment * (i+1) + offset)
            train_start = data_start + timedelta(days=max(0, segment * i - segment//3))
            
            if (train_end - train_start).days < min_train_months * 30:
                train_start = train_end - timedelta(days=int(min_train_months * 30))
            
            embargo_end = train_end + timedelta(days=10)
            test_window = 60 if strategy == "short" else 120
            test_end = min(train_end + timedelta(days=test_window), data_end + timedelta(days=18))
            
            # 最后的split允许train_start推前以利用更多数据
            if i == n_splits - 1 and (train_end - train_start).days < min_train_months * 30:
                train_start = data_start
            
            split_id = f"{market}_{strategy}_s{i+1}"
            splits.append(TimeSplit(
                split_id=split_id, market=market, strategy=strategy,
                train_start=train_start.strftime("%Y-%m-%d"),
                train_end=train_end.strftime("%Y-%m-%d"),
                test_start=embargo_end.strftime("%Y-%m-%d"),
                test_end=test_end.strftime("%Y-%m-%d"),
                embargo_days=10, forward_period=forward_period,
            ))
    
    elif market == "hk":
        # 港股数据可能很短——用更保守的scheme
        splits = []
        if total_days < 180:
            # 少于6个月数据: 单个split，训练=前80%数据，测试=最后20%
            split_point = data_start + timedelta(days=int(total_days * 0.8))
            embargo_end = split_point + timedelta(days=10)
            split_id = f"{market}_{strategy}_s1"
            splits.append(TimeSplit(
                split_id=split_id, market=market, strategy=strategy,
                train_start=data_start.strftime("%Y-%m-%d"),
                train_end=split_point.strftime("%Y-%m-%d"),
                test_start=embargo_end.strftime("%Y-%m-%d"),
                test_end=data_end.strftime("%Y-%m-%d"),
                embargo_days=10, forward_period=forward_period,
            ))
        else:
            segment_days = total_days // (n_splits + 1)
            for i in range(n_splits):
                offset = rng.randint(-min(30, segment_days//3), min(30, segment_days//3)) if i > 0 else 0
                train_end = data_start + timedelta(days=segment_days * (i+1) + offset)
                train_start = data_start + timedelta(days=max(0, segment_days * i - segment_days//3 + offset))
                
                if (train_end - train_start).days < min_train_months * 30:
                    train_start = train_end - timedelta(days=int(min_train_months * 30))
                
                embargo_end = train_end + timedelta(days=10)
                test_window = 60 if strategy == "short" else 90
                test_end = min(train_end + timedelta(days=test_window), data_end)
                
                split_id = f"{market}_{strategy}_s{i+1}"
                splits.append(TimeSplit(
                    split_id=split_id, market=market, strategy=strategy,
                    train_start=train_start.strftime("%Y-%m-%d"),
                    train_end=train_end.strftime("%Y-%m-%d"),
                    test_start=embargo_end.strftime("%Y-%m-%d"),
                    test_end=test_end.strftime("%Y-%m-%d"),
                    embargo_days=10, forward_period=forward_period,
                ))
    else:
        raise ValueError(f"不支持的市场: {market}")
    
    return splits


def train_on_split(factor_df: pd.DataFrame, split: TimeSplit) -> SplitResult:
    """在单个时间区段上训练 LightGBM。"""
    t0 = time.time()
    
    try:
        df = factor_df.copy()
        train_mask = (df["trade_date"] >= split.train_start) & (df["trade_date"] <= split.train_end)
        test_mask = (df["trade_date"] >= split.test_start) & (df["trade_date"] <= split.test_end)
        
        df_train = df[train_mask].copy()
        df_test = df[test_mask].copy()
        
        if len(df_train) < 1000 or len(df_test) < 100:
            return SplitResult(split=split, ic=0, rank_ic=0, train_samples=len(df_train),
                             test_samples=len(df_test), n_features=0, train_time_s=0,
                             model_path="", top_factors=[], success=False,
                             error=f"数据不足: train={len(df_train)}, test={len(df_test)}")
        
        factor_names = [c for c in df.columns if c.startswith("K")]
        fp = split.forward_period
        
        df_train = df_train.sort_values(["symbol", "trade_date"])
        df_train["forward_ret"] = df_train.groupby("symbol")["close"].shift(-fp) / df_train["close"] - 1
        
        df_test = df_test.sort_values(["symbol", "trade_date"])
        df_test["forward_ret"] = df_test.groupby("symbol")["close"].shift(-fp) / df_test["close"] - 1
        
        X_train = df_train[factor_names].copy()
        y_train = df_train["forward_ret"].copy()
        X_test = df_test[factor_names].copy()
        y_test = df_test["forward_ret"].copy()
        
        min_valid = max(1, int(len(factor_names) * 0.7))
        
        valid_train = X_train.notna().sum(axis=1) >= min_valid
        valid_train = valid_train & y_train.notna()
        X_train = X_train[valid_train]
        y_train = y_train[valid_train]
        
        valid_test = X_test.notna().sum(axis=1) >= min_valid
        valid_test = valid_test & y_test.notna()
        X_test = X_test[valid_test]
        y_test = y_test[valid_test]
        
        if len(X_train) < 500 or len(X_test) < 50:
            return SplitResult(split=split, ic=0, rank_ic=0, train_samples=len(X_train),
                             test_samples=len(X_test), n_features=len(factor_names), train_time_s=0,
                             model_path="", top_factors=[], success=False,
                             error=f"清洗后数据不足: train={len(X_train)}, test={len(X_test)}")
        
        train_medians = X_train.median()
        X_train = X_train.fillna(train_medians)
        X_test = X_test.fillna(train_medians)
        
        train_mean = X_train.mean()
        train_std = X_train.std().replace(0, 1)
        X_train_s = (X_train - train_mean) / train_std
        X_test_s = (X_test - train_mean) / train_std
        
        # 按场景差异化超参: (market, strategy)
        PARAM_MATRIX = {
            ("a", "short"):  {"n_estimators":150, "max_depth":5, "num_leaves":31,  "learning_rate":0.05, "reg_alpha":2.0, "reg_lambda":2.0, "min_child_samples":80},
            ("a", "long"):   {"n_estimators":250, "max_depth":7, "num_leaves":127, "learning_rate":0.02, "reg_alpha":1.0, "reg_lambda":1.0, "min_child_samples":30},
            ("hk", "short"): {"n_estimators":150, "max_depth":5, "num_leaves":31,  "learning_rate":0.05, "reg_alpha":3.0, "reg_lambda":3.0, "min_child_samples":100},
            ("hk", "long"):  {"n_estimators":200, "max_depth":6, "num_leaves":63,  "learning_rate":0.03, "reg_alpha":1.5, "reg_lambda":1.5, "min_child_samples":50},
        }
        p = PARAM_MATRIX.get((split.market, split.strategy), PARAM_MATRIX[("a","short")])
        
        model = lgb.LGBMRegressor(
            n_estimators=p["n_estimators"], max_depth=p["max_depth"], num_leaves=p["num_leaves"],
            learning_rate=p["learning_rate"], subsample=0.8, colsample_bytree=0.8,
            reg_alpha=p["reg_alpha"], reg_lambda=p["reg_lambda"], min_child_samples=p["min_child_samples"],
            random_state=42, n_jobs=4, verbosity=-1,
        )
        model.fit(X_train_s, y_train, eval_set=[(X_test_s, y_test)])
        
        train_time = time.time() - t0
        
        y_pred = model.predict(X_test_s)
        ic = np.corrcoef(y_pred, y_test)[0, 1]
        rank_ic, _ = spearmanr(y_pred, y_test)
        
        importance = pd.DataFrame({
            "factor": factor_names,
            "importance": model.feature_importances_,
        }).sort_values("importance", ascending=False)
        
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        model_path = MODEL_DIR / f"lgb_{split.split_id}_{today}.pkl"
        joblib.dump(model, str(model_path))
        
        IC_DIR.mkdir(parents=True, exist_ok=True)
        result_path = IC_DIR / f"ic_{split.split_id}_{today}.json"
        result_dict = {
            "split_id": split.split_id, "market": split.market,
            "strategy": split.strategy, "forward_period": fp,
            "train_start": split.train_start, "train_end": split.train_end,
            "test_start": split.test_start, "test_end": split.test_end,
            "ic": round(float(ic), 4),
            "rank_ic": round(float(rank_ic), 4) if not np.isnan(rank_ic) else None,
            "train_samples": len(X_train), "test_samples": len(X_test),
            "n_features": len(factor_names), "train_time_s": round(train_time, 1),
            "model_path": str(model_path),
            "top_10_factors": importance.head(10)["factor"].tolist(),
        }
        with open(result_path, "w") as f:
            json.dump(result_dict, f, indent=2, ensure_ascii=False)
        
        logger.info(f"[{split.split_id}] IC={ic:.4f} RankIC={rank_ic:.4f} "
                   f"train={len(X_train)} test={len(X_test)} time={train_time:.1f}s")
        
        return SplitResult(
            split=split, ic=round(float(ic), 4),
            rank_ic=round(float(rank_ic), 4) if not np.isnan(rank_ic) else 0,
            train_samples=len(X_train), test_samples=len(X_test),
            n_features=len(factor_names), train_time_s=train_time,
            model_path=str(model_path),
            top_factors=importance.head(10)["factor"].tolist(),
            success=True, error="",
        )
        
    except Exception as e:
        logger.error(f"[{split.split_id}] 训练失败: {e}")
        return SplitResult(split=split, ic=0, rank_ic=0, train_samples=0, test_samples=0,
                          n_features=0, train_time_s=0, model_path="", top_factors=[],
                          success=False, error=str(e))


def run_walk_forward(market: str = "a", n_splits: int = 3,
                     forward_period: int = None,
                     strategy: str = "short",
                     random_seed: int = 42) -> list[SplitResult]:
    """完整 Walk-Forward: 加载因子→生成区段→逐区段训练→汇总"""
    if forward_period is None:
        forward_period = 5 if strategy == "short" else 20
    
    t0 = time.time()
    
    prefix = "hk_" if market == "hk" else ""
    factor_path = FACTOR_DIR / f"{prefix}latest.parquet"
    if not factor_path.exists():
        raise FileNotFoundError(f"因子快照不存在: {factor_path}")
    
    df = pd.read_parquet(factor_path)
    logger.info(f"加载因子({market}): {len(df)}行 {df['symbol'].nunique()}只 "
               f"{df['trade_date'].min()}~{df['trade_date'].max()}")
    
    splits = generate_splits(market=market, n_splits=n_splits,
                            random_seed=random_seed, strategy=strategy,
                            forward_period=forward_period, factor_df=df)
    
    logger.info(f"生成 {len(splits)} 个训练区段:")
    for s in splits:
        logger.info(f"  {s.split_id}: train={s.train_start}~{s.train_end} "
                   f"test={s.test_start}~{s.test_end} forward={s.forward_period}d")
    
    results = []
    for split in splits:
        result = train_on_split(df, split)
        results.append(result)
    
    total_time = time.time() - t0
    success = [r for r in results if r.success]
    logger.info(f"Walk-Forward完成: {len(success)}/{len(results)} 成功, 总耗时{total_time:.1f}s")
    
    if success:
        avg_ic = np.mean([r.ic for r in success])
        avg_rank_ic = np.mean([r.rank_ic for r in success])
        logger.info(f"平均IC={avg_ic:.4f}, 平均RankIC={avg_rank_ic:.4f}")
    
    today = date.today().isoformat()
    summary_path = IC_DIR / f"walk_forward_{market}_{strategy}_{today}.json"
    summary = {
        "market": market, "strategy": strategy,
        "forward_period": forward_period,
        "n_splits": n_splits, "total_time_s": round(total_time, 1),
        "n_success": len(success), "n_failed": len(results) - len(success),
        "avg_ic": round(float(np.mean([r.ic for r in success])), 4) if success else None,
        "avg_rank_ic": round(float(np.mean([r.rank_ic for r in success])), 4) if success else None,
        "splits": [asdict(r) for r in results],
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"汇总报告: {summary_path}")
    
    return results


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["a", "hk"], default="a")
    parser.add_argument("--splits", type=int, default=3)
    parser.add_argument("--strategy", choices=["short", "long"], default="short",
                       help="短线(5日) vs 长线(20日)")
    parser.add_argument("--forward", type=int, default=None,
                       help="覆盖strategy默认forward_period")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    
    results = run_walk_forward(
        market=args.market, n_splits=args.splits,
        strategy=args.strategy, forward_period=args.forward,
        random_seed=args.seed,
    )
    
    print(f"\n{'='*60}")
    print(f"Walk-Forward 结果 ({args.market}/{args.strategy}):")
    for r in results:
        status = "OK" if r.success else f"FAIL: {r.error}"
        print(f"  {r.split.split_id}: IC={r.ic:.4f} RankIC={r.rank_ic:.4f} "
              f"train={r.train_samples} test={r.test_samples} [{status}]")
