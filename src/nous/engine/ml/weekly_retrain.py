"""
滚动重训练管线 (周度)

每周日运行:
  1. 回补本周新增日线数据
  2. 重新计算全量因子
  3. 训练 LightGBM (扩展窗口)
  4. IC 对比 (本周 vs 上周 vs 历史)
  5. SHAP 因子衰减检测
  6. MLflow 记录 + 告警
"""

import sys
import json
import time
import logging
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[4]  # nous repo root))

logger = logging.getLogger(__name__)


def compare_ic(current_ic: float, previous_ic_path: Optional[Path] = None) -> dict:
    """对比本周 IC 与历史 IC"""
    result = {
        "current_ic": round(current_ic, 4),
        "previous_ic": None,
        "ic_change": None,
        "alert": None,
    }
    
    if previous_ic_path and previous_ic_path.exists():
        with open(previous_ic_path) as f:
            prev = json.load(f)
        prev_ic = prev.get("ic", 0)
        result["previous_ic"] = prev_ic
        result["ic_change"] = round(current_ic - prev_ic, 4)
        
        if current_ic < prev_ic * 0.8:  # 下降 20%+
            result["alert"] = "⚠️ IC 下降超过 20%，因子可能衰减"
        elif current_ic < prev_ic * 0.5:
            result["alert"] = "🔴 IC 腰斩！需紧急排查"
        elif current_ic > prev_ic * 1.2:
            result["alert"] = "✅ IC 显著提升"
    
    return result


def detect_factor_decay(
    current_importance: pd.DataFrame,
    previous_importance_path: Optional[Path] = None,
    threshold: float = 0.5,
) -> dict:
    """检测因子衰减"""
    if not previous_importance_path or not previous_importance_path.exists():
        return {"decayed_factors": [], "rising_factors": [], "n_decayed": 0, "n_rising": 0}
    
    prev_imp = pd.read_csv(previous_importance_path)
    
    # 归一化重要度
    cur = current_importance.set_index("factor")["importance"]
    cur_norm = cur / cur.sum()
    prev_norm = prev_imp.set_index("factor")["importance"]
    prev_norm = prev_norm / prev_norm.sum()
    
    # 对比变化
    common = cur_norm.index.intersection(prev_norm.index)
    changes = pd.DataFrame({
        "factor": common,
        "cur": cur_norm[common],
        "prev": prev_norm[common],
        "change_pct": (cur_norm[common] - prev_norm[common]) / prev_norm[common].replace(0, np.nan),
    })
    
    decayed = changes[changes["change_pct"] < -threshold]["factor"].tolist()
    rising = changes[changes["change_pct"] > threshold]["factor"].tolist()
    
    return {
        "decayed_factors": decayed,
        "rising_factors": rising,
        "n_decayed": len(decayed),
        "n_rising": len(rising),
    }


def run_weekly_retraining(limit: int = 500, forward: int = 5):
    """
    周度重训练完整管线。
    """
    today = date.today()
    ic_dir = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "ic_analysis"
    imp_dir = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "factor_importance"
    imp_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"{'='*60}")
    logger.info(f"周度重训练 — {today.isoformat()}")
    logger.info(f"{'='*60}")
    
    # 1. 因子计算
    t0 = time.time()
    from nous.engine.ml.factor_compute import compute_all_factors, save_factor_snapshot
    
    logger.info("Step 1/5: 计算全量因子...")
    
    # 如果指定 limit，选取成交额最大的 N 只
    if limit and limit > 0:
        import sqlite3
        db_path = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"
        conn = sqlite3.connect(str(db_path))
        latest_full = conn.execute(
            "SELECT trade_date FROM stock_daily d JOIN stock_basic b ON d.symbol=b.symbol WHERE b.market='a' GROUP BY trade_date HAVING COUNT(*) > 1000 ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        if latest_full:
            symbols = [r[0] for r in conn.execute(
                "SELECT d.symbol FROM stock_daily d JOIN stock_basic b ON d.symbol=b.symbol WHERE b.market='a' AND d.trade_date=? ORDER BY d.amount DESC LIMIT ?",
                (latest_full[0], limit)
            ).fetchall()]
        else:
            symbols = None
        conn.close()
    else:
        symbols = None
    
    df = compute_all_factors(symbols=symbols)
    snapshot_path = save_factor_snapshot(df)
    logger.info(f"  因子快照: {snapshot_path} ({len(df)}行, {df['symbol'].nunique()}只)")
    
    # 2. 模型训练
    logger.info("Step 2/5: 训练 LightGBM...")
    from nous.engine.ml.model_train import run_pipeline
    
    result = run_pipeline(forward_period=forward)
    if result is None:
        logger.error("训练失败，终止")
        return None
    
    current_ic = result["ic"]
    rank_ic = result.get("rank_ic")
    logger.info(f"  IC={current_ic:.4f}, Rank IC={rank_ic}")
    
    # 3. IC 对比
    logger.info("Step 3/5: IC 对比分析...")
    prev_ic_files = sorted(ic_dir.glob("ic_*.json"))
    prev_ic = prev_ic_files[-2] if len(prev_ic_files) >= 2 else None  # 上一次
    ic_comparison = compare_ic(current_ic, prev_ic)
    logger.info(f"  {ic_comparison}")
    
    # 4. SHAP 因子衰减检测
    logger.info("Step 4/5: 因子衰减检测...")
    try:
        from nous.engine.ml.shap_analysis import run_shap_analysis
        shap_report = run_shap_analysis()
    except Exception as e:
        logger.warning(f"SHAP 分析跳过: {e}")
        shap_report = None
    
    # 因子重要性对比
    shap_csv = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "shap_analysis" / "shap_importance.csv"
    if not shap_csv.exists():
        shap_csv = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "shap" / "shap_importance.csv"
    
    cur_imp = None
    if shap_csv.exists():
        cur_imp = pd.read_csv(shap_csv)
    
    prev_imp_file = None
    csv_files = sorted(imp_dir.glob("*.csv"))
    if len(csv_files) >= 2:
        prev_imp_file = csv_files[-2]
    
    decay = {"decayed_factors": [], "rising_factors": [], "n_decayed": 0, "n_rising": 0}
    if cur_imp is not None:
        try:
            decay = detect_factor_decay(cur_imp, prev_imp_file)
        except Exception as e:
            logger.warning(f"因子衰减检测跳过: {e}")
    if decay["decayed_factors"]:
        logger.warning(f"  ⚠️ 衰减因子 ({decay['n_decayed']}): {decay['decayed_factors'][:5]}")
    if decay["rising_factors"]:
        logger.info(f"  ✅ 上升因子 ({decay['n_rising']}): {decay['rising_factors'][:5]}")
    
    # 保存因子重要性历史
    if cur_imp is not None:
        cur_imp.to_csv(imp_dir / f"importance_{today.isoformat()}.csv", index=False)
    
    # 5. 汇总报告
    elapsed = time.time() - t0
    report = {
        "date": today.isoformat(),
        "elapsed_s": round(elapsed, 1),
        "model": {
            "ic": current_ic,
            "rank_ic": rank_ic,
            "n_samples": result.get("train_samples", 0),
            "top_factors": result.get("top_10_factors", [])[:5],
        },
        "ic_comparison": ic_comparison,
        "factor_decay": {
            "n_decayed": decay["n_decayed"],
            "n_rising": decay["n_rising"],
            "decayed_top5": decay["decayed_factors"][:5],
            "rising_top5": decay["rising_factors"][:5],
        },
        "snapshot_path": str(snapshot_path),
    }
    
    report_path = ic_dir / f"weekly_report_{today.isoformat()}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    
    logger.info(f"Step 5/5: 报告已保存 → {report_path}")
    logger.info(f"{'='*60}")
    logger.info(f"重训练完成: {elapsed:.0f}s")
    logger.info(f"{'='*60}")
    
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    import argparse
    parser = argparse.ArgumentParser(description="周度滚动重训练")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--forward", type=int, default=5)
    args = parser.parse_args()
    
    run_weekly_retraining(limit=args.limit, forward=args.forward)
