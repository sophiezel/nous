"""
Meta-Labeling — 二级置信度过滤
基于 Lopez de Prado (2018) AFML Chapter 3 的框架。

核心思想:
- 主模型预测方向(做多/做空)
- Meta Model 预测"主模型的预测是否正确"
- 两个任务解耦: 主模型优化召回率, Meta Model 优化精确率

用法:
    from nous.engine.ml.meta_labeling import MetaLabeler
    labeler = MetaLabeler()
    labeler.fit(X_train, y_train, primary_preds)
    filtered = labeler.filter(primary_preds, X_test, threshold=0.6)
"""

from __future__ import annotations

import logging
import json
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import lightgbm as lgb
import joblib

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "models"


class TripleBarrier:
    """三重障碍法生成 Meta Labels。

    定义"正确预测":
    - 做多预测 + 价格触及上轨(盈利目标) = 正确
    - 做多预测 + 价格触及下轨(止损) = 错误
    - 做空预测 + 价格触及下轨 = 正确
    - 做空预测 + 价格触及上轨 = 错误
    - 未触及任何障碍 = 按最终价格判断
    """

    @staticmethod
    def generate_labels(
        prices: pd.Series,
        horizon: int = 20,
        pt_factor: float = 2.0,
        sl_factor: float = 1.0,
    ) -> pd.Series:
        """为每行数据生成 Meta Label (1=正确, 0=错误)。

        Args:
            prices: 未来N日价格序列 (索引=原始数据行)
            horizon: 预测周期(交易日)
            pt_factor: 止盈倍数(ATR倍数)
            sl_factor: 止损倍数

        Returns:
            Series of 0/1 labels
        """
        labels = []
        returns = prices.pct_change(periods=horizon).shift(-horizon)

        for i in range(len(prices) - horizon):
            window = prices.iloc[i : i + horizon + 1]
            entry = window.iloc[0]
            upper = entry * (1 + 0.02 * pt_factor)
            lower = entry * (1 - 0.01 * sl_factor)

            hit_upper = (window > upper).any()
            hit_lower = (window < lower).any()

            if hit_upper and not hit_lower:
                labels.append(1)
            elif hit_lower and not hit_upper:
                labels.append(0)
            elif hit_upper and hit_lower:
                labels.append(1)
            else:
                final_ret = returns.iloc[i]
                labels.append(1 if final_ret > 0 else 0)

        labels.extend([0] * horizon)
        return pd.Series(labels, index=prices.index, name="meta_label")


class MetaLabeler:
    """Meta-Labeling 完整管线。

    使用 LightGBM 作为 Meta Model (轻量, 与主模型架构解耦)。
    """

    def __init__(self):
        self.model: lgb.LGBMClassifier | None = None
        self.threshold: float = 0.6
        self.fitted: bool = False
        self.feature_names: list[str] = []

    def fit(
        self,
        X: pd.DataFrame,
        y_primary: pd.Series,
        primary_preds: np.ndarray,
        meta_labels: pd.Series | None = None,
        close_prices: pd.Series | None = None,
        horizon: int = 20,
    ) -> dict:
        """训练 Meta Model。

        Args:
            X: 因子特征 (与主模型训练相同)
            y_primary: 主模型训练目标(forward returns)
            primary_preds: 主模型对训练集的预测值
            meta_labels: 预计算的 meta labels。若为None则用TripleBarrier生成
            close_prices: 用于TripleBarrier的收盘价序列
            horizon: 预测周期

        Returns:
            dict with training metrics
        """
        if meta_labels is None:
            if close_prices is None:
                raise ValueError("需要 meta_labels 或 close_prices")
            meta_labels = TripleBarrier.generate_labels(
                close_prices, horizon=horizon
            )
            logger.info(f"TripleBarrier生成meta labels: {meta_labels.sum()}/{len(meta_labels)} 正例")

        # 构建 Meta 特征: 原始因子 + 主模型预测值 + 预测残差
        meta_df = X.copy()
        meta_df["primary_pred"] = primary_preds
        meta_df["primary_pred_abs"] = np.abs(primary_preds)
        if y_primary is not None and len(y_primary) == len(primary_preds):
            meta_df["primary_residual"] = primary_preds - y_primary.values

        self.feature_names = list(meta_df.columns)

        # 对齐索引
        common_idx = meta_df.index.intersection(meta_labels.index)
        X_meta = meta_df.loc[common_idx]
        y_meta = meta_labels.loc[common_idx]

        valid = X_meta.notna().all(axis=1) & y_meta.notna()
        X_meta = X_meta[valid]
        y_meta = y_meta[valid]

        if len(X_meta) < 100:
            logger.warning(f"Meta training data too small: {len(X_meta)}")
            self.fitted = False
            return {"status": "failed", "reason": "too_few_samples", "n_samples": len(X_meta)}

        # 填缺值
        medians = X_meta.median()
        X_meta = X_meta.fillna(medians)

        # 标准化
        means = X_meta.mean()
        stds = X_meta.std().replace(0, 1)
        X_meta_s = (X_meta - means) / stds

        # 训练 LightGBM 分类器
        pos_weight = (len(y_meta) - y_meta.sum()) / max(y_meta.sum(), 1)
        pos_weight = min(pos_weight, 10.0)

        self.model = lgb.LGBMClassifier(
            n_estimators=100,
            max_depth=4,
            num_leaves=15,
            learning_rate=0.05,
            reg_alpha=1.0,
            reg_lambda=1.0,
            min_child_samples=50,
            scale_pos_weight=pos_weight,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=-1,
        )
        self.model.fit(X_meta_s, y_meta)

        # 评估
        y_prob = self.model.predict_proba(X_meta_s)[:, 1]

        from sklearn.metrics import roc_auc_score, precision_score, recall_score

        try:
            auc = roc_auc_score(y_meta, y_prob)
        except ValueError:
            auc = 0.5

        # 找最优阈值
        best_f1, best_thresh = 0, 0.5
        for t in np.arange(0.3, 0.9, 0.05):
            y_pred_t = (y_prob >= t).astype(int)
            if y_pred_t.sum() == 0:
                continue
            prec = precision_score(y_meta, y_pred_t, zero_division=0)
            rec = recall_score(y_meta, y_pred_t, zero_division=0)
            if prec + rec > 0:
                f1 = 2 * prec * rec / (prec + rec)
                if f1 > best_f1:
                    best_f1, best_thresh = f1, t

        self.threshold = float(best_thresh)
        self.fitted = True

        meta = {
            "status": "ok",
            "auc": round(auc, 4),
            "best_threshold": round(best_thresh, 2),
            "best_f1": round(best_f1, 4),
            "n_train": len(X_meta),
            "n_positive": int(y_meta.sum()),
            "pos_ratio": round(y_meta.sum() / len(y_meta), 3),
            "top_features": list(
                pd.DataFrame(
                    {"feature": self.feature_names, "importance": self.model.feature_importances_}
                )
                .sort_values("importance", ascending=False)
                .head(10)["feature"]
            ),
        }

        logger.info(f"Meta Model trained: AUC={auc:.4f} threshold={best_thresh:.2f} F1={best_f1:.4f}")
        return meta

    def filter(
        self,
        primary_preds: np.ndarray,
        X: pd.DataFrame,
        threshold: float | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """过滤低置信度预测。

        Args:
            primary_preds: 主模型预测值
            X: 特征DataFrame (与训练时相同列)
            threshold: 覆盖 self.threshold

        Returns:
            (filtered_preds, confidence_scores)
            - filtered_preds 中低置信度信号置为 NaN
            - confidence_scores 是 Meta Model 输出的概率
        """
        if not self.fitted or self.model is None:
            logger.warning("Meta Model not fitted, returning all predictions")
            return primary_preds, np.ones_like(primary_preds)

        if threshold is None:
            threshold = self.threshold

        # 构建特征
        meta_df = X.copy()
        meta_df["primary_pred"] = primary_preds
        meta_df["primary_pred_abs"] = np.abs(primary_preds)

        # 只保留训练时的列
        # 补充训练时有但预测时缺失的特征
        for col in self.feature_names:
            if col not in meta_df.columns:
                meta_df[col] = 0.0  # 预测时不可用的特征填0

        # 只保留训练时的特征集
        available = [c for c in self.feature_names if c in meta_df.columns]
        meta_df = meta_df[available].copy()

        # 填缺值+标准化
        medians = meta_df.median()
        meta_df = meta_df.fillna(medians)
        means = meta_df.mean()
        stds = meta_df.std().replace(0, 1)
        X_meta_s = (meta_df - means) / stds

        # 预测置信度
        probs = self.model.predict_proba(X_meta_s)[:, 1]

        filtered = primary_preds.copy().astype(float)
        filtered[probs < threshold] = np.nan

        n_total = len(primary_preds)
        n_kept = (~np.isnan(filtered)).sum()
        logger.info(f"Meta Label过滤: {n_kept}/{n_total} 保留 ({n_kept/n_total*100:.1f}%)")

        return filtered, probs

    def save(self, name: str = "meta_labeler"):
        """保存 Meta Model。"""
        if not self.fitted:
            raise RuntimeError("模型未训练")
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        path = MODEL_DIR / f"{name}_{date.today().isoformat()}.pkl"
        joblib.dump(
            {
                "model": self.model,
                "threshold": self.threshold,
                "feature_names": self.feature_names,
            },
            str(path),
        )
        logger.info(f"Meta Model saved: {path}")
        return str(path)

    def load(self, path: str):
        """加载 Meta Model。"""
        data = joblib.load(path)
        self.model = data["model"]
        self.threshold = data["threshold"]
        self.feature_names = data["feature_names"]
        self.fitted = True
        logger.info(f"Meta Model loaded: threshold={self.threshold}")


# ─── CLI ───

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    import argparse

    parser = argparse.ArgumentParser(description="Meta-Labeling 训练与过滤")
    parser.add_argument("--mode", choices=["train", "filter"], default="train")
    parser.add_argument("--model-path", type=str, help="已训练的meta model路径 (filter模式)")
    parser.add_argument("--horizon", type=int, default=20, help="预测周期")
    parser.add_argument("--threshold", type=float, default=None, help="置信度阈值 (覆盖自动选择)")
    args = parser.parse_args()

    if args.mode == "train":
        from nous.core.paths import factor_dir
        factor_path = factor_dir() / "latest.parquet"
        if not factor_path.exists():
            print(f"因子快照不存在: {factor_path}")
            exit(1)

        df = pd.read_parquet(factor_path)
        factor_names = [c for c in df.columns if c.startswith("K")]
        print(f"加载因子: {len(df)}行, {len(factor_names)}因子")

        X = df[factor_names]
        y = df["close"].copy()

        # 用简单策略生成primary_preds: 正向动量=做多
        if "K1_ret_5d" in df.columns:
            primary_preds = df["K1_ret_5d"].fillna(0).values
        else:
            primary_preds = np.zeros(len(df))

        labeler = MetaLabeler()
        meta = labeler.fit(X, y, primary_preds, close_prices=y, horizon=args.horizon)
        print(json.dumps(meta, indent=2, ensure_ascii=False))

        if meta["status"] == "ok":
            path = labeler.save()
            print(f"Model saved: {path}")

    elif args.mode == "filter":
        if not args.model_path:
            print("filter模式需要 --model-path")
            exit(1)
        labeler = MetaLabeler()
        labeler.load(args.model_path)
        print(f"Loaded: threshold={labeler.threshold}")
