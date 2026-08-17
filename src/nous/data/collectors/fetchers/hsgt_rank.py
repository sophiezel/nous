"""北向/南向资金板块+个股排名采集
板块排行: stock_hsgt_board_rank_em(symbol, indicator)
个股排行: stock_hsgt_hold_stock_em(market, indicator) [沪股通/深股通/港股通]
"""
import sys, os, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak
import pandas as pd
import sqlite3
from datetime import date

from nous.core.paths import screener_db

DB = screener_db()

BOARD_DDL = """
CREATE TABLE IF NOT EXISTS hsgt_board_daily (
    trade_date TEXT,
    board_name TEXT,
    direction TEXT,
    rank_no INTEGER,
    net_inflow REAL,
    change_pct REAL,
    PRIMARY KEY (trade_date, board_name, direction)
)
"""

STOCK_DDL = """
CREATE TABLE IF NOT EXISTS hsgt_stock_daily (
    trade_date TEXT,
    symbol TEXT,
    direction TEXT,
    rank_no INTEGER,
    net_inflow REAL,
    change_pct REAL,
    PRIMARY KEY (trade_date, symbol, direction)
)
"""


def backfill_hsgt_ranks():
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute(BOARD_DDL)
    conn.execute(STOCK_DDL)

    # ── 板块排行 ──
    for label, sym in [
        ('北向板块', '北向资金增持行业板块排行'),
        ('南向板块', '南向资金增持行业板块排行'),
    ]:
        direction = 'north' if '北向' in label else 'south'
        print(f"[hsgt] {label}...")
        try:
            for ind in ['今日', '3日排行', '5日排行', '10日排行']:
                try:
                    df = ak.stock_hsgt_board_rank_em(symbol=sym, indicator=ind)
                    if len(df) == 0:
                        continue
                    df = df.rename(columns={'序号': 'rank_no', '名称': 'board_name',
                                            '最新交易日': 'trade_date', '净买入金额': 'net_inflow',
                                            '涨跌幅': 'change_pct'})
                    df['direction'] = direction
                    df_clean = df[['trade_date','board_name','direction','rank_no','net_inflow','change_pct']].drop_duplicates()
                    df_clean.to_sql('hsgt_board_daily', f'sqlite:///{DB}',
                                    if_exists='append', index=False)
                    print(f"  {ind}: {len(df)}条, latest={df['trade_date'].iloc[0]}")
                    break  # 拿到今日数据就停
                except Exception:
                    continue
            else:
                print(f"  {label}: 所有indicator都失败")
        except Exception as e:
            print(f"  {label}: {e}")
        time.sleep(1)

    # ── 个股排行 (北向持股) ──
    for label, market, direction in [
        ('北向沪股通', '沪股通', 'north'),
        ('北向深股通', '深股通', 'north'),
        ('南向港股通', '港股通', 'south'),
    ]:
        print(f"[hsgt] {label}个股排行...")
        try:
            for ind in ['5日排行', '10日排行', '20日排行']:
                try:
                    df = ak.stock_hsgt_hold_stock_em(market=market, indicator=ind)
                    if len(df) == 0:
                        continue
                    df = df.rename(columns={
                        '序号': 'rank_no', '股票代码': 'symbol',
                        '日期': 'trade_date', '净买入额': 'net_inflow',
                        '涨跌幅': 'change_pct',
                    })
                    df['direction'] = direction
                    df_clean = df[['trade_date','symbol','direction','rank_no','net_inflow','change_pct']].drop_duplicates()
                    df_clean.to_sql('hsgt_stock_daily', f'sqlite:///{DB}',
                                    if_exists='append', index=False)
                    print(f"  {ind}: {len(df)}条")
                    break
                except Exception:
                    continue
            else:
                print(f"  {label}: 所有indicator都失败")
        except Exception as e:
            print(f"  {label}: {e}")
        time.sleep(1)

    conn.commit()
    conn.close()
    print("[hsgt] 回补完成")


def collect_today():
    today = date.today().isoformat()
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA busy_timeout = 30000")
    existing = conn.execute(
        "SELECT COUNT(*) FROM hsgt_board_daily WHERE trade_date = ?", (today,)
    ).fetchone()[0]
    conn.close()
    if existing:
        print(f"[hsgt] {today} 已有数据，跳过")
        return
    backfill_hsgt_ranks()


if __name__ == "__main__":
    backfill_hsgt_ranks()
