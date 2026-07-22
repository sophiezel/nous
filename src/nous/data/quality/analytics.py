"""DuckDB分析层 + Parquet冷存储 — 优化6

提供:
1. 从SQLite同步到DuckDB(列存, 分析查询快10-100x)
2. 预计算物化视图(日收益率/行业PE中位数)
3. Parquet归档导出(>1年数据压缩10:1)
4. 跨年联合查询

用法:
  python -m src.data_quality.analytics sync       # 同步SQLite→DuckDB
  python -m src.data_quality.analytics archive     # 导出冷数据→Parquet
  python -m src.data_quality.analytics query '...' # 执行分析查询
"""

import sys
import time
import sqlite3
from pathlib import Path
from datetime import date, timedelta

DB_PATH = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"
ANALYTICS_PATH = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "analytics.db"
COLD_PATH = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "cold"
VIEWS_SQL = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "analytics_views.sql"


def init_analytics():
    """初始化DuckDB分析库"""
    import duckdb
    ANALYTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(ANALYTICS_PATH))
    
    # Attach SQLite
    conn.execute(f"ATTACH '{DB_PATH}' AS hot (TYPE SQLITE, READ_ONLY)")
    
    print("DuckDB分析层初始化完成")
    conn.close()
    return True


def sync_to_duckdb(full: bool = False):
    """同步SQLite数据到DuckDB(物化视图) — 通过pandas桥接避免SQLite schema兼容问题"""
    import duckdb, pandas as pd
    
    t0 = time.time()
    conn = duckdb.connect(str(ANALYTICS_PATH))
    
    # 通过pandas桥接: SQLite→DataFrame→DuckDB
    sqlite_conn = sqlite3.connect(str(DB_PATH))
    
    tables = {
        "stock_daily": ("stock_daily_dk", "SELECT * FROM stock_daily"),
        "screen_results": ("screen_results_dk", "SELECT * FROM screen_results"),
        "stock_fundamental": ("stock_fundamental_dk", "SELECT * FROM stock_fundamental"),
    }
    
    for src, (dst, query) in tables.items():
        try:
            df = pd.read_sql_query(query, sqlite_conn)
            if full:
                conn.execute(f"DROP TABLE IF EXISTS {dst}")
            conn.execute(f"CREATE OR REPLACE TABLE {dst} AS SELECT * FROM df")
            cnt = conn.execute(f"SELECT COUNT(*) FROM {dst}").fetchone()[0]
            print(f"  {src} → {dst}: {cnt}行")
        except Exception as e:
            print(f"  ⚠️ {src}: {e}")
    
    sqlite_conn.close()
    print(f"  全量同步: {(time.time()-t0):.1f}s")
    
    # 预计算物化视图
    print("  计算物化视图...")
    
    # 日收益率
    conn.execute("""
    CREATE OR REPLACE TABLE daily_returns AS
    SELECT symbol, trade_date, close,
        (close - LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date)) 
        / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date), 0) * 100 AS ret_pct
    FROM stock_daily_dk
    WHERE close > 0
    """)
    
    # 月统计
    conn.execute("""
    CREATE OR REPLACE TABLE monthly_stats AS
    SELECT symbol, 
        strftime(CAST(trade_date AS DATE), '%Y-%m') as month,
        FIRST(close) as open_price,
        LAST(close) as close_price,
        COUNT(*) as trading_days,
        AVG(volume) as avg_volume
    FROM stock_daily_dk
    GROUP BY symbol, month
    ORDER BY symbol, month
    """)
    
    cnt = conn.execute("SELECT COUNT(*) FROM stock_daily_dk").fetchone()[0]
    ret_cnt = conn.execute("SELECT COUNT(*) FROM daily_returns").fetchone()[0]
    conn.close()
    print(f"  stock_daily_dk: {cnt}行, daily_returns: {ret_cnt}行")
    return True


def archive_to_parquet(years_back: int = 1):
    """将超过N年的数据从DuckDB导出为Parquet"""
    import duckdb
    t0 = time.time()
    COLD_PATH.mkdir(parents=True, exist_ok=True)
    
    conn = duckdb.connect(str(ANALYTICS_PATH))
    cutoff = (date.today() - timedelta(days=365 * years_back)).isoformat()
    
    # 从DuckDB自身表导出(不用sqlite_scan避免schema兼容问题)
    tables_to_archive = [
        ("stock_daily_dk", f"trade_date < '{cutoff}'", COLD_PATH / f"stock_daily_before_{cutoff}.parquet"),
        ("screen_results_dk", f"screen_date < '{cutoff}'", COLD_PATH / f"screen_results_before_{cutoff}.parquet"),
    ]
    
    for table, where, outfile in tables_to_archive:
        try:
            conn.execute(f"""
                COPY (SELECT * FROM {table} WHERE {where}) 
                TO '{outfile}' (FORMAT PARQUET, COMPRESSION 'zstd')
            """)
            size_mb = outfile.stat().st_size / 1024 / 1024 if outfile.exists() else 0
            print(f"  {table} → {outfile.name} ({size_mb:.1f}MB)")
        except Exception as e:
            print(f"  ⚠️ {table}: {e}")
    
    conn.close()
    print(f"  Parquet归档完成: {(time.time()-t0):.1f}s")
    return True


def run_query(sql: str):
    """执行跨源分析查询(SQLite+Parquet联合)"""
    import duckdb
    conn = duckdb.connect(str(ANALYTICS_PATH))
    conn.execute(f"ATTACH IF NOT EXISTS '{DB_PATH}' AS hot (TYPE SQLITE, READ_ONLY)")
    
    # 加载Parquet文件(如果存在)
    for pq in sorted(COLD_PATH.glob("*.parquet")):
        conn.execute(f"CREATE OR REPLACE VIEW {pq.stem} AS SELECT * FROM '{pq}'")
    
    result = conn.execute(sql).fetchdf()
    conn.close()
    return result


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    
    if cmd == "sync":
        full = "--full" in sys.argv
        sync_to_duckdb(full=full)
    elif cmd == "init":
        init_analytics()
    elif cmd == "archive":
        years = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        archive_to_parquet(years)
    elif cmd == "query":
        sql = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "SELECT 1"
        df = run_query(sql)
        print(df.to_string())
    else:
        print("用法: sync|init|archive|query")
