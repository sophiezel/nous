"""南向资金个股采集器 — 基于akshare stock_hsgt_individual_em

数据字段:
- 持股日期 / 持股数量 / 持股市值 / 持股市值变化(-1d/-5d/-10d)
- 写入 hsgt_stock_daily 表 (扩展字段)

用法:
  python southbound_collector.py              # 全量采集(仅活跃标的)
  python southbound_collector.py --symbol 00700  # 单只
"""

import sys, os, time, sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import akshare as ak
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

DB_PATH = Path.home() / "code/stock-screener/data/screener.db"

# 港股通活跃标的(从stock_basic获取, 过滤掉非港股通)
def get_hk_connect_universe() -> list[str]:
    """获取港股通标的列表"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # 优先从hk_connect_universe表读取
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM hk_connect_universe"
        ).fetchall()
        if rows and len(rows) > 50:
            return [r[0] for r in rows]
        # 回退: 从stock_basic取所有港股
        rows = conn.execute(
            "SELECT symbol FROM stock_basic WHERE market='hk'"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def fetch_southbound_single(symbol: str) -> list[dict]:
    """获取单只标的的南向持股数据
    
    Returns:
        [{'trade_date': '2026-05-21', 'symbol': '00700',
          'hold_shares': 1067776014, 'hold_value': 4.68e11,
          'change_1d': -1.7e10, 'change_5d': -2.1e10, 'change_10d': -4.6e10}, ...]
    """
    try:
        df = ak.stock_hsgt_individual_em(symbol=symbol)
        if df is None or df.empty:
            return []
        
        results = []
        for _, row in df.iterrows():
            try:
                results.append({
                    'trade_date': str(row['持股日期'])[:10],
                    'symbol': symbol,
                    'close': float(row.get('当日收盘价', 0) or 0),
                    'change_pct': float(row.get('当日涨跌幅', 0) or 0),
                    'hold_shares': int(row.get('持股数量', 0) or 0),
                    'hold_value': float(row.get('持股市值', 0) or 0),
                    'hold_ratio': float(row.get('持股数量占A股百分比', 0) or 0),
                    'change_1d': float(row.get('持股市值变化-1日', 0) or 0),
                    'change_5d': float(row.get('持股市值变化-5日', 0) or 0),
                    'change_10d': float(row.get('持股市值变化-10日', 0) or 0),
                })
            except Exception:
                continue
        return results
    except Exception as e:
        print(f"  ⚠️ {symbol}: {e}")
        return []


def sync_southbound_all(dry_run: bool = False, max_symbols: int = None, incremental: bool = False):
    """全量/增量同步南向持股数据到DB
    
    Args:
        incremental: True时只更新数据落后的标的(最新日期超过1天前)
    """
    symbols = get_hk_connect_universe()
    if max_symbols:
        symbols = symbols[:max_symbols]
    
    # 增量模式: 先查各标的最新日期, 跳过已更新的
    skip_set = set()
    if incremental:
        conn_chk = sqlite3.connect(str(DB_PATH))
        rows = conn_chk.execute("""
            SELECT symbol, MAX(trade_date) as latest 
            FROM hsgt_stock_daily WHERE direction='南向' 
            GROUP BY symbol
        """).fetchall()
        # 用数据库全局最新交易日期做阈值(而非日历日期)
        global_max = conn_chk.execute(
            "SELECT MAX(trade_date) FROM hsgt_stock_daily WHERE direction='南向'"
        ).fetchone()[0] or '2000-01-01'
        conn_chk.close()
        skip_set = {r[0] for r in rows if r[1] >= global_max}
        symbols = [s for s in symbols if s not in skip_set]
        print(f"[南向采集] 增量模式: 全局最新={global_max}, {len(skip_set)}只已更新, 需采集{len(symbols)}只")
    else:
        print(f"[南向采集] 全量模式: {len(symbols)}只")
    
    if dry_run:
        # 只测试前3只
        for sym in symbols[:3]:
            data = fetch_southbound_single(sym)
            print(f"  {sym}: {len(data)} records")
            if data:
                print(f"    Latest: {data[-1]['trade_date']}, hold={data[-1]['hold_shares']:,}, change_1d={data[-1]['change_1d']/1e8:.1f}亿")
        return
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=30000")
    
    TIME_BUDGET = 540  # 9分钟预算(600s cron超时留60s安全边际)
    start_time = time.time()
    
    # 确保表存在并扩展字段
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hsgt_stock_daily (
            trade_date TEXT, symbol TEXT, direction TEXT,
            net_inflow REAL, change_pct REAL,
            estimated_net_buy REAL, estimated_net_buy_direction TEXT,
            holding_market_cap REAL, holding_pct REAL,
            confidence TEXT, industry TEXT, name TEXT,
            PRIMARY KEY (trade_date, symbol, direction)
        );
    """)
    
    # 尝试添加新列(如果不存在)
    for col, col_type in [
        ('hold_shares', 'INTEGER'),
        ('hold_value', 'REAL'),
        ('change_1d', 'REAL'),
        ('change_5d', 'REAL'),
        ('change_10d', 'REAL'),
    ]:
        try:
            conn.execute(f"ALTER TABLE hsgt_stock_daily ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # 列已存在
    
    total_inserted = 0
    failed = 0
    
    for i, sym in enumerate(symbols):
        elapsed = time.time() - start_time
        if elapsed > TIME_BUDGET:
            remaining = len(symbols) - i
            print(f"  耗时{elapsed:.0f}s > {TIME_BUDGET}s预算, 暂停, 剩余{remaining}只下次继续")
            break
        
        if i > 0 and i % 20 == 0:
            print(f"  进度: {i}/{len(symbols)}, 写入{total_inserted}条, 失败{failed}, 耗时{elapsed:.0f}s")
            time.sleep(0.5)  # 限流
        
        data = fetch_southbound_single(sym)
        if not data:
            failed += 1
            continue
        
        for d in data:
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO hsgt_stock_daily 
                    (trade_date, symbol, direction, net_inflow, change_pct,
                     holding_market_cap, holding_pct,
                     hold_shares, hold_value, change_1d, change_5d, change_10d)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    d['trade_date'], d['symbol'], '南向',
                    d['change_1d'], d['change_pct'],
                    d['hold_value'], d['hold_ratio'],
                    d['hold_shares'], d['hold_value'],
                    d['change_1d'], d['change_5d'], d['change_10d']
                ))
                total_inserted += 1
            except Exception as e:
                pass
        
        time.sleep(0.3)  # per-symbol delay
    
    conn.commit()
    conn.close()
    print(f"[南向采集] 完成: {total_inserted}条, 失败{failed}只")
    
    # 交叉验证: 个股汇总 vs 大盘总计
    _cross_validate_southbound()


def _cross_validate_southbound():
    """多源交叉验证: 个股南向汇总 vs 大盘南向总计"""
    try:
        import akshare as ak
        import warnings; warnings.filterwarnings('ignore')
        
        # 源A: 个股汇总 (从DB)
        conn = sqlite3.connect(str(DB_PATH))
        today = date.today().isoformat()
        yesterday = conn.execute(
            "SELECT trade_date FROM hsgt_stock_daily WHERE direction='南向' ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        if not yesterday:
            conn.close()
            return
        trade_date = yesterday[0]
        
        total_change = conn.execute(
            "SELECT SUM(change_1d) FROM hsgt_stock_daily WHERE direction='南向' AND trade_date=?",
            (trade_date,)
        ).fetchone()[0] or 0
        conn.close()
        
        # 源B: 大盘总计 (akshare)
        df = ak.stock_hsgt_hist_em(symbol='南向资金')
        macro_row = df[df['日期'] == trade_date]
        if macro_row.empty:
            print(f"  [交叉验证] 大盘数据无{trade_date}记录")
            return
        
        macro_net = float(macro_row.iloc[0]['当日成交净买额']) * 1e8  # 亿元→元
        
        if abs(macro_net) > 1e6:  # 避免除零
            divergence = abs(total_change - macro_net) / abs(macro_net)
            flag = "✅" if divergence < 0.3 else "⚠️"
            print(f"  [交叉验证] {trade_date}: 个股汇总={total_change/1e8:.1f}亿 vs 大盘={macro_net/1e8:.1f}亿, 差异={divergence*100:.1f}% {flag}")
    except Exception as e:
        print(f"  [交叉验证] 跳过: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="南向资金个股采集")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbol", type=str)
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--incremental", action="store_true", help="增量模式: 只更新已超时的标的")
    args = parser.parse_args()
    
    if args.symbol:
        data = fetch_southbound_single(args.symbol)
        print(f"{args.symbol}: {len(data)} records")
    else:
        sync_southbound_all(dry_run=args.dry_run, max_symbols=args.max, incremental=args.incremental)
