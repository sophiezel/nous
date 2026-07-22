"""龙虎榜数据采集：AKShare Eastmoney → SQLite (curl_cffi TLS)"""
from __future__ import annotations

import sys, time, random
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import akshare as ak
from nous.data.collectors.fetchers.base_tls import BROWSER_UA
from nous.data import storage

# 让 AKShare 底层使用浏览器的 User-Agent（通过 monkey-patch requests.utils）
import requests as _requests
_requests.utils.default_user_agent = lambda: BROWSER_UA


def fetch_lhb_daily(trade_date: str) -> list[dict]:
    """采集单日龙虎榜数据（Eastmoney源，T+1）。
    trade_date: 'YYYYMMDD'
    返回标准化 dict 列表。
    """
    try:
        df = ak.stock_lhb_detail_em(start_date=trade_date, end_date=trade_date)
    except Exception as e:
        print(f"  [LHB] EM source failed: {e}, trying Sina fallback...")
        df = _fetch_lhb_sina(trade_date)

    if df is None or len(df) == 0:
        print(f"  [LHB] {trade_date}: no data")
        return []

    rows = []
    for _, r in df.iterrows():
        try:
            symbol = str(r.get("代码", "")).zfill(6)
            if not symbol or symbol == "nan":
                continue
            rows.append({
                "trade_date": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
                "symbol": symbol,
                "name": str(r.get("名称", "")),
                "close": _safe_float(r.get("收盘价")),
                "pct_change": _safe_float(r.get("涨跌幅")),
                "turnover_rate": _safe_float(r.get("换手率")),
                "l_buy": _safe_float(r.get("龙虎榜买入额")),
                "l_sell": _safe_float(r.get("龙虎榜卖出额")),
                "net_amount": _safe_float(r.get("龙虎榜净买额")),
                "amount": _safe_float(r.get("龙虎榜成交额")),
                "reason": str(r.get("上榜原因", "")),
            })
        except Exception:
            continue

    return rows


def _fetch_lhb_sina(trade_date: str):
    """Sina fallback"""
    try:
        return ak.stock_lhb_detail_daily_sina(date=trade_date)
    except Exception:
        return None


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return None if (f != f or abs(f) > 1e20) else f  # NaN check
    except (ValueError, TypeError):
        return None


def collect_today():
    """增量采集：最近交易日龙虎榜数据（T+1，跳过周末）"""
    yesterday = date.today() - timedelta(days=1)
    # 周末回溯到周五
    for _ in range(5):
        if yesterday.weekday() < 5:
            break
        yesterday -= timedelta(days=1)
    target = yesterday.strftime("%Y%m%d")
    print(f"[LHB] Fetching {target} (today={date.today().isoformat()})...")
    rows = fetch_lhb_daily(target)
    if rows:
        n = storage.save_lhb_daily(rows)
        print(f"[LHB] Saved {n}/{len(rows)} rows for {target}")
    else:
        print(f"[LHB] No data for {target} (T+1, 数据可能尚未发布)")
    return len(rows)


def backfill(start_date: str, end_date: str | None = None):
    """回补历史龙虎榜数据。
    start_date: 'YYYYMMDD'
    end_date: 'YYYYMMDD' (default: today)
    """
    if end_date is None:
        end_date = date.today().strftime("%Y%m%d")

    d = date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:8]))
    end = date(int(end_date[:4]), int(end_date[4:6]), int(end_date[6:8]))

    total_saved = 0
    while d <= end:
        ds = d.strftime("%Y%m%d")
        # 跳过周末
        if d.weekday() < 5:
            rows = fetch_lhb_daily(ds)
            if rows:
                n = storage.save_lhb_daily(rows)
                total_saved += n
                print(f"[LHB] {ds}: saved {n} rows (total {total_saved})")
            else:
                print(f"[LHB] {ds}: 0 rows")
            time.sleep(0.5)  # 防止限流
        d += timedelta(days=1)

    print(f"[LHB] Backfill complete. Total saved: {total_saved}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--backfill", nargs="?", const="20250101", help="Start date YYYYMMDD")
    p.add_argument("--date", help="Single date YYYYMMDD")
    args = p.parse_args()

    if args.date:
        rows = fetch_lhb_daily(args.date)
        print(f"Fetched {len(rows)} rows")
        if rows:
            storage.save_lhb_daily(rows)
            print("Saved.")
    elif args.backfill:
        backfill(args.backfill)
    else:
        collect_today()
