"""
模拟盘盈亏 + 绩效归因
- 绝对收益: 年化收益率/累计收益/最大回撤/Calmar Ratio
- 风险调整: 夏普比率/Sortino Ratio/信息比率(IR)
- 交易统计: 胜率/盈亏比/换手率/平均持仓天数
- Brinson归因: 配置效应 vs 选股效应
"""

import json
import logging
from pathlib import Path
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
import sqlite3

logger = logging.getLogger(__name__)

EVAL_DIR = data_dir() / "evaluation"
from nous.core.paths import data_dir, screener_db
DB_PATH = screener_db()
def compute_performance_metrics(
    daily_pnl: list[dict],
    trades: list[dict],
    market: str = "a",
    benchmark_returns: list[float] = None,
    initial_capital: float = 1_000_000,
    trading_days_per_year: int = 242,
) -> dict:
    """
    计算完整绩效指标。
    
    Args:
        daily_pnl: [{"date": str, "pnl": float, "equity": float}]
        trades: [{"entry_date","exit_date","symbol","entry_price","exit_price","shares","pnl","pnl_pct"}]
        market: 'a' or 'hk'
        benchmark_returns: 基准日收益率序列
        initial_capital: 初始资金
        trading_days_per_year: 年交易日数(港股247, A股242)
    """
    results = {}
    
    if not daily_pnl:
        return {"error": "无每日盈亏数据"}
    
    df = pd.DataFrame(daily_pnl)
    df = df.sort_values("date")
    df["equity"] = df["equity"].astype(float)
    
    n_days = len(df)
    results["trading_days"] = n_days
    results["date_range"] = f"{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}"
    
    # 1. 绝对收益
    total_return = (df["equity"].iloc[-1] / initial_capital - 1)
    results["total_return"] = round(total_return, 4)
    results["annualized_return"] = round((1 + total_return) ** (trading_days_per_year / n_days) - 1, 4) if n_days > 0 else 0
    
    # 2. 最大回撤
    cummax = df["equity"].cummax()
    drawdowns = (df["equity"] - cummax) / cummax
    max_drawdown = drawdowns.min()
    results["max_drawdown"] = round(float(max_drawdown), 4)
    
    # 3. Calmar Ratio
    if max_drawdown != 0:
        results["calmar_ratio"] = round(results["annualized_return"] / abs(max_drawdown), 4)
    else:
        results["calmar_ratio"] = None
    
    # 4. 日收益率
    df["daily_return"] = df["equity"].pct_change()
    daily_returns = df["daily_return"].dropna()
    
    if len(daily_returns) > 0:
        # 夏普比率 (假设无风险利率=2%)
        rf_daily = 0.02 / trading_days_per_year
        excess = daily_returns - rf_daily
        sharpe = np.sqrt(trading_days_per_year) * excess.mean() / excess.std() if excess.std() > 0 else 0
        results["sharpe_ratio"] = round(float(sharpe), 4)
        
        # Sortino Ratio (下行波动率)
        downside = daily_returns[daily_returns < 0]
        if len(downside) > 0 and downside.std() > 0:
            sortino = np.sqrt(trading_days_per_year) * excess.mean() / downside.std()
            results["sortino_ratio"] = round(float(sortino), 4)
        else:
            results["sortino_ratio"] = None
        
        # 波动率
        results["volatility"] = round(float(daily_returns.std() * np.sqrt(trading_days_per_year)), 4)
    
    # 5. 信息比率 (vs 基准)
    if benchmark_returns and len(benchmark_returns) == len(daily_returns):
        bm = pd.Series(benchmark_returns, index=daily_returns.index)
        alpha = daily_returns - bm
        tracking_error = alpha.std()
        if tracking_error > 0:
            ir = np.sqrt(trading_days_per_year) * alpha.mean() / tracking_error
            results["information_ratio"] = round(float(ir), 4)
    else:
        results["information_ratio"] = None
    
    # 6. 交易统计
    if trades and len(trades) > 0:
        trades_df = pd.DataFrame(trades)
        wins = trades_df[trades_df["pnl"] > 0]
        losses = trades_df[trades_df["pnl"] < 0]
        
        results["total_trades"] = len(trades_df)
        results["win_rate"] = round(len(wins) / len(trades_df), 4)
        
        avg_win = wins["pnl"].mean() if len(wins) > 0 else 0
        avg_loss = abs(losses["pnl"].mean()) if len(losses) > 0 else 0
        results["avg_win"] = round(float(avg_win), 2)
        results["avg_loss"] = round(float(avg_loss), 2)
        results["profit_factor"] = round(float(avg_win / avg_loss), 4) if avg_loss > 0 else None
        
        # Max win/loss
        results["max_win"] = round(float(trades_df["pnl"].max()), 2) if len(trades_df) > 0 else 0
        results["max_loss"] = round(float(trades_df["pnl"].min()), 2) if len(trades_df) > 0 else 0
        
        # 平均持仓天数
        if "entry_date" in trades_df.columns and "exit_date" in trades_df.columns:
            trades_df["entry_dt"] = pd.to_datetime(trades_df["entry_date"])
            trades_df["exit_dt"] = pd.to_datetime(trades_df["exit_date"])
            trades_df["holding_days"] = (trades_df["exit_dt"] - trades_df["entry_dt"]).dt.days
            results["avg_holding_days"] = round(float(trades_df["holding_days"].mean()), 1)
    
    results["initial_capital"] = initial_capital
    results["final_equity"] = round(float(df["equity"].iloc[-1]), 2)
    results["market"] = market
    
    return results


def compute_performance_from_db(market: str = "a") -> dict:
    """从 sim_trades / sim_pnl_snapshot 表读取并计算绩效"""
    conn = sqlite3.connect(str(DB_PATH))
    
    # 每日净值
    pnl_df = pd.read_sql_query(
        "SELECT date, equity, pnl FROM sim_pnl_snapshot ORDER BY date", conn
    )
    daily_pnl = pnl_df.to_dict("records") if len(pnl_df) > 0 else []
    
    # 交易记录
    trades_df = pd.read_sql_query(
        "SELECT * FROM sim_trades ORDER BY entry_date", conn
    )
    trades = trades_df.to_dict("records") if len(trades_df) > 0 else []
    
    conn.close()
    
    initial_capital = 1_000_000
    if daily_pnl and len(daily_pnl) > 0:
        initial_capital = daily_pnl[0].get("equity", 1_000_000)
    
    return compute_performance_metrics(daily_pnl, trades, market=market, 
                                       initial_capital=initial_capital)


# === Brinson 绩效归因 ===

def brinson_attribution(
    portfolio_returns: list[float],
    portfolio_weights: list[list[float]],  # [日期][板块]
    benchmark_returns: list[float],
    benchmark_weights: list[list[float]],
    sector_returns: list[list[float]],    # [日期][板块]
) -> dict:
    """
    Brinson 三因素绩效归因:
    - 配置效应 (Allocation): 超配/低配板块带来的收益
    - 选股效应 (Selection): 在同板块内选股带来的收益
    - 交互效应 (Interaction): 交叉项
    """
    n_periods = len(portfolio_returns)
    n_sectors = len(sector_returns[0]) if sector_returns else 0
    
    if n_periods == 0 or n_sectors == 0:
        return {"error": "数据不足"}
    
    allocation = 0
    selection = 0
    interaction = 0
    
    for t in range(n_periods):
        for s in range(n_sectors):
            pw = portfolio_weights[t][s]
            bw = benchmark_weights[t][s]
            pr = portfolio_returns[t]
            sr = sector_returns[t][s]
            br = benchmark_returns[t]
            
            # Brinson-Fachler 公式
            allocation += (pw - bw) * (sr - br)
            selection += bw * (pr - sr)
            interaction += (pw - bw) * (pr - sr)
    
    return {
        "allocation_effect": round(float(allocation), 6),    # 配置效应
        "selection_effect": round(float(selection), 6),       # 选股效应
        "interaction_effect": round(float(interaction), 6),   # 交互效应
        "total_active_return": round(float(allocation + selection + interaction), 6),
    }


def save_performance_report(metrics: dict, name: str = "performance"):
    """保存绩效报告"""
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = EVAL_DIR / f"{name}_{today}.json"
    with open(path, "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"绩效报告: {path}")
    return str(path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["a", "hk"], default="a")
    args = parser.parse_args()
    
    metrics = compute_performance_from_db(market=args.market)
    
    if "error" in metrics:
        print(f"ERROR: {metrics['error']}")
    else:
        print(f"绩效归因 ({args.market}):")
        print(f"  交易天数: {metrics.get('trading_days', 0)}")
        print(f"  总收益: {metrics.get('total_return', 0):.2%}")
        print(f"  年化收益: {metrics.get('annualized_return', 0):.2%}")
        print(f"  最大回撤: {metrics.get('max_drawdown', 0):.2%}")
        print(f"  夏普比率: {metrics.get('sharpe_ratio', 'N/A')}")
        print(f"  Calmar: {metrics.get('calmar_ratio', 'N/A')}")
        print(f"  信息比率: {metrics.get('information_ratio', 'N/A')}")
        print(f"  交易次数: {metrics.get('total_trades', 0)}")
        print(f"  胜率: {metrics.get('win_rate', 0):.1%}")
        print(f"  盈亏比: {metrics.get('profit_factor', 'N/A')}")
        
        save_performance_report(metrics)
