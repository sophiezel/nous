"""
VaR/CVaR 风险度量
=================
支持方法:
  - historical: 历史模拟法 (默认, 无分布假设)
  - parametric: 参数法 (假设正态分布)

数值单位: 人民币
"""

import numpy as np
import pandas as pd
from pathlib import Path
import sqlite3

DB = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

def compute_var(returns, confidence=0.95, method="historical"):
    """
    VaR: Value at Risk (在险价值)

    参数
    ----
    returns : array-like
        收益率序列 (小数, 如 0.01 表示 1%)
    confidence : float, default=0.95
        置信水平, 如 0.95 / 0.99
    method : str, default='historical'
        - 'historical' : 历史模拟法 (分位数, 无分布假设)
        - 'parametric'  : 参数法 (假设收益率服从正态分布)

    返回
    ----
    float
        VaR 值 (负值表示损失). 例: -0.0234 表示 2.34% 的潜在损失
    """
    returns = np.asarray(returns, dtype=float)

    if method == "historical":
        # 历史模拟法: 直接取收益率的 (1-confidence) 分位数
        return np.percentile(returns, (1 - confidence) * 100)

    elif method == "parametric":
        # 参数法: 假设收益率服从正态分布
        from scipy.stats import norm
        mu = returns.mean()
        sigma = returns.std(ddof=1)
        return float(mu + sigma * norm.ppf(1 - confidence))

    else:
        raise ValueError(f"未知方法: {method}, 可选: historical / parametric")


def compute_cvar(returns, confidence=0.95):
    """
    CVaR (Expected Shortfall): 条件风险价值 / 预期亏损
    计算超过 VaR 的尾部损失的均值

    参数
    ----
    returns : array-like
    confidence : float, default=0.95

    返回
    ----
    float
    """
    returns = np.asarray(returns, dtype=float)
    var = compute_var(returns, confidence, method="historical")
    tail = returns[returns <= var]
    if len(tail) == 0:
        return float(var)  # 无尾部观测时退回 VaR
    return float(tail.mean())


# ES 别名 + FRTB 标准
compute_expected_shortfall = compute_cvar


def compute_es_frtb(returns):
    """FRTB 标准的 Expected Shortfall (97.5% 置信水平)。

    Basel III FRTB 规定内部模型法使用 97.5% ES 替代 99% VaR。
    """
    return compute_expected_shortfall(returns, confidence=0.975)


def portfolio_es(holdings, confidence=0.975, lookback=60):
    """组合 Expected Shortfall (默认 FRTB 97.5%)。

    调用 portfolio_var 并返回 ES + VaR 对比。
    """
    result = portfolio_var(holdings, confidence=confidence, lookback=lookback)
    return {
        "es": result.get(f"cvar_{int(confidence*100)}", result.get("cvar_95")),
        "var": result.get(f"var_{int(confidence*100)}", result.get("var_95")),
        "confidence": confidence,
        "mdd": result.get("mdd"),
    }


# ---------------------------------------------------------------------------
# 回撤分析
# ---------------------------------------------------------------------------

def compute_max_drawdown(nav_series):
    """
    最大回撤 (Maximum Drawdown) 与回撤发生日期

    参数
    ----
    nav_series : pd.Series
        净值序列 (累计收益率 + 1)

    返回
    ----
    dict
        {'mdd': float, 'mdd_date': str}
        - mdd: 最大回撤比例 (负值)
        - mdd_date: 回撤最大时对应的日期
    """
    if not isinstance(nav_series, pd.Series):
        nav_series = pd.Series(nav_series)

    cummax = nav_series.cummax()
    drawdown = (nav_series - cummax) / cummax
    mdd = float(drawdown.min())

    # 回撤幅度最大的时间点
    mdd_idx = drawdown.idxmin()
    mdd_date = str(mdd_idx)[:10] if mdd_idx is not None else "N/A"

    return {"mdd": mdd, "mdd_date": mdd_date}


def compute_drawdown_duration(nav_series):
    """
    回撤恢复时间 (从峰值到回到峰值所需天数)

    返回 dict: {max_duration_days, avg_duration_days}
    """
    if not isinstance(nav_series, pd.Series):
        nav_series = pd.Series(nav_series)

    cummax = nav_series.cummax()
    in_drawdown = nav_series < cummax

    # 简单统计: 最大连续回撤天数
    durations = []
    current = 0
    for flag in in_drawdown.values:
        if flag:
            current += 1
        else:
            if current > 0:
                durations.append(current)
                current = 0
    if current > 0:
        durations.append(current)

    return {
        "max_drawdown_days": max(durations) if durations else 0,
        "avg_drawdown_days": round(sum(durations) / len(durations), 1) if durations else 0,
    }


# ---------------------------------------------------------------------------
# 组合 VaR / CVaR（基于历史模拟法）
# ---------------------------------------------------------------------------

def portfolio_var(holdings, confidence=0.95, lookback=60):
    """
    组合 VaR / CVaR / 最大回撤
    使用历史模拟法, 从 stock_daily 中拉取历史收盘价计算收益

    参数
    ----
    holdings : dict
        {symbol: weight}, 权重之和应为 1.0
    confidence : float, default=0.95
    lookback : int, default=60
        回溯交易日数 (至少 2 天)

    返回
    ----
    dict
        {
            'var_95':  float,
            'var_99':  float,
            'cvar_95': float,
            'mdd':     dict
        }
    """
    conn = sqlite3.connect(str(DB))

    # 确定日期窗口
    end_date = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily"
    ).fetchone()[0]

    # 取 lookback + 1 个交易日作为起点 (需要差分)
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date <= ? "
        "ORDER BY trade_date DESC LIMIT ?",
        (end_date, lookback + 1)
    ).fetchall()
    if len(rows) < lookback + 1:
        start_date = rows[-1][0] if rows else end_date
    else:
        start_date = rows[-1][0]

    # 拉取每个持仓的收盘价
    all_returns = []
    for sym, w in holdings.items():
        rows = conn.execute(
            "SELECT close FROM stock_daily WHERE symbol=? AND trade_date >= ? "
            "ORDER BY trade_date",
            (sym, start_date)
        ).fetchall()
        prices = [r[0] for r in rows]
        if len(prices) >= 2:
            rets = np.diff(prices) / prices[:-1]
            all_returns.append(w * np.array(rets, dtype=float))
        else:
            # 数据不足时跳过该标的
            pass

    conn.close()

    if not all_returns:
        return {"var_95": 0.0, "var_99": 0.0, "cvar_95": 0.0, "mdd": {"mdd": 0.0, "mdd_date": "N/A"}}

    # 对齐长度: 取所有序列的最小长度
    min_len = min(len(r) for r in all_returns)
    aligned = [r[-min_len:] for r in all_returns]
    portfolio_returns = np.sum(aligned, axis=0)  # 加权和

    # 计算净值序列用于回撤
    nav = pd.Series(np.cumprod(1 + portfolio_returns))

    return {
        "var_95": round(float(compute_var(portfolio_returns, 0.95)), 4),
        "var_99": round(float(compute_var(portfolio_returns, 0.99)), 4),
        "cvar_95": round(float(compute_cvar(portfolio_returns, 0.95)), 4),
        "mdd": compute_max_drawdown(nav),
    }
