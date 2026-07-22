#!/usr/bin/env python3
"""export_slim_dump.py — 导出核心表到压缩SQL，ECS冷启动重建用"""

import sqlite3, gzip, sys, os
from datetime import datetime, timedelta

SRC_DB = os.path.expanduser("~/code/stock-screener/data/screener.db")
OUTPUT = "/tmp/ecs_full_v2.sql.gz"
CORE_DAYS, RT_DAYS = 90, 3
CORE = (datetime.now() - timedelta(days=CORE_DAYS)).strftime("%Y-%m-%d")
RT = (datetime.now() - timedelta(days=RT_DAYS)).strftime("%Y-%m-%d")
FUT = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

print(f"[export] CORE={CORE} RT={RT} FUT={FUT}")

src = sqlite3.connect(SRC_DB)
src.row_factory = sqlite3.Row
total_rows = 0
sql_lines = []

def dump(name, where=None):
    global total_rows
    s = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=? AND sql IS NOT NULL", [name]).fetchone()
    if not s:
        print(f"  [skip] {name}: not found")
        return 0
    sql_lines.append(f"-- Table: {name}")
    sql_lines.append(f"{s['sql']};")
    for row in src.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", [name]):
        sql_lines.append(f"{row['sql']};")
    
    cols = [c[1] for c in src.execute(f"PRAGMA table_info({name})").fetchall()]
    if not cols:
        return 0
    cl = ", ".join(f'"{c}"' for c in cols)
    q = f"SELECT * FROM {name}"
    if where:
        q += f" WHERE {where}"
    rows = src.execute(q).fetchall()
    if not rows:
        print(f"  [ok] {name}: 0 rows")
        return 0
    for row in rows:
        vals = []
        for c in cols:
            v = row[c]
            if v is None:
                vals.append("NULL")
            elif isinstance(v, (int, float)):
                vals.append(str(v))
            else:
                escaped = str(v).replace("'", "''")
                vals.append("'" + escaped + "'")
        sql_lines.append(f"INSERT OR REPLACE INTO {name} ({cl}) VALUES ({', '.join(vals)});")
    total_rows += len(rows)
    print(f"  [ok] {name}: {len(rows)} rows")
    return len(rows)

# Phase 1: Core tables (90 days)
print("\n=== Core (90d) ===")
dump("stock_daily", f"trade_date >= '{CORE}'")
dump("stock_basic")
dump("index_daily", f"trade_date >= '{CORE}'")
dump("screen_results", f"screen_date >= '{CORE}'")
dump("sentiment_cache", f"date >= '{CORE}'")
# recommendation_history uses entry_date
dump("recommendation_history", f"entry_date >= '{CORE}'")

# Phase 2: Realtime tables (3 days)
print("\n=== Realtime (3d) ===")
dump("intraday_minute", f"datetime >= '{RT}'")
dump("market_breadth_snapshot", f"datetime >= '{RT}'")
dump("northbound_intraday", f"datetime >= '{RT}'")

# Phase 3: Fund/Margin/Event tables (90 days)
print("\n=== Fund/Margin/Event (90d) ===")
dump("hsgt_daily", f"trade_date >= '{CORE}'")
dump("hsgt_stock_daily", f"trade_date >= '{CORE}'")
dump("margin_daily", f"trade_date >= '{CORE}'")
dump("lhb_daily", f"trade_date >= '{CORE}'")
dump("block_trades", f"trade_date >= '{CORE}'")
dump("fund_flow_stock", f"trade_date >= '{CORE}'")
dump("limit_up_sentiment", f"trade_date >= '{CORE}'")
dump("limit_up_premium", f"trade_date >= '{CORE}'")

# Phase 4: Futures + Macro (30 days)
print("\n=== Futures+Macro (30d) ===")
dump("futures_daily", f"trade_date >= '{FUT}'")
dump("futures_basis", f"trade_date >= '{FUT}'")
# Macro tables have Chinese column names
dump("macro_pmi")   # "月份" — just dump all (small)
dump("macro_cpi", f"trade_date >= '{FUT}'")
dump("macro_ppi", f"trade_date >= '{FUT}'")
dump("macro_m2")     # "月份" — small table
dump("macro_shibor") # "日期" — small
dump("macro_lpr")    # "TRADE_DATE" — small
dump("macro_gdp")    # "日期" — small

# Phase 5: Quant signals (30 days)
print("\n=== Quant (30d) ===")
dump("quant_signals", f"trade_date >= '{FUT}'")
dump("quant_factor_importance", f"trade_date >= '{FUT}'")

# Phase 6: Historical partitions (full)
print("\n=== Historical partitions ===")
for y in range(2015, 2025):
    dump(f"stock_daily_{y}")

# Phase 7: Fundamental
print("\n=== Fundamental ===")
dump("stock_fundamental")
dump("stock_fundamental_snapshots", f"snapshot_date >= '{CORE}'")

# Phase 8: Other
print("\n=== Other ===")
dump("index_global_daily", f"trade_date >= '{CORE}'")
dump("etf_flow_daily", f"trade_date >= '{CORE}'")
dump("stock_fund_flow", f"trade_date >= '{CORE}'")
dump("hsgt_sector_daily", f"trade_date >= '{CORE}'")

src.close()

sql_text = "\n".join(sql_lines)
print(f"\n[export] Total: {total_rows} rows, {len(sql_text):,} chars")

with gzip.open(OUTPUT, 'wt', encoding='utf-8') as f:
    f.write(sql_text)

mb = os.path.getsize(OUTPUT) / (1024*1024)
print(f"[export] {OUTPUT} ({mb:.1f} MB) DONE")
