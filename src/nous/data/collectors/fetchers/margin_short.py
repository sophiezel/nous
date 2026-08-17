"""做空/融券数据采集：AKShare stock_margin_sse() + stock_margin_szse()
写入 screener.db → margin_short_daily 表（含融资+融券全字段）
注: SSE API (query.sse.com.cn) 需要 curl_cffi TLS 伪装绕过 SSL 问题
"""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Monkey-patch: curl_cffi 绕过 SSE SSL 协议不兼容
import requests as _orig_requests
from curl_cffi import requests as _curl_requests
_orig_requests.get = lambda url, **kw: _curl_requests.get(
    url, impersonate='chrome131', timeout=30,
    **{k: v for k, v in kw.items() if k not in ('proxies',)}
)
for k in list(os.environ):
    if 'proxy' in k.lower(): del os.environ[k]

import akshare as ak
import pandas as pd
import sqlite3
from datetime import date, timedelta

from nous.core.paths import screener_db

DB = screener_db()

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS margin_short_daily (
    trade_date TEXT PRIMARY KEY,
    margin_balance REAL,      -- 融资余额(元)
    margin_buy REAL,           -- 融资买入额(元)
    short_balance REAL,        -- 融券余额(元)
    short_volume REAL,         -- 融券余量(股)
    short_sell REAL,           -- 融券卖出量(股)
    total_balance REAL,        -- 融资融券余额(元)
    source TEXT                -- 'sse' / 'szse' / 'combined'
)
"""

def backfill_margin_short():
    """从 AKShare 拉取沪深两市融资融券明细"""
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute(TABLE_DDL)

    all_frames = []
    sources = [
        ('上交所', ak.stock_margin_sse, 'sse'),
    ]

    for label, fn, src in sources:
        try:
            raw = fn()
            raw = raw.rename(columns={
                '信用交易日期': 'trade_date',
                '融资余额': 'margin_balance',
                '融资买入额': 'margin_buy',
                '融券余量': 'short_volume',
                '融券余量金额': 'short_balance',
                '融券卖出量': 'short_sell',
                '融资融券余额': 'total_balance',
            })
            raw['source'] = src
            all_frames.append(raw)
            print(f"[margin] {label}: {len(raw)}条, {raw['trade_date'].min()}~{raw['trade_date'].max()}")
        except Exception as e:
            print(f"[margin] {label}: 失败 - {e}")

    # 补充深交所当天数据
    try:
        szse = ak.stock_margin_szse()
        today = date.today().strftime('%Y-%m-%d')
        szse_row = {
            'trade_date': today,
            'margin_balance': float(szse['融资余额'].iloc[0]) * 1e8 if '融资余额' in szse.columns else None,
            'margin_buy': float(szse['融资买入额'].iloc[0]) * 1e8 if '融资买入额' in szse.columns else None,
            'short_balance': float(szse['融券余额'].iloc[0]) * 1e8 if '融券余额' in szse.columns else None,
            'short_volume': float(szse['融券余量'].iloc[0]) * 1e8 if '融券余量' in szse.columns else None,
            'short_sell': float(szse['融券卖出量'].iloc[0]) * 1e8 if '融券卖出量' in szse.columns else None,
            'total_balance': float(szse['融资融券余额'].iloc[0]) * 1e8 if '融资融券余额' in szse.columns else None,
            'source': 'szse',
        }
        all_frames.append(pd.DataFrame([szse_row]))
        print(f"[margin] 深交所(当天): 1条, {today}")
    except Exception as e:
        print(f"[margin] 深交所(当天): 失败 - {e}")

    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        combined['trade_date'] = pd.to_datetime(combined['trade_date'], format='mixed').dt.strftime('%Y-%m-%d')
        # 同一天两市场求和合并
        agg = combined.groupby('trade_date', as_index=False).agg({
            'margin_balance': 'sum',
            'margin_buy': 'sum',
            'short_balance': 'sum',
            'short_volume': 'sum',
            'short_sell': 'sum',
            'total_balance': 'sum',
        })
        agg['source'] = 'combined'
        # 使用逐行 INSERT OR REPLACE 安全写入，不毁灭历史数据
        cols = list(agg.columns)
        for _, row in agg.iterrows():
            vals = [row[c] for c in cols]
            ph = ','.join(['?' for _ in cols])
            col_n = ','.join(cols)
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO margin_short_daily ({col_n}) VALUES ({ph})",
                    vals
                )
            except Exception:
                pass
        conn.commit()
        print(f"[margin] → 合计 {len(agg)} 条写入 margin_short_daily")
    else:
        print("[margin] 无数据")

    conn.close()


def collect_today():
    """增量采集最近交易日数据（T+1），使用 INSERT OR REPLACE 安全更新"""
    today = date.today()
    # 周末回溯到周五
    target = today
    for _ in range(5):
        if target.weekday() < 5:
            break
        target -= timedelta(days=1)
    target_str = target.isoformat()
    print(f"[margin] target={target_str} (today={today.isoformat()})")

    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA busy_timeout = 30000")
    existing = conn.execute(
        "SELECT COUNT(*) FROM margin_short_daily WHERE trade_date = ?", (target_str,)
    ).fetchone()[0]
    conn.close()

    if existing:
        print(f"[margin] {target_str} 已有数据({existing}条)，仍更新以确保最新")
    backfill_margin_short()


if __name__ == "__main__":
    backfill_margin_short()
