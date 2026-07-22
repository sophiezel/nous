#!/usr/bin/env python3
"""
futures_basis — 股指期货基差计算

从新浪财经获取 IF/IC/IH/IM 期货实时价格，基于最新数据计算：
  - 基差 = 期货价格 - 现货指数
  - 基差率 = 基差 / 现货指数
  - 年化基差率 = 基差率 × (365 / 到期天数)

写入 futures_basis 表，PRIMARY KEY (trade_date, symbol)。

数据源:
  - 期货实时价: Sina hq.sinajs.cn (IF00, IC00, IH00, IM00)
  - 现货指数: akshare stock_zh_index_daily

自愈: resilient_fetch + CircuitBreaker + 指数退避重试 + 优雅降级
看门狗: heartbeat('futures_basis')

单独运行:
    python -m src.collectors.futures_basis
"""

import sys
import os
import time
import re
import sqlite3
from datetime import date, datetime, timedelta

# ── 确保可以从 src 导入 ─────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import requests
import pandas as pd

from nous.data.collectors import resilient_fetch, heartbeat, CircuitBreaker
from nous.data.storage import get_db

# ── 配置 ────────────────────────────────────────────

# 合约 → (期货代码, 现货指数代码(Sina), 指数名(用于查akshare))
# 现货指数: sh000300=沪深300, sh000905=中证500, sh000016=上证50, sh000852=中证1000
FUTURES_CONFIG = {
    'IF': {'futures_code': 'IF00', 'index_code': 'sh000300', 'index_name': '沪深300'},
    'IC': {'futures_code': 'IC00', 'index_code': 'sh000905', 'index_name': '中证500'},
    'IH': {'futures_code': 'IH00', 'index_code': 'sh000016', 'index_name': '上证50'},
    'IM': {'futures_code': 'IM00', 'index_code': 'sh000852', 'index_name': '中证1000'},
}

SINA_FUTURES_URL = "https://hq.sinajs.cn/list=IF00,IC00,IH00,IM00"
SINA_HEADERS = {
    'Referer': 'https://finance.sina.com.cn',
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
}

# 期货到期日近似计算: 每月的第三个星期五
# 如果当月已过，取下个月
DEFAULT_DAYS_TO_EXPIRY = 30  # 默认到期天数（当无法精确计算时）

# ── 表 DDL ──────────────────────────────────────────

DDL_FUTURES_BASIS = """
CREATE TABLE IF NOT EXISTS futures_basis (
    trade_date      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    futures_price   REAL,
    spot_index      REAL,
    basis           REAL,
    basis_rate      REAL,
    annualized_basis REAL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, symbol)
);
"""

# ── 到期日计算 ──────────────────────────────────────

def third_friday_of_month(year: int, month: int) -> date:
    """计算某月第三个星期五的日期。"""
    # 当月第一天
    first_day = date(year, month, 1)
    # 第一个星期五的偏移 (星期五=4)
    # weekday(): Monday=0, Sunday=6
    days_to_first_friday = (4 - first_day.weekday()) % 7
    first_friday = first_day + timedelta(days=days_to_first_friday)
    # 第三个星期五 = 第一个星期五 + 14天
    return first_friday + timedelta(weeks=2)


def days_to_expiry(trade_date: date) -> int:
    """计算从 trade_date 到最近期货到期日的天数。
    
    使用当月第三个星期五作为到期日，如果已过则用下个月。
    """
    expiry = third_friday_of_month(trade_date.year, trade_date.month)
    if expiry <= trade_date:
        # 当月到期日已过，取下个月
        if trade_date.month == 12:
            expiry = third_friday_of_month(trade_date.year + 1, 1)
        else:
            expiry = third_friday_of_month(trade_date.year, trade_date.month + 1)
    return (expiry - trade_date).days


# ── 数据采集 ────────────────────────────────────────

def fetch_futures_prices() -> dict[str, float]:
    """从新浪财经获取期货实时价格。
    
    Sina 期货行情格式（逗号分隔）:
      0: 合约名称 (e.g. "IF当月连续")
      1: 开盘价
      2: 昨结算
      3: 最新价 (当前价)
      4: 最高价
      5: 最低价
      ...

    Returns:
        {symbol: price} 如 {'IF': 4579.8, 'IC': ...}
    """
    def _fetch():
        resp = requests.get(SINA_FUTURES_URL, headers=SINA_HEADERS, timeout=15)
        resp.encoding = 'gbk'
        return resp.text

    result, status = resilient_fetch(
        'sina', _fetch,
        fallback_fn=lambda: '',
        max_retries=3, base_delay=1.0,
    )

    if not status.get('success'):
        print(f"  [futures] Sina期货获取失败: {status.get('error', 'unknown')}", file=sys.stderr)
        return {}

    text = result
    prices = {}

    for symbol_key, config in FUTURES_CONFIG.items():
        futures_code = config['futures_code']
        # 解析: var hq_str_IF00="data...";
        pattern = rf'hq_str_{futures_code}="([^"]*)"'
        match = re.search(pattern, text)
        if not match:
            print(f"  [futures] {futures_code} 数据未找到 (可能为空)", file=sys.stderr)
            continue

        data = match.group(1)
        if not data.strip():
            print(f"  [futures] {futures_code} 数据为空 (非交易时段)", file=sys.stderr)
            continue

        fields = data.split(',')
        if len(fields) < 4:
            print(f"  [futures] {futures_code} 数据字段不足 ({len(fields)})", file=sys.stderr)
            continue

        try:
            # 字段3为最新价 (当前价)
            price = float(fields[3])
            prices[symbol_key] = price
        except (ValueError, IndexError) as e:
            print(f"  [futures] {futures_code} 价格解析失败: {e}", file=sys.stderr)
            continue

    return prices


def fetch_spot_index(symbol: str) -> pd.DataFrame:
    """从 akshare 获取指数日线数据。
    
    Args:
        symbol: 指数代码，如 'sh000300'
    
    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    def _fetch():
        import akshare
        return akshare.stock_zh_index_daily(symbol=symbol)

    result, status = resilient_fetch(
        'akshare', _fetch,
        fallback_fn=lambda: pd.DataFrame(),
        max_retries=3, base_delay=1.0,
    )

    if not status.get('success') or result is None:
        print(f"  [futures] 指数 {symbol} 获取失败: {status.get('error', 'unknown')}", file=sys.stderr)
        return pd.DataFrame()

    if status.get('fallback_used'):
        print(f"  [futures] 指数 {symbol} 使用降级数据", file=sys.stderr)

    return result


def get_latest_close(index_df: pd.DataFrame) -> tuple[float, str]:
    """从指数日线DataFrame中获取最新收盘价和对应日期。
    
    Returns:
        (close_price, date_str) 或 (0.0, '')
    """
    if index_df is None or index_df.empty:
        return 0.0, ''

    try:
        last = index_df.iloc[-1]
        if 'close' in index_df.columns and 'date' in index_df.columns:
            close_val = float(last['close'])
            date_val = str(last['date'])
            return close_val, date_val
    except (IndexError, ValueError, TypeError) as e:
        print(f"  [futures] 指数收盘价解析失败: {e}", file=sys.stderr)

    return 0.0, ''


# ── 基差计算 ────────────────────────────────────────

def compute_basis(futures_price: float, spot_price: float, days_to_exp: int) -> dict:
    """计算基差指标。
    
    Returns:
        {basis, basis_rate, annualized_basis}
    """
    basis = futures_price - spot_price
    basis_rate = basis / spot_price if spot_price > 0 else 0.0
    # 年化基差率 = 基差率 × (365 / 到期天数)
    annualized = basis_rate * (365 / days_to_exp) if days_to_exp > 0 else 0.0

    return {
        'basis': round(basis, 2),
        'basis_rate': round(basis_rate, 6),
        'annualized_basis': round(annualized, 6),
    }


# ── 写入 DB ─────────────────────────────────────────

def write_futures_basis(conn: sqlite3.Connection, rows: list[dict]):
    """批量写入 futures_basis 表。"""
    conn.executemany("""
        INSERT OR REPLACE INTO futures_basis
        (trade_date, symbol, futures_price, spot_index, basis,
         basis_rate, annualized_basis, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, [
        (
            r['trade_date'],
            r['symbol'],
            r['futures_price'],
            r['spot_index'],
            r['basis'],
            r['basis_rate'],
            r['annualized_basis'],
        )
        for r in rows
    ])
    conn.commit()
    print(f"  [futures] 写入 futures_basis: {len(rows)} 条记录", file=sys.stderr)


# ── 采集主函数 ──────────────────────────────────────

def collect() -> bool:
    """执行一次完整基差采集。返回 True 表示成功。"""
    today = date.today()
    trade_date_str = today.strftime('%Y-%m-%d')
    print(f"  [futures] ===== {datetime.now().strftime('%H:%M:%S')} 股指期货基差采集 =====",
          file=sys.stderr)

    # 1. 获取期货价格
    futures_prices = fetch_futures_prices()
    if not futures_prices:
        print(f"  [futures] DEGRADED: 期货价格全部获取失败", file=sys.stderr)

    # 2. 计算到期天数
    exp_days = days_to_expiry(today)
    print(f"  [futures] 到期天数: {exp_days} (今日={today})", file=sys.stderr)

    # 3. 对每个合约，获取对应现货指数并计算基差
    rows = []
    all_failed = True

    for symbol_key, config in FUTURES_CONFIG.items():
        futures_price = futures_prices.get(symbol_key)
        if futures_price is None:
            print(f"  [futures] {symbol_key} 无期货价格，跳过", file=sys.stderr)
            continue

        index_code = config['index_code']
        index_name = config['index_name']

        # 获取现货指数
        index_df = fetch_spot_index(index_code)
        spot_price, spot_date = get_latest_close(index_df)

        if spot_price <= 0:
            print(f"  [futures] {symbol_key}({index_name}) 现货指数获取失败，使用0填充",
                  file=sys.stderr)
            spot_price = 0.0

        # 计算基差
        basis_data = compute_basis(futures_price, spot_price, exp_days)

        row = {
            'trade_date': trade_date_str,
            'symbol': symbol_key,
            'futures_price': futures_price,
            'spot_index': spot_price,
            'basis': basis_data['basis'],
            'basis_rate': basis_data['basis_rate'],
            'annualized_basis': basis_data['annualized_basis'],
        }
        rows.append(row)
        all_failed = False

        print(f"  [futures] {symbol_key}({index_name}): 期货={futures_price} "
              f"现货={spot_price} 基差={basis_data['basis']:+.2f} "
              f"年化={basis_data['annualized_basis']:+.4f}", file=sys.stderr)

    if all_failed:
        print(f"  [futures] DEGRADED: 所有合约数据均获取失败", file=sys.stderr)

    # 4. 写入数据库
    try:
        conn = get_db(write=True)
        try:
            conn.executescript(DDL_FUTURES_BASIS)
            if rows:
                write_futures_basis(conn, rows)
            else:
                print(f"  [futures] 无数据写入", file=sys.stderr)

            heartbeat('futures_basis')
            return bool(rows)
        finally:
            conn.close()
    except Exception as e:
        print(f"  [futures] DB写入失败: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ── 主入口 ──────────────────────────────────────────

def main():
    """单次采集入口（独立运行时调用）。"""
    print(f"[futures_basis] 股指期货基差采集开始", file=sys.stderr)
    print(f"[futures_basis] 交易日期: {date.today()}", file=sys.stderr)

    success = collect()

    if success:
        print(f"[futures_basis] 采集完成 ✓", file=sys.stderr)
        sys.exit(0)
    else:
        # 休市/非交易时间无数据是正常情况，不应报错
        print(f"[futures_basis] 无数据 (休市/非交易时间正常)", file=sys.stderr)
        sys.exit(0)


if __name__ == '__main__':
    main()
