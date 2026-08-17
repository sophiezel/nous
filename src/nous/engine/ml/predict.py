"""模型预测引擎: 加载训练好的模型, 对全市场股票打分排序"""
import sys
from pathlib import Path
from datetime import date
import pandas as pd
import numpy as np
import joblib
import sqlite3
import logging

logger = logging.getLogger(__name__)

from nous.core.paths import factor_dir, model_dir, screener_db

MODEL_DIR = model_dir()
FACTOR_DIR = factor_dir()
DB_PATH = screener_db()


def load_latest_model():
    """加载最新训练的模型 (LightGBM / VotingRegressor / 任何 sklearn 兼容模型)

    从 MODEL_DIR 中按文件名排序取最新的 lgb_*.pkl, 使用 joblib 加载。
    兼容: LightGBM, XGBoost, CatBoost, Ridge, MLPRegressor, VotingRegressor
    """
    models = sorted(MODEL_DIR.glob("lgb_*.pkl"))
    if not models:
        raise FileNotFoundError(f"No model found in {MODEL_DIR} (looking for lgb_*.pkl)")
    model_path = str(models[-1])
    logger.info(f"Loading model: {model_path}")
    return joblib.load(model_path)


def predict_scores(factors_df=None, model=None, top_n=100):
    """
    对全市场股票打分, 返回 TOP N。

    Args:
        factors_df: 因子 DataFrame。如果为 None，从最新快照加载。
        model: LightGBM 模型。如果为 None，加载最新模型。
        top_n: 返回前 N 只

    Returns:
        DataFrame with columns [symbol, score, ...]
    """
    if factors_df is None:
        path = FACTOR_DIR / "latest.parquet"
        if not path.exists():
            logger.info("No factor snapshot found, computing factors...")
            from nous.engine.ml.factor_compute import compute_all_factors, save_factor_snapshot
            df = compute_all_factors()
            save_factor_snapshot(df)
            factors_df = df
        else:
            factors_df = pd.read_parquet(path)

    if model is None:
        model = load_latest_model()

    # 用有最多股票的最近交易日（而非绝对最新日）
    date_counts = factors_df.groupby('trade_date').size()
    # 找最近一个有超过 min_stocks 只股票的日期
    min_stocks = 100
    valid_dates = date_counts[date_counts >= min_stocks].index
    if len(valid_dates) > 0:
        latest_date = valid_dates.max()
    else:
        latest_date = factors_df['trade_date'].max()
    latest = factors_df[factors_df['trade_date'] == latest_date].copy()
    logger.info(f"Prediction date: {str(latest_date)[:10]}, stocks: {len(latest)}")

    factor_names = [c for c in latest.columns if c.startswith('K')]
    logger.info(f"Using {len(factor_names)} factors for prediction")

    X = latest[factor_names].copy()

    # 处理 NaN (用训练时的中位数填充)
    X = X.fillna(X.median())

    # 预测
    scores = model.predict(X)

    result = pd.DataFrame({
        'symbol': latest['symbol'].values,
        'model_score': scores,
    })

    # 标准化分数到 0-10 区间 (方便接入 screener)
    if len(scores) > 1:
        s_min, s_max = scores.min(), scores.max()
        if s_max > s_min:
            result['model_score_norm'] = 10 * (scores - s_min) / (s_max - s_min)
        else:
            result['model_score_norm'] = 5.0
    else:
        result['model_score_norm'] = 5.0

    result = result.sort_values('model_score', ascending=False)

    # 返回 TOP N + 基本信息
    top = result.head(top_n).copy()

    # 附加名称和基本面
    conn = sqlite3.connect(str(DB_PATH))
    for i, row in top.iterrows():
        info = conn.execute(
            "SELECT b.name, f.pe, f.pb, f.roe FROM stock_basic b LEFT JOIN stock_fundamental f ON b.symbol=f.symbol WHERE b.symbol=?",
            (row['symbol'],)
        ).fetchone()
        if info:
            top.at[i, 'name'] = info[0]
            top.at[i, 'pe'] = info[1]
            top.at[i, 'pb'] = info[2]
            top.at[i, 'roe'] = info[3]
    conn.close()

    return top


def get_model_recommendations(top_n=20):
    """
    获取模型推荐列表，可直接喂入 screener L3 或 soul_engine。
    返回: list[dict]
    """
    top = predict_scores(top_n=top_n)
    recommendations = []
    for _, row in top.iterrows():
        recommendations.append({
            'symbol': row['symbol'],
            'name': row.get('name', ''),
            'model_score': round(float(row['model_score']), 4),
            'model_score_norm': round(float(row['model_score_norm']), 2),
            'pe': round(float(row['pe']), 1) if pd.notna(row.get('pe')) else None,
            'roe': round(float(row['roe']), 1) if pd.notna(row.get('roe')) else None,
        })
    return recommendations


def get_all_stock_scores(factors_df=None, model=None):
    """
    返回全市场股票的模型分数 (用于 screener 集成)。
    返回: dict[str, float] — {symbol: model_score_norm}
    """
    if factors_df is None:
        path = FACTOR_DIR / "latest.parquet"
        if not path.exists():
            logger.info("No factor snapshot found, computing factors...")
            from nous.engine.ml.factor_compute import compute_all_factors, save_factor_snapshot
            df = compute_all_factors()
            save_factor_snapshot(df)
            factors_df = df
        else:
            factors_df = pd.read_parquet(path)

    if model is None:
        try:
            model = load_latest_model()
        except FileNotFoundError:
            logger.warning("No model found — skipping ML enhancement")
            return {}

    latest_date = factors_df['trade_date'].max()
    latest = factors_df[factors_df['trade_date'] == latest_date].copy()

    factor_names = [c for c in latest.columns if c.startswith('K')]
    X = latest[factor_names].copy()
    X = X.fillna(X.median())

    scores = model.predict(X)

    # 标准化
    if len(scores) > 1:
        s_min, s_max = scores.min(), scores.max()
        if s_max > s_min:
            scores_norm = 10 * (scores - s_min) / (s_max - s_min)
        else:
            scores_norm = np.full_like(scores, 5.0)
    else:
        scores_norm = np.full_like(scores, 5.0)

    return dict(zip(latest['symbol'].values, scores_norm))


def get_model_ranks(factors_df=None, model=None):
    """
    返回全市场股票的模型排序百分位 (0-100, 越高越好)。
    用于 screener 中的 rank_boost 逻辑。
    返回: dict[str, float] — {symbol: rank_pct}
    """
    if factors_df is None:
        path = FACTOR_DIR / "latest.parquet"
        if not path.exists():
            return {}

    if model is None:
        try:
            model = load_latest_model()
        except FileNotFoundError:
            return {}

    latest_date = factors_df['trade_date'].max()
    latest = factors_df[factors_df['trade_date'] == latest_date].copy()

    factor_names = [c for c in latest.columns if c.startswith('K')]
    X = latest[factor_names].copy()
    X = X.fillna(X.median())

    scores = model.predict(X)
    n = len(scores)
    if n == 0:
        return {}

    # 百分位排名 (0 = 最低, 100 = 最高)
    ranks = pd.Series(scores).rank(pct=True) * 100
    return dict(zip(latest['symbol'].values, ranks.values))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    recs = get_model_recommendations(10)
    print(f"{'='*60}")
    print(f"模型预测 TOP 10 (日期: {date.today().isoformat()})")
    print(f"{'='*60}")
    for i, r in enumerate(recs, 1):
        print(f"  {i:2d}. {r['symbol']} {r['name']:<8s} 模型评分={r['model_score_norm']:.2f}  PE={r.get('pe','N/A')}")


def write_ml_scores(scores_df, pool_type: str = 'a_long', model_path: str = ''):
    """将模型预测结果写入screener.db ml_scores表
    
    Args:
        scores_df: predict_scores()返回的DataFrame,含symbol/model_score/model_score_norm
        pool_type: 池类型 (a_long/a_short/hk_long/hk_short)
        model_path: 模型文件路径
    """
    import sqlite3 as _sql
    db = _sql.connect(str(DB_PATH))
    db.execute("PRAGMA busy_timeout=10000")
    
    # 确保表存在
    db.executescript("""
        CREATE TABLE IF NOT EXISTS ml_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
            pool_type TEXT NOT NULL DEFAULT 'a_long',
            model_name TEXT NOT NULL DEFAULT 'lgb',
            model_score REAL, model_score_norm REAL, model_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(trade_date, symbol, pool_type)
        );
    """)
    
    trade_date = str(date.today())
    inserted = 0
    for _, row in scores_df.iterrows():
        try:
            db.execute("""
                INSERT OR REPLACE INTO ml_scores 
                (trade_date, symbol, pool_type, model_name, model_score, model_score_norm, model_path)
                VALUES (?,?,?,?,?,?,?)
            """, (trade_date, str(row['symbol']), pool_type, 'lgb',
                  float(row.get('model_score', 0) or 0),
                  float(row.get('model_score_norm', 0) or 0),
                  model_path))
            inserted += 1
        except Exception:
            pass
    db.commit()
    db.close()
    logger.info(f"[predict] 写入 {inserted} 条到 ml_scores ({pool_type})")
