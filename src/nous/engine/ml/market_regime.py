"""
市场状态分类全链路 — 标签生成 + 特征工程 + LightGBM 分类器 + 预测

数据来源: screener.db index_daily (沪深300)
状态分类: BULL / BEAR / SIDEWAYS / VOLATILE
"""

import logging
import json
import sqlite3
import math
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import joblib
from nous.core.paths import model_dir, screener_db

logger = logging.getLogger(__name__)

# ── 路径 ──────────────────────────────────────────────────────────
PROJ_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = screener_db()
MODEL_DIR = model_dir()
REGIME_DIR = model_dir().parent / "market_regime"
MODEL_PATH = MODEL_DIR / "regime_classifier.pkl"
REPORT_PATH = REGIME_DIR / "accuracy_report.json"
FEATURES_CSV = REGIME_DIR / "regime_training_data.csv"

REGIME_LABELS = {0: "SIDEWAYS", 1: "BULL", 2: "BEAR", 3: "VOLATILE"}
REGIME_TO_IDX = {v: k for k, v in REGIME_LABELS.items()}

# ── 辅助 ──────────────────────────────────────────────────────────


def _get_conn() -> sqlite3.Connection:
    """获取 screener.db 连接"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_dirs():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REGIME_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════
# Part A-1: 规则标注
# ══════════════════════════════════════════════════════════════════


def load_index_df(symbol: str = "IDX_000300") -> pd.DataFrame:
    """从 screener.db 加载指数日线，返回 sorted DataFrame"""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT trade_date, open, high, low, close, volume, amount
        FROM index_daily
        WHERE symbol = ?
        ORDER BY trade_date ASC
        """,
        (symbol,),
    ).fetchall()
    conn.close()

    if not rows:
        logger.warning("index_daily 无数据，尝试从 stock_daily 近似计算...")
        return _fallback_index_from_stock_daily(symbol)

    df = pd.DataFrame(
        [dict(r) for r in rows],
        columns=["trade_date", "open", "high", "low", "close", "volume", "amount"],
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df.sort_values("trade_date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    logger.info("Loaded %d rows from index_daily (%s ~ %s)", len(df),
                df["trade_date"].iloc[0].strftime("%Y-%m-%d"),
                df["trade_date"].iloc[-1].strftime("%Y-%m-%d"))
    return df


def _fallback_index_from_stock_daily(symbol: str) -> pd.DataFrame:
    """后备方案：从 stock_daily 计算全市场等权平均近似沪深300"""
    conn = _get_conn()
    # 取 A 股所有股票的日线, 每天计算收盘价中位数（近似沪深300）
    rows = conn.execute(
        """
        SELECT trade_date, close
        FROM stock_daily
        JOIN stock_basic USING(symbol)
        WHERE stock_basic.market = 'a'
          AND trade_date >= '2015-01-01'
        ORDER BY trade_date ASC
        """
    ).fetchall()
    conn.close()

    if not rows:
        raise ValueError("stock_daily 也无数据，无法计算指数近似")

    df = pd.DataFrame([dict(r) for r in rows])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    # 每日中位数收盘价
    daily_median = df.groupby("trade_date")["close"].median().reset_index()
    daily_median.rename(columns={"close": "close"}, inplace=True)
    daily_median["open"] = daily_median["close"]
    daily_median["high"] = daily_median["close"]
    daily_median["low"] = daily_median["close"]
    daily_median["volume"] = 0.0
    daily_median["amount"] = 0.0
    daily_median.sort_values("trade_date", inplace=True)
    daily_median.reset_index(drop=True, inplace=True)
    daily_median.attrs["from_fallback"] = True
    logger.info("Fallback: computed %d rows from stock_daily median price", len(daily_median))
    return daily_median


def label_regime(index_df: pd.DataFrame, confirm_days: int = 3) -> pd.DataFrame:
    """
    基于沪深300日线标注市场状态。

    规则:
      BULL:      20日涨 > 10% 且 波动率 < 30% (年化)
      BEAR:      20日涨 < -10%
      SIDEWAYS:  abs(20日涨) < 5% 且 波动率 < 20%
      VOLATILE:  波动率 > 35% (不管涨跌)
    其余归为 SIDEWAYS (默认)

    平滑: 连续 confirm_days 天同状态才确认切换。

    返回:
        DataFrame with [trade_date, regime, regime_code, ret_20d, vol_20d]
    """
    df = index_df.copy()
    if len(df) < 25:
        raise ValueError(f"数据不足，至少需要25行，当前{len(df)}行")

    closes = df["close"].values
    # ── 20日涨跌幅 ──
    ret_20d = np.full(len(df), np.nan)
    if len(df) > 20:
        ret_20d[20:] = closes[20:] / closes[:-20] - 1.0

    # ── 1日收益率 (用于波动率) ──
    ret_1d = np.full(len(df), np.nan)
    ret_1d[1:] = closes[1:] / closes[:-1] - 1.0

    # ── 20日年化波动率 ──
    vol_20d = np.full(len(df), np.nan)
    for i in range(20, len(df)):
        vol_20d[i] = np.std(ret_1d[i - 19: i + 1]) * math.sqrt(252)

    # ── 逐日标注原始标签 ──
    raw_codes = np.full(len(df), REGIME_TO_IDX["SIDEWAYS"], dtype=int)
    for i in range(20, len(df)):
        r = ret_20d[i]
        v = vol_20d[i]
        if np.isnan(r) or np.isnan(v):
            continue
        if r > 0.10 and v < 0.30:
            raw_codes[i] = REGIME_TO_IDX["BULL"]
        elif r < -0.10:
            raw_codes[i] = REGIME_TO_IDX["BEAR"]
        elif abs(r) < 0.05 and v < 0.20:
            raw_codes[i] = REGIME_TO_IDX["SIDEWAYS"]
        elif v > 0.35:
            raw_codes[i] = REGIME_TO_IDX["VOLATILE"]
        else:
            raw_codes[i] = REGIME_TO_IDX["SIDEWAYS"]

    # ── 平滑: 连续 confirm_days 天才确认切换 ──
    smoothed = raw_codes.copy()
    for i in range(confirm_days, len(df)):
        recent = raw_codes[i - confirm_days + 1: i + 1]
        if np.all(recent == recent[0]):
            smoothed[i] = recent[0]
        else:
            smoothed[i] = smoothed[i - 1] if i > confirm_days else recent[0]

    result = pd.DataFrame({
        "trade_date": df["trade_date"],
        "regime_code": smoothed,
        "regime": [REGIME_LABELS[c] for c in smoothed],
    })
    return result


# ══════════════════════════════════════════════════════════════════
# Part A-2: 特征工程
# ══════════════════════════════════════════════════════════════════


def compute_regime_features(
    index_df: pd.DataFrame,
    market_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    特征工程：每个交易日一行

    特征:
      ret_5d, ret_20d, ret_60d       — 指数涨跌幅
      vol_20d, vol_60d                — 波动率
      vol_ratio = vol_20d/vol_60d     — 波动率变化
      volume_trend                    — 成交量5日均值方向 (1=up, -1=down, 0=flat)
      advance_decline_ratio           — 全市场涨跌比 (需 market_df)
      new_high_ratio                  — 创60日新高占比 (需 market_df)
      turnover_ma5                    — 换手率5日均值 (暂用 volume 代替)

    返回:
        DataFrame with [trade_date, ret_5d, ret_20d, ret_60d, vol_20d, vol_60d,
                        vol_ratio, volume_trend, advance_decline_ratio, new_high_ratio, turnover_ma5]
    """
    df = index_df.copy()
    closes = df["close"].values
    volumes = df["volume"].values
    n = len(df)

    # ── 涨跌幅 ──
    ret_5d = np.full(n, np.nan)
    ret_20d = np.full(n, np.nan)
    ret_60d = np.full(n, np.nan)
    if n > 5:
        ret_5d[5:] = closes[5:] / closes[:-5] - 1.0
    if n > 20:
        ret_20d[20:] = closes[20:] / closes[:-20] - 1.0
    if n > 60:
        ret_60d[60:] = closes[60:] / closes[:-60] - 1.0

    # ── 1日收益率 ──
    ret_1d = np.full(n, np.nan)
    ret_1d[1:] = closes[1:] / closes[:-1] - 1.0

    # ── 波动率 ──
    vol_20d = np.full(n, np.nan)
    vol_60d = np.full(n, np.nan)
    for i in range(20, n):
        vol_20d[i] = np.std(ret_1d[i - 19: i + 1]) * math.sqrt(252)
    for i in range(60, n):
        vol_60d[i] = np.std(ret_1d[i - 59: i + 1]) * math.sqrt(252)

    # ── 波动率变化 ──
    vol_ratio = np.full(n, np.nan)
    mask = ~np.isnan(vol_20d) & ~np.isnan(vol_60d) & (vol_60d > 1e-8)
    vol_ratio[mask] = vol_20d[mask] / vol_60d[mask]

    # ── 成交量趋势 (5日均值方向) ──
    volume_trend = np.zeros(n, dtype=int)
    if n > 10:
        vol_ma5 = np.convolve(volumes, np.ones(5) / 5, mode="valid")
        # pad to align
        padded = np.full(n, np.nan)
        padded[4:] = vol_ma5
        for i in range(10, n):
            if padded[i] > padded[i - 5] * 1.05:
                volume_trend[i] = 1
            elif padded[i] < padded[i - 5] * 0.95:
                volume_trend[i] = -1

    # ── 全市场涨跌比 & 新高占比 (需要 stock_daily) ──
    advance_decline_ratio = np.full(n, np.nan)
    new_high_ratio = np.full(n, np.nan)

    if market_df is not None:
        adr, nhr = _compute_market_breadth(market_df, df["trade_date"])
        advance_decline_ratio = adr
        new_high_ratio = nhr
    else:
        # 尝试从 stock_daily 自动计算
        try:
            conn = _get_conn()
            market_raw = pd.read_sql_query(
                """
                SELECT trade_date, symbol, close
                FROM stock_daily
                WHERE trade_date >= (SELECT MIN(trade_date) FROM index_daily WHERE symbol='IDX_000300')
                  AND trade_date <= (SELECT MAX(trade_date) FROM index_daily WHERE symbol='IDX_000300')
                ORDER BY trade_date ASC
                """,
                conn,
                parse_dates=["trade_date"],
            )
            conn.close()
            if len(market_raw) > 0:
                adr, nhr = _compute_market_breadth(market_raw, df["trade_date"])
                advance_decline_ratio = adr
                new_high_ratio = nhr
        except Exception as e:
            logger.warning("全市场宽度计算失败: %s", e)

    # ── 换手率近似 (volume 替代) ──
    turnover_ma5 = np.full(n, np.nan)
    if n > 5:
        # 用成交量5日均值 / 成交量60日均值作为换手率变化指标
        vol_ma5_arr = np.full(n, np.nan)
        vol_ma5_arr[4:] = np.convolve(volumes, np.ones(5) / 5, mode="valid")
        vol_ma60_arr = np.full(n, np.nan)
        vol_ma60_arr[59:] = np.convolve(volumes, np.ones(60) / 60, mode="valid")
        mask_t = ~np.isnan(vol_ma5_arr) & ~np.isnan(vol_ma60_arr) & (vol_ma60_arr > 1e-8)
        turnover_ma5[mask_t] = vol_ma5_arr[mask_t] / vol_ma60_arr[mask_t]

    result = pd.DataFrame({
        "trade_date": df["trade_date"],
        "ret_5d": ret_5d,
        "ret_20d": ret_20d,
        "ret_60d": ret_60d,
        "vol_20d": vol_20d,
        "vol_60d": vol_60d,
        "vol_ratio": vol_ratio,
        "volume_trend": volume_trend,
        "advance_decline_ratio": advance_decline_ratio,
        "new_high_ratio": new_high_ratio,
        "turnover_ma5": turnover_ma5,
    })
    return result


def _compute_market_breadth(
    market_df: pd.DataFrame,
    index_dates: pd.Series,
) -> tuple[np.ndarray, np.ndarray]:
    """
    计算全市场宽度指标。

    返回:
        advance_decline_ratio, new_high_ratio (aligned to index_dates)
    """
    if "trade_date" not in market_df.columns:
        return np.full(len(index_dates), np.nan), np.full(len(index_dates), np.nan)

    # 按日期分组计算
    grouped = market_df.groupby("trade_date")
    date_to_adr = {}
    date_to_nhr = {}

    for dt, grp in grouped:
        closes_t = grp["close"].values
        if len(closes_t) < 2:
            continue
        advances = np.sum(closes_t > closes_t[0])  # 相比前一日上涨
        declines = np.sum(closes_t < closes_t[0])
        total = advances + declines
        adr = advances / total if total > 0 else 0.5
        date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)
        date_to_adr[date_str] = adr

    # 新高占比: 需要60日窗口，这里简化计算
    # 按股票分组再按日期展开
    if "symbol" in market_df.columns and len(market_df) > 1000:
        try:
            market_df_sorted = market_df.sort_values(["symbol", "trade_date"]).copy()
            # 用 rolling 60 天判断是否新高
            for sym, grp in market_df_sorted.groupby("symbol"):
                grp = grp.sort_values("trade_date")
                highs = grp["close"].rolling(60, min_periods=20).max()
                new_high_mask = grp["close"] >= highs
                for idx in grp.index[new_high_mask]:
                    dt_str = grp.loc[idx, "trade_date"]
                    if isinstance(dt_str, pd.Timestamp):
                        dt_str = dt_str.strftime("%Y-%m-%d")
                    date_to_nhr[dt_str] = date_to_nhr.get(dt_str, 0) + 1

            # 计算比例
            total_stocks = market_df_sorted["symbol"].nunique()
            for dt_str in date_to_nhr:
                date_to_nhr[dt_str] /= max(total_stocks, 1)
        except Exception:
            pass

    # 对齐到 index_dates
    adr_arr = np.full(len(index_dates), np.nan)
    nhr_arr = np.full(len(index_dates), np.nan)
    for i, dt in enumerate(index_dates):
        dt_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)
        if dt_str in date_to_adr:
            adr_arr[i] = date_to_adr[dt_str]
        if dt_str in date_to_nhr:
            nhr_arr[i] = date_to_nhr[dt_str]

    return adr_arr, nhr_arr


# ══════════════════════════════════════════════════════════════════
# Part A-3: LightGBM 分类器训练
# ══════════════════════════════════════════════════════════════════


def _prepare_training_data() -> tuple[pd.DataFrame, pd.Series]:
    """
    准备训练数据：合并特征 + 标签，剔除 NaN。
    返回: (X, y)
    """
    df = load_index_df()
    labels = label_regime(df)
    features = compute_regime_features(df)

    # 合并
    merged = pd.merge(labels, features, on="trade_date", how="inner")
    merged.sort_values("trade_date", inplace=True)

    # 只保留有特征+标签的行
    feat_cols = [
        "ret_5d", "ret_20d", "ret_60d",
        "vol_20d", "vol_60d", "vol_ratio",
        "volume_trend", "advance_decline_ratio",
        "new_high_ratio", "turnover_ma5",
    ]
    merged = merged.dropna(subset=feat_cols + ["regime_code"])
    merged = merged[merged["regime_code"].notna()]

    X = merged[feat_cols].copy()
    y = merged["regime_code"].astype(int)

    # 替换 remaining NaN
    X = X.fillna(0.0)
    X = X.replace([np.inf, -np.inf], 0.0)

    logger.info("Training data: %d samples, %d features", len(X), len(feat_cols))
    logger.info("Label distribution: %s", y.value_counts().to_dict())

    # 保存
    _ensure_dirs()
    merged.to_csv(FEATURES_CSV, index=False)
    logger.info("Saved training data to %s", FEATURES_CSV)

    return X, y, feat_cols


def train_regime_classifier():
    """
    训练 LightGBM 多分类器。

    训练集/测试集: 从 index_daily 数据分层抽样 (75/25)

    输出:
        data/models/regime_classifier.pkl
        data/market_regime/accuracy_report.json
    """
    import lightgbm as lgb
    from sklearn.metrics import accuracy_score, confusion_matrix
    from sklearn.model_selection import train_test_split

    X, y, feat_cols = _prepare_training_data()

    # ── 统计参与训练的标签 ──
    present_labels = sorted(y.unique())
    logger.info("Labels present in data: %s", {REGIME_LABELS.get(i, f"UNK_{i}"): int((y == i).sum()) for i in range(4)})

    # 如果某些标签缺失，调整 num_class
    num_classes = len(present_labels)
    label_to_idx = {orig: i for i, orig in enumerate(present_labels)}
    idx_to_label = {i: orig for i, orig in enumerate(present_labels)}

    y_mapped = y.map(label_to_idx)

    # ── 分层抽样 ──
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_mapped, test_size=0.25, random_state=42, stratify=y_mapped,
    )

    logger.info("Train: %d, Test: %d", len(X_train), len(X_test))
    logger.info("Train label distribution: %s", {REGIME_LABELS.get(idx_to_label.get(i, -1), f"CLS_{i}"): int((y_train == i).sum()) for i in range(num_classes)})

    # ── 处理样本不均衡 ──
    total = len(y_train)
    class_weights = {}
    for cls in range(num_classes):
        cnt = (y_train == cls).sum()
        class_weights[cls] = total / (num_classes * cnt) if cnt > 0 else 1.0

    logger.info("Class weights: %s", class_weights)

    # ── 训练 ──
    params = {
        "objective": "multiclass",
        "num_class": num_classes,
        "metric": "multi_logloss",
        "boosting_type": "gbdt",
        "num_leaves": 15,  # 减少过拟合
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
        "num_threads": 4,
        "min_data_in_leaf": 5,
        "lambda_l1": 0.1,
        "lambda_l2": 0.1,
    }

    sample_weight = np.array([class_weights.get(c, 1.0) for c in y_train.values])

    train_data = lgb.Dataset(X_train, label=y_train.values, weight=sample_weight)
    valid_data = lgb.Dataset(X_test, label=y_test.values, reference=train_data)

    model = lgb.train(
        params,
        train_data,
        valid_sets=[valid_data],
        num_boost_round=300,
        callbacks=[lgb.early_stopping(20), lgb.log_evaluation(50)],
    )

    # ── 评估 ──
    y_pred = model.predict(X_test)
    y_pred_class = np.argmax(y_pred, axis=1)

    acc = accuracy_score(y_test, y_pred_class)
    # 构建完整 4x4 混淆矩阵
    cm_full = np.zeros((4, 4), dtype=int)
    for true_idx, pred_idx in zip(y_test, y_pred_class):
        orig_true = idx_to_label.get(int(true_idx), 0)
        orig_pred = idx_to_label.get(int(pred_idx), 0)
        cm_full[orig_true, orig_pred] += 1

    logger.info("Test accuracy: %.4f", acc)
    logger.info("Confusion matrix (4x4):\n%s", cm_full)

    # ── 保存模型：始终保存 4-class 映射结构 ──
    _ensure_dirs()
    model_data = {
        "model": model,
        "feature_names": feat_cols,
        "label_map": REGIME_LABELS,
        "present_labels": present_labels,
        "label_to_idx": label_to_idx,
        "idx_to_label": idx_to_label,
        "num_classes_trained": num_classes,
        "accuracy": float(acc),
    }
    joblib.dump(model_data, MODEL_PATH)
    logger.info("Model saved to %s", MODEL_PATH)

    # ── 精度摘要 ──
    per_class_acc = {}
    for orig_cls in range(4):
        total_cls = cm_full[orig_cls, :].sum()
        correct_cls = cm_full[orig_cls, orig_cls]
        per_class_acc[REGIME_LABELS[orig_cls]] = {
            "total": int(total_cls),
            "correct": int(correct_cls),
            "accuracy": float(correct_cls / total_cls) if total_cls > 0 else 0.0,
        }

    # ── 保存报告 ──
    report = {
        "accuracy": float(acc),
        "confusion_matrix": cm_full.tolist(),
        "per_class_accuracy": per_class_acc,
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "label_distribution_train": {REGIME_LABELS.get(idx_to_label.get(i, -1), f"CLS_{i}"): int((y_train == i).sum()) for i in range(num_classes)},
        "label_distribution_test": {REGIME_LABELS.get(idx_to_label.get(i, -1), f"CLS_{i}"): int((y_test == i).sum()) for i in range(num_classes)},
        "feature_importance": dict(
            zip(feat_cols, model.feature_importance().tolist())
        ),
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info("Report saved to %s", REPORT_PATH)

    # ── 打印摘要 ──
    print(f"\n{'=' * 60}")
    print(f"  市场状态分类器训练完成")
    print(f"{'=' * 60}")
    print(f"  全局准确率:    {acc:.2%}")
    print(f"  训练样本:      {len(X_train)}")
    print(f"  测试样本:      {len(X_test)}")
    print(f"  标签覆盖:      {', '.join(REGIME_LABELS[i] for i in present_labels)}")
    print(f"\n  混淆矩阵 (4x4):")
    labels_short = ["SIDE", "BULL", "BEAR", "VOLA"]
    header = "           " + "  ".join(f"{l:>6s}" for l in labels_short)
    print(f"  Pred→     {header}")
    for i in range(4):
        row_str = "  ".join(f"{cm_full[i, j]:6d}" for j in range(4))
        print(f"  True {labels_short[i]:4s}  {row_str}")
    print(f"\n  每类精度:")
    for regime, info in sorted(per_class_acc.items()):
        if info["total"] > 0:
            print(f"    {regime:>10s}: {info['accuracy']:.1%} ({info['correct']}/{info['total']})")
        else:
            print(f"    {regime:>10s}: N/A (无样本)")
    print(f"\n  特征重要性 (Top 5):")
    fi = sorted(
        zip(feat_cols, model.feature_importance()),
        key=lambda x: x[1],
        reverse=True,
    )[:5]
    for name, importance in fi:
        print(f"    {name}: {importance}")
    print(f"{'=' * 60}\n")

    return acc


# ══════════════════════════════════════════════════════════════════
# Part A-4: 当前市场状态预测
# ══════════════════════════════════════════════════════════════════


def predict_current_regime() -> dict:
    """
    加载模型, 用最新特征预测当前状态。

    返回:
        {
            "regime": "BULL" | "BEAR" | "SIDEWAYS" | "VOLATILE",
            "confidence": float,
            "regime_code": int,
            "probabilities": list[float],
            "features": dict[str, float],
            "prediction_date": str,
        }
    """
    _ensure_dirs()

    if not MODEL_PATH.exists():
        logger.info("模型不存在，自动训练...")
        train_regime_classifier()

    model_data = joblib.load(MODEL_PATH)
    model = model_data["model"]
    feat_names = model_data["feature_names"]
    label_map = model_data["label_map"]
    idx_to_label = model_data.get("idx_to_label", {i: i for i in range(4)})

    # 加载最新指数数据
    df = load_index_df()
    if len(df) < 60:
        raise ValueError(f"指数数据不足，需要 >= 60 行，当前 {len(df)} 行")

    # 计算特征
    features = compute_regime_features(df)
    features = features.iloc[-1:].copy()

    # 提取特征向量
    X = features[feat_names].fillna(0.0).replace([np.inf, -np.inf], 0.0)

    # 预测
    probs = model.predict(X)[0]
    pred_class_internal = int(np.argmax(probs))
    confidence = float(probs[pred_class_internal])

    # 映射回原始标签代码 (0-3)
    pred_original_code = idx_to_label.get(pred_class_internal, 0)
    regime = label_map.get(pred_original_code, "UNKNOWN")

    # 构建完整概率映射 (4-class)
    full_probs = {}
    for internal_idx, prob in enumerate(probs):
        orig_code = idx_to_label.get(internal_idx, internal_idx)
        full_probs[label_map.get(orig_code, f"CLASS_{orig_code}")] = float(prob)
    # 填充缺失类别概率为 0
    for orig_code in range(4):
        name = label_map.get(orig_code, f"CLASS_{orig_code}")
        if name not in full_probs:
            full_probs[name] = 0.0

    latest_date = features["trade_date"].iloc[0]
    if isinstance(latest_date, pd.Timestamp):
        latest_date = latest_date.strftime("%Y-%m-%d")

    # 额外规则: 如果模型置信度 < 0.4, 用规则判断作为后备
    if confidence < 0.4:
        rule_regime = _rule_based_prediction(df)
        logger.info(
            "模型置信度低 (%.3f), 使用规则后备: %s -> %s",
            confidence, regime, rule_regime,
        )
        regime = rule_regime
        pred_original_code = REGIME_TO_IDX.get(regime, 0)
        confidence = max(confidence, 0.5)  # 规则判断给予基础置信度

    result = {
        "regime": regime,
        "regime_code": pred_original_code,
        "confidence": confidence,
        "probabilities": full_probs,
        "features": {col: float(X[col].iloc[0]) for col in feat_names},
        "prediction_date": latest_date,
    }
    return result


def _rule_based_prediction(index_df: pd.DataFrame) -> str:
    """纯规则后备预测"""
    df = index_df.copy()
    closes = df["close"].values
    n = len(df)

    if n < 21:
        return "SIDEWAYS"

    ret_20d = closes[-1] / closes[-21] - 1.0
    ret_1d = closes[1:] / closes[:-1] - 1.0
    vol_20d = np.std(ret_1d[-20:]) * math.sqrt(252) if len(ret_1d) >= 20 else 0.0

    if ret_20d > 0.10 and vol_20d < 0.30:
        return "BULL"
    if ret_20d < -0.10:
        return "BEAR"
    if vol_20d > 0.35:
        return "VOLATILE"
    return "SIDEWAYS"


# ══════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════


def main():
    """CLI 入口: 训练 + 预测"""
    import argparse

    parser = argparse.ArgumentParser(description="市场状态分类器")
    parser.add_argument("--train", action="store_true", help="训练分类器")
    parser.add_argument("--predict", action="store_true", help="预测当前状态")
    parser.add_argument("--show-data", action="store_true", help="显示标签分布")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.train:
        train_regime_classifier()

    if args.predict:
        result = predict_current_regime()
        print(f"\n{'=' * 60}")
        print(f"  市场状态预测")
        print(f"{'=' * 60}")
        print(f"  预测日期:     {result['prediction_date']}")
        print(f"  当前状态:     {result['regime']}")
        print(f"  置信度:       {result['confidence']:.2%}")
        print(f"\n  各状态概率:")
        for regime_name, prob in sorted(
            result["probabilities"].items(), key=lambda x: x[1], reverse=True
        ):
            bar = "█" * int(prob * 30)
            print(f"    {regime_name:>10s}: {prob:>6.1%}  {bar}")
        print(f"\n  关键特征:")
        for k, v in result["features"].items():
            print(f"    {k}: {v:.4f}")
        print(f"{'=' * 60}\n")

    if args.show_data:
        df = load_index_df()
        labels = label_regime(df)
        print(f"\n标注入库: {len(labels)} 行")
        print(f"状态分布:")
        for code, name in sorted(REGIME_LABELS.items()):
            cnt = (labels["regime_code"] == code).sum()
            pct = cnt / len(labels) * 100 if len(labels) > 0 else 0
            bar = "█" * int(pct / 3)
            print(f"  {name:>10s}: {cnt:4d}  ({pct:5.1f}%) {bar}")
        print()

    if not any([args.train, args.predict, args.show_data]):
        parser.print_help()


if __name__ == "__main__":
    main()
