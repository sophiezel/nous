"""screener.db → Backtrader DataFeed 桥接模块

支持:
- 幸存者偏差消除: 自动获取区间内所有可交易股票 (含退市)
- 滚动窗口回测: 每个窗口独立获取股票池
"""
import backtrader as bt
import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

from nous.engine.backtest.survivorship import (
    get_survivorship_free_universe,
    filter_symbols_in_date_range,
)

DB_PATH = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"


def get_backtrader_data(
    symbols: Optional[list] = None,
    start: str = None,
    end: str = None,
    auto_universe: bool = False,
    min_data_days: int = 50,
) -> dict:
    """从 screener.db 加载日线数据, 返回 {symbol: bt.feeds.PandasData} 字典.

    Args:
        symbols: 股票代码列表, 如 ['000001', '600519']。
                 若为 None 且 auto_universe=True, 则自动获取区间内所有股票。
        start: 起始日期 'YYYY-MM-DD', None 表示不限
        end: 截止日期 'YYYY-MM-DD', None 表示不限
        auto_universe: 若 True 且 symbols 为 None, 自动使用
                       get_survivorship_free_universe() 获取完整股票池
        min_data_days: 最少需要多少条日线才纳入 (默认 50)

    Returns:
        {symbol: bt.feeds.PandasData} — 只有数据量 > min_data_days 的才被包含
    """
    # 幸存者偏差消除: 自动获取完整股票池
    if symbols is None and auto_universe and start and end:
        symbols = get_survivorship_free_universe(start, end, min_days=1)
        if not symbols:
            print("[警告] auto_universe 未找到任何股票, 请检查 start/end 范围")
            return {}
        print(f"[幸存者偏差消除] 自动加载 {len(symbols)} 只候选股票 (含退市)")

    if not symbols:
        return {}

    conn = sqlite3.connect(str(DB_PATH))

    clauses = [f"symbol IN ({','.join(repr(s) for s in symbols)})"]
    if start:
        clauses.append(f"trade_date >= '{start}'")
    if end:
        clauses.append(f"trade_date <= '{end}'")

    where = " AND ".join(clauses)
    sql = (
        "SELECT symbol, trade_date, open, high, low, close, volume "
        f"FROM stock_daily_all WHERE {where} ORDER BY trade_date"
    )

    df = pd.read_sql_query(sql, conn, parse_dates=["trade_date"])
    conn.close()

    # 统一 datetime 类型 (Backtrader 要求 datetime, 不能有 date)
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"])

    feeds = {}
    for sym in symbols:
        sym_df = df[df["symbol"] == sym] \
            .set_index("trade_date") \
            .sort_index()
        # 确保索引是 DatetimeIndex (Backtrader PandasData 需要)
        if not isinstance(sym_df.index, pd.DatetimeIndex):
            sym_df.index = pd.to_datetime(sym_df.index)
        # 确保列顺序符合 PandasData 默认映射
        sym_df = sym_df[["open", "high", "low", "close", "volume"]]
        if len(sym_df) >= min_data_days:
            data = bt.feeds.PandasData(dataname=sym_df)
            feeds[sym] = data
    return feeds


def get_fundamental_data(symbols: list) -> dict:
    """从 stock_fundamental 表读取 PE / ROE 快照数据.

    Returns:
        {symbol: {'pe': float, 'roe': float}}
    """
    conn = sqlite3.connect(str(DB_PATH))
    placeholders = ",".join(repr(s) for s in symbols)
    df = pd.read_sql_query(
        f"SELECT symbol, pe, roe FROM stock_fundamental WHERE symbol IN ({placeholders})",
        conn,
    )
    conn.close()
    result = {}
    for _, row in df.iterrows():
        result[row["symbol"]] = {"pe": row["pe"], "roe": row["roe"]}
    return result
