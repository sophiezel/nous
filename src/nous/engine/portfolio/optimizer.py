"""组合优化: HRP 风险平价 + 均值方差"""
import numpy as np
import pandas as pd


def optimize_hrp(prices_df: pd.DataFrame) -> dict:
    """
    HRP (层次风险平价) — 不依赖预期收益, 只做风险分散。

    Args:
        prices_df: columns=symbols, index=dates, values=close price

    Returns:
        {symbol: weight}
    """
    from pypfopt import hierarchical_portfolio
    import scipy.cluster.hierarchy as sch

    # SciPy ≥1.16 removed sch._LINKAGE_METHODS; PyPortfolioOpt still references it.
    if not hasattr(sch, "_LINKAGE_METHODS"):
        sch._LINKAGE_METHODS = (
            "single", "complete", "average", "weighted",
            "centroid", "median", "ward",
        )

    returns = prices_df.pct_change().dropna()
    hrp = hierarchical_portfolio.HRPOpt(returns)
    hrp.optimize()
    weights = hrp.clean_weights()

    return {k: round(v, 4) for k, v in weights.items() if v > 0.001}


def optimize_max_sharpe(prices_df: pd.DataFrame, risk_free_rate: float = 0.02) -> dict:
    """
    均值方差优化 — 最大化夏普比率。

    Args:
        prices_df: columns=symbols, index=dates, values=close price

    Returns:
        {symbol: weight}
    """
    from pypfopt import risk_models, expected_returns, EfficientFrontier

    mu = expected_returns.mean_historical_return(prices_df)
    S = risk_models.sample_cov(prices_df)

    ef = EfficientFrontier(mu, S)
    weights = ef.max_sharpe(risk_free_rate=risk_free_rate)
    cleaned = ef.clean_weights()

    return {k: round(v, 4) for k, v in cleaned.items() if v > 0.001}


def apply_constraints(weights: dict, max_single: float = 0.15, max_iter: int = 50) -> dict:
    """
    施加 A 股特有约束: 单票 <= max_single

    迭代裁剪-再分配算法:
    1. 将超过 max_single 的权重裁剪到上限, 收集溢出
    2. 将溢出按比例分配给仍低于上限的股票
    3. 重复直到所有权重都在上限内, 或所有股票都达到上限

    当 n * max_single < 1.0 时, 剩余部分视为现金/未分配,
    这在 A 股实盘中是合理的 (证券账户总有部分现金)。

    Args:
        weights: {symbol: weight} 原始权重
        max_single: 单只股票最大权重
        max_iter: 最大迭代次数

    Returns:
        {symbol: weight} 约束后的权重
    """
    w = {k: float(v) for k, v in weights.items()}

    for _ in range(max_iter):
        # 裁剪超限的符号
        overflow = 0.0
        anything_clipped = False
        for sym in list(w.keys()):
            if w[sym] > max_single:
                overflow += w[sym] - max_single
                w[sym] = max_single
                anything_clipped = True

        if not anything_clipped:
            break  # 所有权重在限制内

        # 将溢出分配给仍低于上限的符号
        below_cap = {sym: v for sym, v in w.items() if v < max_single}
        total_below = sum(below_cap.values())

        if total_below > 0:
            for sym in below_cap:
                w[sym] += overflow * (below_cap[sym] / total_below)
        else:
            # 所有符号都达到上限, 无法再分配, 结束
            break

    return {k: round(v, 4) for k, v in w.items()}


def get_prices_from_db(symbols: list[str], days: int = 60) -> pd.DataFrame:
    """从 screener.db 获取收盘价矩阵（分区日线 + 热表尾）。"""
    import sqlite3
    from datetime import date, timedelta

    from nous.data.storage import get_db
    from nous.data.storage.daily_bars import daily_relation_sql

    conn = get_db(write=False)
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=int(days * 1.6) + 14)).isoformat()
    rel = daily_relation_sql(start, end, conn=conn)
    placeholders = ",".join("?" for _ in symbols)
    df = pd.read_sql_query(
        f"SELECT trade_date, symbol, close FROM {rel} "
        f"WHERE symbol IN ({placeholders}) AND trade_date >= ? ORDER BY trade_date",
        conn,
        params=[*symbols, start],
        parse_dates=["trade_date"],
    )
    conn.close()

    if df.empty:
        return pd.DataFrame()

    pivot = df.pivot(index="trade_date", columns="symbol", values="close")
    pivot = pivot.ffill().dropna(axis=1, how="all")
    return pivot.tail(days)
