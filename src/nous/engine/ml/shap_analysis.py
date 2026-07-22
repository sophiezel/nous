"""SHAP 因子可解释性分析 (stub — 可增量实现)"""
import sys
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

FACTOR_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "factors"
MODEL_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "models"
SHAP_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "shap"


def run_shap_analysis(
    model_path: Optional[str] = None,
    factor_path: Optional[str] = None,
    max_samples: int = 1000,
) -> dict:
    """
    运行 SHAP 分析，计算因子贡献度。
    
    Args:
        model_path: 模型路径 (默认使用最新模型)
        factor_path: 因子快照路径 (默认使用 latest.parquet)
        max_samples: SHAP 分析最大样本数
    
    Returns:
        {top_10_factors: [...], summary: str, shap_path: str}
    """
    SHAP_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. 加载模型
    import joblib
    import json
    
    if model_path is None:
        from datetime import date
        today = date.today().isoformat()
        candidates = sorted(MODEL_DIR.glob(f"lgb_*.pkl"))
        if not candidates:
            logger.warning("未找到已训练的模型，跳过 SHAP 分析")
            return {}
        model_path = str(candidates[-1])
        logger.info(f"使用最新模型: {model_path}")
    
    model = joblib.load(model_path)
    
    # 2. 加载因子
    if factor_path is None:
        factor_path = str(FACTOR_DIR / "latest.parquet")
    
    import pandas as pd
    df = pd.read_parquet(factor_path)
    factor_names = [c for c in df.columns if c.startswith("K")]
    
    logger.info(f"加载因子: {len(df)}行, {len(factor_names)}个因子")
    
    # 3. 准备数据 (与 model_train.py 相同的预处理)
    import numpy as np
    
    X = df[factor_names].copy()
    # 过滤全NaN行
    min_valid = int(len(factor_names) * 0.8)
    valid = X.notna().sum(axis=1) >= min_valid
    X = X[valid]
    
    # 中位数填充 + Z-score (与训练一致)
    train_medians = X.median()
    X = X.fillna(train_medians)
    train_mean = X.mean()
    train_std = X.std().replace(0, 1)
    X_scaled = (X - train_mean) / train_std
    
    # 采样 (减少 SHAP 计算量)
    if len(X_scaled) > max_samples:
        X_scaled = X_scaled.sample(n=max_samples, random_state=42)
        logger.info(f"SHAP 采样: {max_samples}行")
    
    # 4. 尝试 SHAP
    try:
        import shap
        
        # 创建解释器 (TreeExplainer 对 LightGBM 原生支持)
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_scaled)
        
        # 全局重要性 (mean |SHAP value|)
        shap_importance = np.abs(shap_values).mean(axis=0)
        factor_ranking = pd.DataFrame({
            "factor": factor_names,
            "shap_value": shap_importance,
        }).sort_values("shap_value", ascending=False)
        
        top_10 = factor_ranking.head(10)["factor"].tolist()
        
        # 保存 SHAP 结果
        import json
        from datetime import date
        today = date.today().isoformat()
        
        shap_summary = {
            "top_10_factors": top_10,
            "factor_ranking": factor_ranking.to_dict("records"),
        }
        
        shap_path = SHAP_DIR / f"shap_{today}.json"
        with open(shap_path, "w") as f:
            json.dump(shap_summary, f, indent=2, ensure_ascii=False, default=str)
        
        logger.info(f"SHAP 分析完成: TOP5={top_10[:5]}")
        logger.info(f"SHAP 结果已保存: {shap_path}")
        
        return {
            "top_10_factors": top_10,
            "shap_path": str(shap_path),
        }
        
    except ImportError:
        logger.warning("shap 包未安装，跳过 SHAP 分析。安装: pip install shap")
        return {"top_10_factors": [], "shap_path": None, "note": "shap not installed"}
    except Exception as e:
        logger.error(f"SHAP 分析失败: {e}")
        return {"top_10_factors": [], "shap_path": None, "error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = run_shap_analysis()
    print(f"SHAP 结果: TOP5={result.get('top_10_factors', [])[:5]}")
