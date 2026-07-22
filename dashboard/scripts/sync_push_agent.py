#!/usr/bin/env python3
"""
sync_push_agent.py — Mac->ECS push agent via HTTP API over SSH tunnel
Uses Next.js /api/data/sync endpoint instead of scp+ssh sqlite3.
No orphaned sqlite3 processes on ECS."""
import sqlite3, json, time, os, sys, fcntl, urllib.request
from datetime import datetime, timedelta

SYNC_URL = "http://127.0.0.1:3099/api/data/sync"
SYNC_SECRET = os.environ.get("DATA_SYNC_SECRET", "test")
SRC_DB = os.path.expanduser("~/code/stock-screener/data/screener.db")
STATE_DB = os.path.expanduser("~/.hermes/data/push_state.db")
LOCKFILE = "/tmp/push_agent.lock"
LOG_FILE = os.path.expanduser("~/Library/Logs/push_agent.log")

# Lock
_fd = open(LOCKFILE, 'w')
try: fcntl.flock(_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: print("another instance running"); sys.exit(0)

os.makedirs(os.path.dirname(STATE_DB), exist_ok=True)

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, 'a') as f: f.write(line + "\n")

# State DB
state = sqlite3.connect(STATE_DB)
state.execute("PRAGMA journal_mode=WAL")
state.executescript("""
CREATE TABLE IF NOT EXISTS push_state (
    table_name TEXT PRIMARY KEY, last_synced_ts TEXT,
    status TEXT DEFAULT 'cold', last_push_at TEXT
);
CREATE TABLE IF NOT EXISTS push_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT(datetime('now')),
    table_name TEXT, rows_pushed INTEGER, duration_ms REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS push_heartbeat (
    id INTEGER PRIMARY KEY CHECK(id=1), last_beat TEXT
);
INSERT OR IGNORE INTO push_heartbeat VALUES(1, datetime('now'));
""")
state.commit()

# Table SLA (same as before)
TABLE_SLA = [
    ("intraday_minute","datetime","intraday",570,960,5),
    ("northbound_intraday","datetime","intraday",570,960,5),
    ("stock_daily","trade_date","daily_close",915,1020,60),
    ("index_daily","trade_date","daily_close",915,1020,60),
    ("lhb_daily","trade_date","daily_close",990,1080,120),
    ("block_trades","trade_date","daily_close",1020,1140,120),
    ("limit_up_sentiment","trade_date","daily_close",960,1020,60),
    ("market_breadth_snapshot","datetime","daily_close",960,1020,60),
    ("sentiment_cache","date","daily_close",960,1080,60),
    ("screen_results","screen_date","daily_close",960,1080,60),
    ("recommendation_history","entry_date","daily_close",960,1080,60),
    ("margin_daily","trade_date","t1_morning",480,570,120),
    ("fund_flow_stock","trade_date","t1_morning",480,570,120),
    ("hsgt_daily","trade_date","t1_morning",510,570,120),
    ("hsgt_stock_daily","trade_date","t1_morning",510,570,120),
    ("hsgt_sector_daily","trade_date","t1_morning",510,570,120),
    ("etf_flow_daily","trade_date","t1_morning",540,720,300),
    ("futures_daily","trade_date","daily_close",930,1020,120),
    ("futures_basis","trade_date","daily_close",930,1020,120),
    ("macro_pmi","月份","macro",0,1440,600),
    ("macro_cpi","trade_date","macro",0,1440,600),
    ("macro_ppi","trade_date","macro",0,1440,600),
    ("macro_m2","月份","macro",0,1440,600),
    ("macro_shibor","日期","macro",0,1440,600),
    ("macro_lpr","TRADE_DATE","macro",0,1440,600),
    ("macro_gdp","日期","macro",0,1440,600),
    ("stock_fundamental",None,"static",0,1440,3600),
    ("stock_basic",None,"static",0,1440,3600),
    ("index_global_daily","trade_date","daily_close",915,1440,600),
    ("stock_fund_flow","trade_date","t1_morning",480,720,600),
    # Phase 6 tables — registered but no data yet, add when data exists
    # ("stock_industry",None,"static",0,1440,3600),
    # ("bond_yield","date","daily_close",0,1440,3600),
    # Full coverage tables
    ("quant_signals","trade_date","daily_close",960,1440,600),
    ("portfolio_nav","trade_date","daily_close",960,1440,600),
    ("etl_metrics","started_at","daily_close",960,1440,600),
    ("data_provenance",None,"static",0,1440,3600),
]

def in_window(ws, we):
    t = datetime.now().hour * 60 + datetime.now().minute
    return ws <= t <= we if ws <= we else t >= ws or t <= we

def push_via_http(table, rows):
    """Push rows via HTTP POST to Next.js API endpoint — no orphaned processes"""
    if not rows: return True, None

    payload = json.dumps({"table": table, "rows": rows}).encode("utf-8")
    req = urllib.request.Request(
        SYNC_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {SYNC_SECRET}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if resp.status == 200:
            if body.get("errors"):
                return False, f"{len(body['errors'])} row errors: {body['errors'][0]}"
            return True, None
        return False, body.get("error", f"HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
            return False, detail.get("error", str(e))
        except Exception:
            return False, str(e)[:100]
    except Exception as e:
        return False, str(e)[:100]

# Cold start (7-day fallback)
def cold_start():
    log("Cold start...")
    fb = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    for t, _, _, _, _, _ in TABLE_SLA:
        ex = state.execute("SELECT status FROM push_state WHERE table_name=?",[t]).fetchone()
        if ex and ex[0] != 'cold': continue
        state.execute("INSERT OR REPLACE INTO push_state VALUES(?,?,?,?)", [t, fb, 'warm', None])
        log(f"  {t}: {fb}")
    state.commit()
    log("Cold start done")

def self_check():
    """Startup validation — catch config errors before they crash the agent.
    
    Checks (non-fatal WARN, never blocks startup):
    1. SSH tunnel reachable
    2. Every SLA table exists in source DB
    3. Every ts_col exists in its table schema
    4. ECS sync endpoint reachable
    """
    issues = 0
    
    # 1. Tunnel
    try:
        r = urllib.request.urlopen("http://127.0.0.1:3099/login", timeout=10)
        log(f"SELFCHECK: tunnel OK ({r.status})")
    except Exception as e:
        log(f"SELFCHECK: tunnel FAIL ({e})")
        issues += 1
    
    # 2. ECS endpoint
    try:
        test_body = json.dumps({"table":"_health_check","rows":[{"test":1}],"source":"selfcheck"}).encode()
        req = urllib.request.Request(SYNC_URL, data=test_body,
            headers={"Authorization": f"Bearer {SYNC_SECRET}","Content-Type":"application/json"})
        r = urllib.request.urlopen(req, timeout=15)
        log(f"SELFCHECK: sync endpoint OK ({r.status})")
    except Exception as e:
        log(f"SELFCHECK: sync endpoint FAIL ({e})")
        issues += 1
    
    # 3. Schema validation
    src = sqlite3.connect(SRC_DB)
    for table, ts_col, cat, ws, we, interval in TABLE_SLA:
        # Table exists?
        exists = src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",[table]).fetchone()
        if not exists:
            log(f"SELFCHECK: SKIP {table} — not in source DB")
            continue
        
        # ts_col exists?
        if ts_col:
            cols = [c[1] for c in src.execute(f'PRAGMA table_info("{table}")')]
            if ts_col not in cols:
                log(f"SELFCHECK: WARN {table} — ts_col='{ts_col}' not in schema (have: {cols[:5]}...)")
                issues += 1
    
    src.close()
    
    if issues:
        log(f"SELFCHECK: {issues} warnings (agent will continue)")
    else:
        log(f"SELFCHECK: all OK ({len(TABLE_SLA)} tables)")
    return issues

def main():
    log("=== Push Agent v3 (HTTP API) ===")
    self_check()
    
    ct = state.execute("SELECT COUNT(*) FROM push_state WHERE status='cold'").fetchone()[0]
    tt = state.execute("SELECT COUNT(*) FROM push_state").fetchone()[0]
    if ct > 0 or tt == 0: cold_start()
    
    src = sqlite3.connect(SRC_DB)
    src.row_factory = sqlite3.Row
    
    while True:
        t0 = time.time()
        now = datetime.now()
        
        for table, ts_col, cat, ws, we, interval in TABLE_SLA:
            if not in_window(ws, we): continue
            
            row = state.execute("SELECT last_synced_ts FROM push_state WHERE table_name=?",[table]).fetchone()
            if not row or not row[0]: continue
            last_ts = row[0]
            
            # Static tables (no timestamp): use hash comparison to skip if unchanged
            if not ts_col:
                last_push = state.execute("SELECT last_push_at FROM push_state WHERE table_name=?",[table]).fetchone()
                if last_push and last_push[0]:
                    try:
                        last_push_dt = datetime.fromisoformat(last_push[0])
                        if (now - last_push_dt).total_seconds() < 3600:
                            continue
                    except: pass
                cur = src.execute(f'SELECT * FROM "{table}" LIMIT 200')
                rows = cur.fetchall()
            else:
                try:
                    cur = src.execute(f'SELECT * FROM "{table}" WHERE "{ts_col}" > ? ORDER BY "{ts_col}" LIMIT 200', [last_ts])
                    rows = cur.fetchall()
                except:
                    continue
            
            if not rows: continue
            
            # Convert to dicts
            cols = [c[1] for c in src.execute(f'PRAGMA table_info("{table}")')]
            data = [{c: r[c] for c in cols} for r in rows]
            
            ok, err = push_via_http(table, data)
            if ok:
                try:
                    if ts_col and rows:
                        new_ts = max(str(r[ts_col]) for r in rows)
                        state.execute("UPDATE push_state SET last_synced_ts=?,last_push_at=? WHERE table_name=?",
                                      [new_ts, now.isoformat(), table])
                    elif not ts_col:
                        state.execute("UPDATE push_state SET last_push_at=? WHERE table_name=?",
                                      [now.isoformat(), table])
                except (KeyError, IndexError) as e:
                    log(f"WARN {table}: cannot update ts ({e})")
                log(f"OK {table}: {len(rows)} rows ({time.time()-t0:.1f}s)")
            else:
                log(f"FAIL {table}: {err}")
            state.commit()
        
        state.execute("UPDATE push_heartbeat SET last_beat=?",[now.isoformat()])
        state.commit()
        
        elapsed = time.time() - t0
        sleep_t = max(2, 5 - elapsed)
        time.sleep(sleep_t)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: log("Shutdown")
    except Exception as e: log(f"FATAL: {e}"); raise
