#!/usr/bin/env python3
"""push_health_check.py — monitor push agent + ECS health"""
import sqlite3, os, sys, urllib.request
from datetime import datetime

STATE_DB = os.path.expanduser("~/.hermes/data/push_state.db")

ok = True
try:
    db = sqlite3.connect(STATE_DB)
    hb = db.execute("SELECT last_beat FROM push_heartbeat WHERE id=1").fetchone()
    if not hb:
        print("WARN: no heartbeat")
        ok = False
    else:
        last_beat = datetime.fromisoformat(hb[0])
        age = (datetime.now() - last_beat).total_seconds()
        if age > 300:
            print(f"ALERT: heartbeat stale ({age:.0f}s)")
            ok = False
        else:
            print(f"OK: heartbeat {age:.0f}s ago")
    
    n = len(db.execute("SELECT * FROM push_state").fetchall())
    print(f"Tables tracked: {n}")
    
    # ECS check — any HTTP response = reachable
    try:
        r = urllib.request.urlopen("https://tiangong.uno/login", timeout=15)
        print(f"ECS: reachable ({r.status})")
    except urllib.error.HTTPError as e:
        print(f"ECS: reachable ({e.code})")
    except Exception as e:
        print(f"ECS unreachable: {e}")
    
    db.close()
except Exception as e:
    print(f"FATAL: {e}")
    ok = False

sys.exit(0 if ok else 1)
