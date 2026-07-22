"""批量回填历史日线数据 — akshare stock_zh_a_hist

对全部5200+只A股，回填2020-01-01至2026-07-10的日线数据。
使用akshare的stock_zh_a_hist，每次只下载一只股票，限制并发避免被封。
"""

import sys, time, logging
from pathlib import Path
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill")

DB = Path.home() / "nous-data" / "screener.db"

def backfill_stock_daily(
    start: str = "20200101",
    end: str = "20260710",
    limit: int = 0,
    market: str = "a",
):
    import akshare as ak
    import sqlite3
    
    conn = sqlite3.connect(str(DB))
    
    # Get symbols to backfill
    symbols = conn.execute(
        "SELECT symbol FROM stock_basic WHERE market=? ORDER BY symbol", (market,)
    ).fetchall()
    symbols = [r[0] for r in symbols]
    
    if limit > 0:
        symbols = symbols[:limit]
    
    total = len(symbols)
    success = 0
    fail = 0
    t0 = time.time()
    
    log.info(f"回填 {total} 只股票, {start} → {end}")
    
    for i, symbol in enumerate(symbols):
        try:
            # Check if already have sufficient data
            existing = conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE symbol=? AND trade_date>=? AND trade_date<=?",
                (symbol, start[:4]+"-"+start[4:6]+"-"+start[6:], end[:4]+"-"+end[4:6]+"-"+end[6:])
            ).fetchone()[0]
            
            if existing > 200:
                success += 1
                if success % 500 == 0:
                    elapsed = time.time() - t0
                    rate = success / elapsed if elapsed > 0 else 0
                    log.info(f"  skip {success}/{total} ({rate:.0f}/s) existing={existing}")
                continue
            
            # Fetch from akshare
            df = ak.stock_zh_a_hist(
                symbol=symbol, period="daily",
                start_date=start, end_date=end, adjust="qfq"
            )
            
            if df is None or df.empty:
                fail += 1
                continue
            
            # Insert
            count = 0
            for _, row in df.iterrows():
                conn.execute(
                    """INSERT OR REPLACE INTO stock_daily(symbol,trade_date,open,high,low,close,volume,amount)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (symbol, str(row["日期"])[:10], row["开盘"], row["最高"],
                     row["最低"], row["收盘"], row["成交量"], row.get("成交额", 0)),
                )
                count += 1
            
            success += 1
            
            if success % 100 == 0:
                conn.commit()
                elapsed = time.time() - t0
                rate = success / elapsed if elapsed > 0 else 0
                remaining = (total - success) / rate if rate > 0 else 0
                log.info(f"  {success}/{total} ({rate:.0f}/s) est {remaining:.0f}s left (last={symbol} +{count}rows)")
            
            # Rate limiting
            time.sleep(0.1)
            
        except Exception as e:
            fail += 1
            if fail % 50 == 1:
                log.warning(f"  fail[{fail}] {symbol}: {type(e).__name__}")
    
    conn.commit()
    elapsed = time.time() - t0
    log.info(f"DONE: {success} ok, {fail} fail, {elapsed:.0f}s")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20200101")
    p.add_argument("--end", default="20260710")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--market", default="a")
    args = p.parse_args()
    backfill_stock_daily(start=args.start, end=args.end, limit=args.limit, market=args.market)
