"""大宗交易数据采集：AKShare → SQLite (curl_cffi TLS)"""
from __future__ import annotations

import sys, time, random
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import akshare as ak
from nous.data.collectors.fetchers.base_tls import BROWSER_UA
from nous.data import storage

import requests as _requests
_requests.utils.default_user_agent = lambda: BROWSER_UA


def fetch_block_trades(start_date: str, end_date: str) -> list[dict]:
    """采集大宗交易明细。
    start_date/end_date: 'YYYYMMDD'
    """
    try:
        df = ak.stock_dzjy_mrmx(symbol="A股", start_date=start_date, end_date=end_date)
    except Exception as e:
        print(f"  [BLOCK] AKShare failed: {e}")
        return []

    if df is None or len(df) == 0:
        return []

    rows = []
    for _, r in df.iterrows():
        try:
            symbol = str(r.get("证券代码", "")).zfill(6)
            if not symbol or symbol == "nan" or len(symbol) > 6:
                continue
            rows.append({
                "trade_date": str(r.get("交易日期", ""))[:10],
                "symbol": symbol,
                "name": str(r.get("证券简称", "")),
                "price": _f(r.get("成交价")),
                "volume": _f(r.get("成交量")),
                "amount": _f(r.get("成交额")),
                "premium": _f(r.get("折溢率")),
                "buyer": str(r.get("买方营业部", "")),
                "seller": str(r.get("卖方营业部", "")),
            })
        except Exception:
            continue

    return rows


def _f(v) -> float | None:
    try:
        f = float(v)
        return None if (f != f or abs(f) > 1e20) else f
    except (ValueError, TypeError):
        return None


def collect_today():
    """增量采集：最近交易日大宗交易（T+1，跳过周末）"""
    yesterday = date.today() - timedelta(days=1)
    for _ in range(5):
        if yesterday.weekday() < 5:
            break
        yesterday -= timedelta(days=1)
    target = yesterday.strftime("%Y%m%d")
    print(f"[BLOCK] Fetching {target} (today={date.today().isoformat()})...")
    rows = fetch_block_trades(target, target)
    if rows:
        n = storage.save_block_trades(rows)
        print(f"[BLOCK] Saved {n}/{len(rows)} rows for {target}")
    else:
        print(f"[BLOCK] No data for {target} (T+1, 数据可能尚未发布)")
    return len(rows)


def backfill(start_date: str, end_date: str | None = None):
    """回补历史大宗交易。按月批量拉取减少API调用。"""
    if end_date is None:
        end_date = date.today().strftime("%Y%m%d")

    d = date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:8]))
    end = date(int(end_date[:4]), int(end_date[4:6]), int(end_date[6:8]))

    total = 0
    # Batch by month to reduce API calls
    while d <= end:
        # End of current month
        if d.month == 12:
            month_end = d.replace(year=d.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            month_end = d.replace(month=d.month + 1, day=1) - timedelta(days=1)
        if month_end > end:
            month_end = end

        ds = d.strftime("%Y%m%d")
        de = month_end.strftime("%Y%m%d")
        print(f"[BLOCK] {ds} ~ {de}...")
        rows = fetch_block_trades(ds, de)
        if rows:
            n = storage.save_block_trades(rows)
            total += n
            print(f"[BLOCK]   saved {n} rows (total {total})")
        time.sleep(1)
        d = month_end + timedelta(days=1)

    print(f"[BLOCK] Backfill complete. Total: {total}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--backfill", nargs="?", const="20250101")
    p.add_argument("--date")
    args = p.parse_args()
    if args.date:
        rows = fetch_block_trades(args.date, args.date)
        print(f"Fetched {len(rows)} rows")
        if rows:
            storage.save_block_trades(rows)
    elif args.backfill:
        backfill(args.backfill)
    else:
        collect_today()
