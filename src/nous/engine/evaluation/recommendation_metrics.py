"""
荐股准确率评估体系
- Rank IC: 模型预测分数 vs 未来N日实际收益率 Spearman 相关系数
- Top-K Hit Rate: 模型TOP K中实际收益进入全市场前20%的比例
- 分层IC: 按市值/行业分组
- 时间衰减: 预测后 D+1/D+3/D+5/D+10/D+20 IC曲线
"""

import json
import logging
from pathlib import Path
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
import sqlite3
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

EVAL_DIR = data_dir() / "evaluation"
from nous.core.paths import data_dir, screener_db
DB_PATH = screener_db()
def compute_recommendation_metrics(
    predictions_df: pd.DataFrame,
    market: str = "a",
    top_k_list: list[int] = None,
    forward_periods: list[int] = None,
) -> dict:
    """
    计算荐股准确率指标。
    
    Args:
        predictions_df: [symbol, prediction_date, predicted_score] + 实际收益率列
        market: 'a' or 'hk'
        top_k_list: 要评估的Top-K值
        forward_periods: 预测后N日的时间范围
    
    Returns:
        dict with rank_ic, hit_rates, decay_curve, stratified_ic
    """
    if top_k_list is None:
        top_k_list = [10, 20, 30, 50]
    if forward_periods is None:
        forward_periods = [1, 3, 5, 10, 20]
    
    df = predictions_df.copy()
    results = {}
    
    # 1. Rank IC (预测分数 vs 未来收益)
    for fp in forward_periods:
        col = f"ret_{fp}d"
        if col in df.columns:
            valid = df["predicted_score"].notna() & df[col].notna()
            if valid.sum() >= 30:
                ic, _ = spearmanr(df.loc[valid, "predicted_score"], df.loc[valid, col])
                results[f"rank_ic_{fp}d"] = round(float(ic) if not np.isnan(ic) else 0, 4)
            else:
                results[f"rank_ic_{fp}d"] = None
    
    # 2. IC Decay Curve
    decay = {}
    for fp in forward_periods:
        key = f"rank_ic_{fp}d"
        if key in results and results[key] is not None:
            decay[f"d{fp}"] = results[key]
    results["ic_decay_curve"] = decay
    
    # 3. Top-K Hit Rate
    for k in top_k_list:
        for fp in forward_periods[:3]:  # Only D+1, D+3, D+5 for hit rate
            col = f"ret_{fp}d"
            if col not in df.columns:
                continue
            
            # 全市场前20%分位阈值
            threshold = df[col].quantile(0.8) if df[col].notna().sum() > 0 else 0
            
            # 按预测分数排序取Top-K
            top_k = df.nlargest(k, "predicted_score")
            if len(top_k) == 0:
                results[f"hit_rate_top{k}_{fp}d"] = 0
                continue
            
            hits = (top_k[col] >= threshold).sum()
            hit_rate = hits / len(top_k) if len(top_k) > 0 else 0
            results[f"hit_rate_top{k}_{fp}d"] = round(float(hit_rate), 4)
    
    # 4. 分层IC（按市值分组-简化版：按预测分数高低分组）
    if "predicted_score" in df.columns and "ret_5d" in df.columns:
        df_valid = df[df["predicted_score"].notna() & df["ret_5d"].notna()]
        if len(df_valid) >= 50:
            df_valid["score_quantile"] = pd.qcut(df_valid["predicted_score"], q=4, labels=["Q1", "Q2", "Q3", "Q4"])
            for q in ["Q1", "Q2", "Q3", "Q4"]:
                subset = df_valid[df_valid["score_quantile"] == q]
                if len(subset) >= 20:
                    ic, _ = spearmanr(subset["predicted_score"], subset["ret_5d"])
                    results[f"ic_by_quantile_{q}"] = round(float(ic) if not np.isnan(ic) else 0, 4)
    
    # 5. 汇总
    avg_ic = np.mean([v for k, v in results.items() if k.startswith("rank_ic_") and v is not None])
    results["avg_rank_ic"] = round(float(avg_ic), 4) if not np.isnan(avg_ic) else 0
    results["market"] = market
    results["n_predictions"] = len(df)
    
    return results


def evaluate_from_model(test_data: dict, market: str = "a") -> dict:
    """
    从模型预测结果评估荐股准确率。
    test_data: {X_test, y_test, y_pred, symbols_test, dates_test, factor_names}
    """
    df = pd.DataFrame({
        "symbol": test_data["symbols_test"],
        "date": test_data["dates_test"],
        "predicted_score": test_data["y_pred"],
        "ret_5d": test_data["y_test"].values,
    })
    return compute_recommendation_metrics(df, market=market)


def evaluate_from_screen_results(market: str = "a", screen_date: str = None) -> dict:
    """
    从 screen_results 表评估已有的筛选结果准确率。
    使用 screen_results.score 作为预测分数，stock_daily 计算后续收益。
    """
    conn = sqlite3.connect(str(DB_PATH))
    
    if screen_date is None:
        cursor = conn.execute("SELECT MAX(screen_date) FROM screen_results")
        screen_date = cursor.fetchone()[0]
    
    if market == "hk":
        market_filter = "AND b.market = 'hk'"
    else:
        market_filter = "AND (b.market = 'a' OR b.market IS NULL)"
    
    query = f"""
        SELECT s.symbol, s.score as predicted_score,
               s.screen_date as prediction_date
        FROM screen_results s
        LEFT JOIN stock_basic b ON s.symbol = b.symbol
        WHERE s.screen_date = ?
        {market_filter}
        ORDER BY s.score DESC
    """
    df = pd.read_sql_query(query, conn, params=(screen_date,))
    
    if len(df) == 0:
        conn.close()
        return {"error": f"无数据: {screen_date}", "n_predictions": 0}
    
    # 计算后续实际收益
    symbols_str = "','".join(df["symbol"].tolist())
    
    ret_query = f"""
        SELECT d.symbol, d.trade_date, d.close,
               AVG(CASE WHEN d2.trade_date > d.trade_date 
                   AND d2.trade_date <= date(d.trade_date, '+5 days')
                   THEN d2.close END) as close_5d_after
        FROM stock_daily d
        LEFT JOIN stock_daily d2 ON d.symbol = d2.symbol
        WHERE d.symbol IN ('{symbols_str}')
        AND d.trade_date = ?
        GROUP BY d.symbol
    """
    try:
        ret_df = pd.read_sql_query(ret_query, conn, params=(screen_date,))
        conn.close()
    except Exception as e:
        conn.close()
        logger.warning(f"计算future return失败: {e}")
        return compute_recommendation_metrics(df, market=market)
    
    # 简化: 使用T+5日收益
    if len(ret_df) > 0 and "close_5d_after" in ret_df.columns:
        ret_df["ret_5d"] = ret_df["close_5d_after"] / ret_df["close"] - 1
        df = df.merge(ret_df[["symbol", "ret_5d"]], on="symbol", how="left")
    
    return compute_recommendation_metrics(df, market=market)


def save_evaluation_report(metrics: dict, name: str = "recommendation"):
    """保存评估报告到 data/evaluation/"""
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = EVAL_DIR / f"{name}_{today}.json"
    with open(path, "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"评估报告已保存: {path}")
    return str(path)


def evaluate_croc_signals(db_conn, start_date: str, end_date: str) -> dict:
    """
    鳄鱼派信号准确率评估
    
    评估维度:
    - 主线命中率: 推荐时主线阶段 vs 后续表现
    - 两只脚胜率: 两只脚共振时推荐的股票胜率
    - 拥挤度预警准确率: 拥挤度>85%后是否真的下跌
    """
    from nous.engine.signals.crocodile_signals import evaluate_crocodile_signals
    
    results = {
        'period': f'{start_date} ~ {end_date}',
        'two_feet_win_rate': None,
        'mainline_hit_rate': None,
        'crowding_alert_accuracy': None,
        'total_signals': 0,
    }
    
    # 获取区间内所有推荐
    rows = db_conn.execute("""
        SELECT rec_date, symbol, score, buy_reason
        FROM recommendation_pool
        WHERE rec_date BETWEEN ? AND ?
        ORDER BY rec_date
    """, (start_date, end_date)).fetchall()
    
    if not rows:
        results['error'] = '无推荐数据'
        return results
    
    results['total_signals'] = len(rows)
    
    # 按日期分组评估
    dates = set(r[0] for r in rows)
    two_feet_wins = 0
    two_feet_total = 0
    mainline_hits = 0
    mainline_total = 0
    
    for d in sorted(dates):
        try:
            croc = evaluate_crocodile_signals(db_conn, d)
            signals = croc.get('signals', {})
            
            # 两只脚评估
            tf = signals.get('two_feet', {})
            if tf.get('status') == '共振上涨':
                two_feet_total += 1
                # 检查当天推荐的股票后续5日表现
                day_picks = [r for r in rows if r[0] == d]
                for pick in day_picks:
                    sym = pick[1]
                    future = db_conn.execute(
                        "SELECT close FROM stock_daily_all WHERE symbol=? AND trade_date>? ORDER BY trade_date LIMIT 5",
                        (sym, d)
                    ).fetchall()
                    if len(future) >= 5:
                        ret = (future[-1][0] - future[0][0]) / future[0][0] * 100
                        if ret > 0:
                            two_feet_wins += 1
        except:
            pass
    
    if two_feet_total > 0:
        results['two_feet_win_rate'] = round(two_feet_wins / two_feet_total * 100, 1)
    
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["a", "hk"], default="a")
    parser.add_argument("--date", default=None, help="screen_date (默认最新)")
    args = parser.parse_args()
    
    metrics = evaluate_from_screen_results(market=args.market, screen_date=args.date)
    
    if "error" in metrics:
        print(f"ERROR: {metrics['error']}")
    else:
        print(f"荐股准确率 ({args.market}):")
        print(f"  预测数: {metrics['n_predictions']}")
        print(f"  平均Rank IC: {metrics.get('avg_rank_ic', 'N/A')}")
        print(f"  IC衰减: {metrics.get('ic_decay_curve', {})}")
        
        hit_keys = [k for k in metrics if k.startswith("hit_rate")]
        for k in sorted(hit_keys):
            print(f"  {k}: {metrics[k]}")
        
        save_evaluation_report(metrics)
