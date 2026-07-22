"""
模型衰退监控 + 自动重训练触发
- Rank IC 滚动均值监控
- PSI (Population Stability Index) 预测分布偏移检测
- 特征重要性漂移检测
- 半衰期估算
- 自动触发: 定期(每月) + 事件驱动(IC跌破阈值)
"""

import json
import logging
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import sqlite3

logger = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "evaluation"
IC_DIR = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "ic_analysis"
DB_PATH = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"

# 衰退阈值
THRESHOLDS = {
    "rank_ic_floor": 0.02,          # Rank IC下限,连续N日低于此触发
    "rank_ic_consecutive_days": 5,  # 连续N日
    "psi_warning": 0.15,            # PSI预警
    "psi_critical": 0.25,           # PSI严重
    "half_life_warning_months": 6,  # 半衰期<6个月告警
    "feature_drift_top10_change": 3, # Top-10特征中变化>=N个
}

# 初始化DB表
def init_model_health_db():
    """创建 model_health_log 表(如不存在)"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_health_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_date TEXT NOT NULL,
            model_id TEXT NOT NULL,
            market TEXT,
            strategy TEXT,
            rank_ic_20d REAL,
            psi REAL,
            feature_drift_count INTEGER,
            half_life_months REAL,
            triggered BOOLEAN DEFAULT 0,
            trigger_reason TEXT,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_health_date ON model_health_log(check_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_health_model ON model_health_log(model_id)")
    conn.commit()
    conn.close()


def compute_rolling_rank_ic(ic_history: list[dict], window: int = 20) -> float:
    """
    计算滚动 Rank IC 均值。
    ic_history: [{"date": str, "rank_ic": float}]
    """
    if len(ic_history) < window:
        if len(ic_history) == 0:
            return 0
        return np.mean([d["rank_ic"] for d in ic_history])
    
    recent = ic_history[-window:]
    return float(np.mean([d["rank_ic"] for d in recent]))


def compute_psi(
    train_predictions: np.ndarray,
    current_predictions: np.ndarray,
    bins: int = 10,
) -> float:
    """
    Population Stability Index: 测量预测分布偏移。
    
    PSI = Σ (P_current - P_train) * ln(P_current / P_train)
    
    PSI < 0.1: 无显著偏移
    0.1 <= PSI < 0.25: 中等偏移
    PSI >= 0.25: 显著偏移，建议重训练
    """
    if len(train_predictions) < 10 or len(current_predictions) < 10:
        return 0.0
    
    # 使用 train 的分位数为 bin edges
    _, bin_edges = np.histogram(train_predictions, bins=bins)
    
    train_counts, _ = np.histogram(train_predictions, bins=bin_edges)
    current_counts, _ = np.histogram(current_predictions, bins=bin_edges)
    
    # 转为比例+平滑
    eps = 1e-6
    train_pct = (train_counts + eps) / (train_counts.sum() + eps * bins)
    current_pct = (current_counts + eps) / (current_counts.sum() + eps * bins)
    
    psi = np.sum((current_pct - train_pct) * np.log(current_pct / train_pct))
    return float(psi)


def compute_feature_drift(
    train_importance: dict[str, float],
    current_importance: dict[str, float],
    top_n: int = 10,
) -> int:
    """
    计算Top-N特征重要性排序变化数量。
    """
    train_top = set(sorted(train_importance, key=train_importance.get, reverse=True)[:top_n])
    current_top = set(sorted(current_importance, key=current_importance.get, reverse=True)[:top_n])
    
    # 新增的特征 + 退出的特征
    drifted = len(train_top - current_top)
    return drifted


def estimate_half_life(ic_history: list[dict]) -> float:
    """
    估算IC半衰期: IC(t) = IC0 * e^(-λt)
    半衰期 = ln(2) / λ
    
    使用滚动IC的对数回归。
    """
    if len(ic_history) < 30:
        return float("inf")
    
    dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in ic_history]
    ics = [d["rank_ic"] for d in ic_history]
    
    # 天数差
    days = np.array([(d - dates[0]).days for d in dates])
    
    # 对数线性回归: ln(IC) = ln(IC0) - λ * t
    valid = [i for i, ic in enumerate(ics) if ic > 0.001]
    if len(valid) < 10:
        return float("inf")
    
    log_ics = np.log([ics[i] for i in valid])
    t = days[valid]
    
    # 简单线性回归
    A = np.vstack([t, np.ones_like(t)]).T
    slope, _ = np.linalg.lstsq(A, log_ics, rcond=None)[0]
    lam = -slope  # decay rate
    
    if lam <= 0:
        return float("inf")
    
    half_life_days = np.log(2) / lam
    half_life_months = half_life_days / 30.44
    return round(half_life_months, 1)


def check_model_health(
    model_id: str,
    market: str = "a",
    strategy: str = "short",
    ic_history: list[dict] = None,
    train_predictions: np.ndarray = None,
    current_predictions: np.ndarray = None,
    train_importance: dict = None,
    current_importance: dict = None,
) -> dict:
    """
    综合模型健康检查。返回是否需重训练 + 触发原因。
    """
    triggers = []
    metrics = {
        "model_id": model_id, "market": market, "strategy": strategy,
        "check_date": date.today().isoformat(),
    }
    
    # 1. Rank IC 滚动检查
    if ic_history and len(ic_history) >= THRESHOLDS["rank_ic_consecutive_days"]:
        recent = ic_history[-THRESHOLDS["rank_ic_consecutive_days"]:]
        recent_ics = [d["rank_ic"] for d in recent]
        if all(ic < THRESHOLDS["rank_ic_floor"] for ic in recent_ics):
            triggers.append(f"连续{THRESHOLDS['rank_ic_consecutive_days']}日RankIC<{THRESHOLDS['rank_ic_floor']}")
    
    rolling_ic = compute_rolling_rank_ic(ic_history) if ic_history else 0
    metrics["rank_ic_20d"] = round(rolling_ic, 4)
    
    # 2. PSI
    if train_predictions is not None and current_predictions is not None:
        psi = compute_psi(train_predictions, current_predictions)
        metrics["psi"] = round(psi, 4)
        if psi >= THRESHOLDS["psi_critical"]:
            triggers.append(f"PSI={psi:.2f}>=临界阈值{THRESHOLDS['psi_critical']}")
        elif psi >= THRESHOLDS["psi_warning"]:
            triggers.append(f"PSI={psi:.2f}>=预警阈值{THRESHOLDS['psi_warning']}")
    else:
        metrics["psi"] = None
    
    # 3. 特征漂移
    if train_importance and current_importance:
        drift = compute_feature_drift(train_importance, current_importance)
        metrics["feature_drift_count"] = drift
        if drift >= THRESHOLDS["feature_drift_top10_change"]:
            triggers.append(f"Top10特征漂移{drift}个>={THRESHOLDS['feature_drift_top10_change']}")
    else:
        metrics["feature_drift_count"] = None
    
    # 4. 半衰期
    if ic_history and len(ic_history) >= 30:
        hl = estimate_half_life(ic_history)
        metrics["half_life_months"] = hl
        if hl < THRESHOLDS["half_life_warning_months"]:
            triggers.append(f"半衰期={hl}月<{THRESHOLDS['half_life_warning_months']}月")
    else:
        metrics["half_life_months"] = None
    
    metrics["triggered"] = len(triggers) > 0
    metrics["trigger_reason"] = "; ".join(triggers) if triggers else ""
    
    return metrics


def log_health_check(metrics: dict):
    """写入 model_health_log 表"""
    init_model_health_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT INTO model_health_log 
        (check_date, model_id, market, strategy, rank_ic_20d, psi, 
         feature_drift_count, half_life_months, triggered, trigger_reason, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metrics["check_date"], metrics["model_id"], metrics.get("market"), metrics.get("strategy"),
        metrics.get("rank_ic_20d"), metrics.get("psi"),
        metrics.get("feature_drift_count"), metrics.get("half_life_months"),
        int(metrics["triggered"]), metrics["trigger_reason"],
        json.dumps(metrics, ensure_ascii=False, default=str),
    ))
    conn.commit()
    conn.close()


def generate_alert_message(metrics: dict) -> str:
    """生成微信告警消息"""
    if not metrics["triggered"]:
        return ""
    
    msg = f"🔴 模型衰退告警 [{metrics['model_id']}]\n"
    msg += f"原因: {metrics['trigger_reason']}\n"
    if metrics.get("rank_ic_20d"):
        msg += f"Rolling IC(20d): {metrics['rank_ic_20d']:.4f}\n"
    if metrics.get("psi"):
        msg += f"PSI: {metrics['psi']:.4f}\n"
    if metrics.get("half_life_months"):
        msg += f"半衰期: {metrics['half_life_months']}月\n"
    msg += "建议立即触发重训练。"
    return msg


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    # 示例: 从IC目录加载历史
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default="lgb_a_short")
    parser.add_argument("--market", default="a")
    parser.add_argument("--strategy", default="short")
    args = parser.parse_args()
    
    # 尝试加载IC历史
    ic_history = []
    ic_dir = Path(IC_DIR)
    if ic_dir.exists():
        for f in sorted(ic_dir.glob("ic_*.json")):
            try:
                data = json.loads(f.read_text())
                if isinstance(data, dict) and "rank_ic" in data and data["rank_ic"] is not None:
                    # 文件名格式: ic_{market}_{strategy}_s{split}_{date}.json
                    # 提取末尾日期 YYYY-MM-DD
                    stem = f.stem
                    date_match = stem.rsplit("_", 1)[-1] if "_" in stem else stem
                    if len(date_match) == 10 and date_match[4] == "-":
                        ic_history.append({"date": date_match, "rank_ic": float(data["rank_ic"])})
            except Exception:
                pass
    
    metrics = check_model_health(
        model_id=args.model_id, market=args.market, strategy=args.strategy,
        ic_history=ic_history if ic_history else None,
    )
    
    log_health_check(metrics)
    print(f"模型健康 [{args.model_id}]:")
    print(f"  Rolling IC(20d): {metrics.get('rank_ic_20d', 'N/A')}")
    print(f"  PSI: {metrics.get('psi', 'N/A')}")
    print(f"  半衰期: {metrics.get('half_life_months', 'N/A')}月")
    print(f"  触发重训练: {'Yes' if metrics['triggered'] else 'No'}")
    if metrics['triggered']:
        print(f"  原因: {metrics['trigger_reason']}")
        print(generate_alert_message(metrics))
