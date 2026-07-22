"""
因子暴露分析
============
计算组合对各因子的暴露度, 并检测集中度风险.

因子暴露 = Σ(weight_i × factor_loading_i)

告警阈值: 单因子暴露绝对值 > 30%
"""

import pandas as pd
from typing import Dict, List


# ---------------------------------------------------------------------------
# 因子暴露计算
# ---------------------------------------------------------------------------

def compute_factor_exposures(
    holdings: Dict[str, float],
    factor_loadings: pd.DataFrame,
) -> Dict[str, float]:
    """
    计算组合因子暴露

    参数
    ----
    holdings : dict
        {symbol: weight}, 如 {'600519': 0.3, '000858': 0.3, '300750': 0.4}
    factor_loadings : pd.DataFrame
        index = symbol, columns = 因子名, values = 因子载荷

    返回
    ----
    dict
        {因子名: 暴露值}
    """
    exposures: Dict[str, float] = {}
    for factor in factor_loadings.columns:
        exp = sum(
            holdings.get(sym, 0.0) * float(factor_loadings.loc[sym, factor])
            for sym in holdings
            if sym in factor_loadings.index
        )
        exposures[factor] = round(float(exp), 4)
    return exposures


# ---------------------------------------------------------------------------
# 因子集中度检测
# ---------------------------------------------------------------------------

def check_concentration(
    exposures: Dict[str, float],
    threshold: float = 0.30,
) -> List[str]:
    """
    检测因子集中度风险

    参数
    ----
    exposures : dict
        来自 compute_factor_exposures 的返回
    threshold : float, default=0.30
        暴露绝对值告警阈值 (30%)

    返回
    ----
    list[str]
        告警消息列表, 空列表表示无告警
    """
    alerts: List[str] = []
    for factor, exp in exposures.items():
        if abs(exp) > threshold:
            alerts.append(
                f"[因子集中] {factor}: {exp:.2%} (阈值: {threshold:.0%})"
            )
    return alerts


# ---------------------------------------------------------------------------
# 辅助: 因子载荷表生成 (演示用)
# ---------------------------------------------------------------------------

def make_dummy_factor_loadings(symbols, factors=None):
    """
    生成随机因子载荷 (仅供测试/演示)

    参数
    ----
    symbols : list of str
    factors : list of str, default=None

    返回
    ----
    pd.DataFrame
    """
    import numpy as np

    if factors is None:
        factors = ["动量", "价值", "质量", "波动率", "成长"]

    np.random.seed(42)
    data = np.random.randn(len(symbols), len(factors)) * 0.15
    return pd.DataFrame(data, index=symbols, columns=factors)
