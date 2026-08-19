"""另类数据因子: 龙虎榜/北向/融资融券/宏观/情绪/大宗交易

因子命名规则: K8_<name>
  - K8_northbound: 北向资金净买入 (全市场代理)
  - K8_margin: 融资余额变化率
  - K8_sentiment: 市场情绪 (涨停家数 / 涨停溢价)
  - K8_block_trade: 大宗交易折溢价 (个股级别)
  - K8_fund_flow: 资金流向 (待扩展)
  - K8_macro: 宏观指标 (待扩展)

依赖: akshare >= 1.18
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from nous.core.paths import repo_root, screener_db

logger = logging.getLogger(__name__)

# 项目路径
PROJECT_ROOT = repo_root()
DB_PATH = screener_db()

def _query_northbound_db(date_str: str = None, days: int = 3) -> float:
    """从hsgt_daily读取推算北向净买额(亿元), 返回days日累计"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        if date_str:
            rows = conn.execute("""
                SELECT MAX(net_buy) FROM hsgt_daily
                WHERE direction='north' AND net_buy IS NOT NULL AND trade_date <= ?
                GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?
            """, (date_str, days)).fetchall()
        else:
            rows = conn.execute("""
                SELECT MAX(net_buy) FROM hsgt_daily
                WHERE direction='north' AND net_buy IS NOT NULL
                GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?
            """, (days,)).fetchall()
        conn.close()
        if rows and rows[0][0] is not None:
            return sum(r[0] for r in rows if r[0])
    except Exception:
        pass
    return None  # 未命中, 调用方降级akshare

def _query_margin_db(date_str: str = None) -> tuple:
    """从margin_daily读取最新融资余额, 返回(date, balance)"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        if date_str:
            row = conn.execute("""
                SELECT trade_date, margin_balance FROM margin_daily
                WHERE margin_balance IS NOT NULL AND trade_date <= ?
                ORDER BY trade_date DESC LIMIT 2
            """, (date_str,)).fetchall()
        else:
            row = conn.execute("""
                SELECT trade_date, margin_balance FROM margin_daily
                WHERE margin_balance IS NOT NULL ORDER BY trade_date DESC LIMIT 2
            """).fetchall()
        conn.close()
        return row
    except Exception:
        pass
    return []

# ---------------------------------------------------------------------------
# 北向资金因子 (全市场级, 所有股票同值)
# ---------------------------------------------------------------------------

def compute_northbound_factor(date: str) -> float:
    """
    K8_northbound: 北向资金净买入 (5日均值, 亿元)
    优先查本地DB(推算值), 降级akshare
    """
    # 优先DB
    total_5d = _query_northbound_db(date_str=date, days=5)
    if total_5d is not None:
        return total_5d / 5.0  # 5日均值(亿元)

    # 降级akshare
    try:
        import akshare as ak
        df = ak.stock_hsgt_hist_em(symbol='北向资金')
        if df is None or df.empty:
            return 0.0

        df = df[df['日期'] <= date].copy()
        if df.empty:
            return 0.0

        recent = df.head(5)['当日成交净买额'].mean()
        return float(recent) if not pd.isna(recent) else 0.0
    except ImportError:
        logger.warning("akshare 未安装, 北向因子返回 0")
        return 0.0
    except Exception as e:
        logger.debug(f"北向因子计算异常: {e}")
        return 0.0


def compute_northbound_cumulative(date: str, days: int = 20) -> float:
    """
    K8_northbound_cum: 北向资金累计净买入 (days 日累计)
    优先查本地DB, 降级akshare
    """
    total = _query_northbound_db(date_str=date, days=days)
    if total is not None:
        return total
    try:
        import akshare as ak
        df = ak.stock_hsgt_hist_em(symbol='北向资金')
        if df is None or df.empty:
            return 0.0

        df = df[df['日期'] <= date].copy()
        if df.empty:
            return 0.0

        cumulative = df.head(days)['当日成交净买额'].sum()
        return float(cumulative) if not pd.isna(cumulative) else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# 融资融券因子 (市场级)
# ---------------------------------------------------------------------------

def compute_margin_factor(date: str) -> float:
    """
    K8_margin: 融资余额变化率 (日环比)
    优先查本地DB, 降级akshare
    """
    # 优先DB
    rows = _query_margin_db(date_str=date)
    if len(rows) >= 2:
        return rows[0][1] / rows[1][1] - 1.0

    # 降级akshare
    try:
        import akshare as ak
        df = ak.macro_china_market_margin_sh()
        if df is None or df.empty:
            return 0.0

        df = df[df['融资余额'].notna()].copy()
        if len(df) < 2:
            return 0.0

        # 筛选在 date 之前的数据
        df = df[df['日期'] <= date].copy()
        if len(df) < 2:
            return 0.0

        latest = float(df.iloc[-1]['融资余额'])
        prev = float(df.iloc[-2]['融资余额'])
        if prev == 0:
            return 0.0

        return latest / prev - 1.0
    except ImportError:
        logger.warning("akshare 未安装, 融资因子返回 0")
        return 0.0
    except Exception as e:
        logger.debug(f"融资因子计算异常: {e}")
        return 0.0


def compute_margin_ma_ratio(date: str, window: int = 5) -> float:
    """
    K8_margin_ma: 融资余额 / 5日均值 - 1
    优先查本地DB, 降级akshare
    """
    # 优先DB — 取 window+1 条数据, 用最新/均值
    rows = _query_margin_db(date_str=date)
    if rows:
        return rows[0][1] / (sum(r[1] for r in rows) / len(rows)) - 1.0

    # 降级akshare
    try:
        import akshare as ak
        df = ak.macro_china_market_margin_sh()
        if df is None or df.empty:
            return 0.0

        df = df[df['融资余额'].notna()].copy()
        df = df[df['日期'] <= date].copy()
        if len(df) < window:
            return 0.0

        recent = df.head(window)['融资余额'].values.astype(float)
        latest = recent[0]
        ma = recent.mean()
        if ma == 0:
            return 0.0
        return latest / ma - 1.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# 市场情绪因子 (涨停家数)
# ---------------------------------------------------------------------------

def compute_sentiment_factor(date: str) -> float:
    """
    K8_sentiment: 市场情绪因子 [0, 1]

    基于前一日涨停家数 / 100 归一化。
    涨停定义: (close - open) / open > 0.095 (≈10% 涨停板)

    Returns:
        float: 0.0 (极弱) ~ 1.0 (极强)
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))

        # 找到 date 之前的最近交易日
        prev_day = conn.execute(
            "SELECT MAX(trade_date) FROM stock_daily WHERE trade_date < ?",
            (date,)
        ).fetchone()[0]

        if prev_day is None:
            conn.close()
            return 0.5

        # 涨停家数: 当日涨幅 >= 9.5%
        limit_count = conn.execute(
            """SELECT COUNT(*) FROM stock_daily
               WHERE trade_date = ? AND close > open
                 AND (close - open) / open >= 0.095""",
            (prev_day,)
        ).fetchone()[0]

        # 跌停家数
        limit_down_count = conn.execute(
            """SELECT COUNT(*) FROM stock_daily
               WHERE trade_date = ? AND close < open
                 AND (open - close) / open >= 0.095""",
            (prev_day,)
        ).fetchone()[0]

        conn.close()

        total = limit_count + limit_down_count
        if limit_count > 0:
            sentiment = min(1.0, limit_count / max(100.0, total))
        else:
            sentiment = 0.3  # 无涨停 = 情绪偏弱

        return float(sentiment)
    except Exception as e:
        logger.debug(f"情绪因子计算异常: {e}")
        return 0.5


def compute_limitup_ratio(date: str) -> float:
    """
    K8_limitup_ratio: 涨停比例 (涨停家数 / 总交易股票数)
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        prev_day = conn.execute(
            "SELECT MAX(trade_date) FROM stock_daily WHERE trade_date < ?",
            (date,)
        ).fetchone()[0]

        if prev_day is None:
            conn.close()
            return 0.0

        limit_count = conn.execute(
            """SELECT COUNT(*) FROM stock_daily
               WHERE trade_date = ? AND close > open
                 AND (close - open) / open >= 0.095""",
            (prev_day,)
        ).fetchone()[0]

        total = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE trade_date = ?",
            (prev_day,)
        ).fetchone()[0]

        conn.close()

        if total == 0:
            return 0.0
        return min(1.0, limit_count / max(100, total))
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# 大宗交易因子 (个股级别)
# ---------------------------------------------------------------------------

def compute_block_trade_factor(symbol: str, date: str) -> float:
    """
    K8_block_trade: 大宗交易折溢价

    计算当日大宗交易成交均价相对收盘价的折溢价。
    负值 = 折价 (大宗买方以低于收盘价买入)
    正值 = 溢价

    Returns:
        float: 折溢价率, 无数据返回 0
    """
    try:
        import akshare as ak
        # 格式化为 YYYYMMDD
        dt_str = date.replace('-', '')[:8]
        df = ak.stock_dzjy_mrmx(
            symbol=symbol,
            start_date=dt_str,
            end_date=dt_str,
        )
        if df is None or df.empty:
            return 0.0

        # 计算折溢价: 成交价/收盘价 - 1
        avg_price = df['成交价'].mean()
        close_price = df['收盘价'].mean()

        if pd.isna(avg_price) or pd.isna(close_price) or close_price == 0:
            return 0.0

        premium = avg_price / close_price - 1.0
        return float(round(premium, 6))
    except ImportError:
        return 0.0
    except Exception as e:
        logger.debug(f"大宗交易因子 [{symbol}@{date}]: {e}")
        return 0.0


# ---------------------------------------------------------------------------
# 批量计算: 对 DataFrame 增加另类因子列
# ---------------------------------------------------------------------------

def add_alt_data_factors(
    df: pd.DataFrame,
    date_col: str = 'trade_date',
    symbol_col: str = 'symbol',
) -> pd.DataFrame:
    """
    给因子 DataFrame 批量添加 K8_ 另类数据因子列

    Args:
        df: 包含 trade_date 和 symbol 的 DataFrame
        date_col: 日期列名
        symbol_col: 股票代码列名

    Returns:
        新增 K8_* 列的 DataFrame (副本)
    """
    result = df.copy()
    dates = result[date_col].unique()

    # 市场级因子 (每个日期计算一次)
    northbound_cache = {}
    margin_cache = {}
    sentiment_cache = {}
    limitup_cache = {}

    logger.info(f"计算另类数据因子: {len(dates)} 个交易日")

    for dt in dates:
        dt_str = str(dt)
        northbound_cache[dt_str] = compute_northbound_factor(dt_str)
        margin_cache[dt_str] = compute_margin_factor(dt_str)
        sentiment_cache[dt_str] = compute_sentiment_factor(dt_str)
        limitup_cache[dt_str] = compute_limitup_ratio(dt_str)

    # 市场级因子映射
    result['K8_northbound'] = result[date_col].astype(str).map(northbound_cache)
    result['K8_margin'] = result[date_col].astype(str).map(margin_cache)
    result['K8_sentiment'] = result[date_col].astype(str).map(sentiment_cache)
    result['K8_limitup_ratio'] = result[date_col].astype(str).map(limitup_cache)

    # 个股级因子: 大宗交易 (只对部分股票有值)
    logger.info("计算大宗交易因子 (个股级)...")
    block_cache = {}
    # 只对前 500 只股票计算 (akshare 请求有限速)
    symbols = result[symbol_col].unique()[:500]
    for sym in symbols:
        # 取该股票的最新日期
        try:
            sym_dates = result[result[symbol_col] == sym][date_col].unique()
            if len(sym_dates) > 0:
                latest_dt = str(sorted(sym_dates)[-1])
                val = compute_block_trade_factor(sym, latest_dt)
                if val != 0:
                    block_cache[(sym, latest_dt)] = val
        except Exception:
            pass

    # 只有有数据的行才有 K8_block_trade
    result['K8_block_trade'] = 0.0
    for (sym, dt), val in block_cache.items():
        mask = (result[symbol_col] == sym) & (result[date_col].astype(str) == dt)
        result.loc[mask, 'K8_block_trade'] = val

    logger.info(f"另类数据因子计算完成: {len(dates)} 日, {len(symbols)} 股票")
    return result


# ---------------------------------------------------------------------------
# 快速测试
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # 测试各因子
    test_date = "2025-12-01"
    print(f"北向因子 ({test_date}): {compute_northbound_factor(test_date):.2f} 亿")
    print(f"融资因子 ({test_date}): {compute_margin_factor(test_date):.4f}")
    print(f"情绪因子 ({test_date}): {compute_sentiment_factor(test_date):.4f}")
    print(f"涨停比例 ({test_date}): {compute_limitup_ratio(test_date):.4f}")

    # 测试大宗交易
    print(f"大宗交易 600519@{test_date}: {compute_block_trade_factor('600519', test_date):.4f}")
