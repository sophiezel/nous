#!/usr/bin/env python3
"""Migrate data from local SQLite to Neon PostgreSQL"""
import os, json, sys

DB_PATH = os.path.expanduser("~/code/dashboard/data/reports.db")
NEON_URL = os.environ.get("DATABASE_URL")

if not NEON_URL:
    print("❌ DATABASE_URL not set. Export it first:")
    print("   export DATABASE_URL='postgresql://...'")
    sys.exit(1)

import sqlite3
import psycopg2

# Connect to both
sqlite = sqlite3.connect(DB_PATH)
pg = psycopg2.connect(NEON_URL)
pg_cur = pg.cursor()

# Run schema
schema_path = os.path.join(os.path.dirname(DB_PATH), "schema.pg.sql")
with open(schema_path) as f:
    pg_cur.execute(f.read())
pg.commit()
print("✅ Schema created")

# Migrate macro_scores
rows = sqlite.execute("SELECT date, score, position, indicators, created_at FROM macro_scores").fetchall()
for r in rows:
    indicators = json.loads(r[3]) if r[3] else None
    pg_cur.execute(
        "INSERT INTO macro_scores (date, score, position, indicators, created_at) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (date) DO NOTHING",
        (r[0], r[1], r[2], json.dumps(indicators) if indicators else None, r[4])
    )
print(f"✅ macro_scores: {len(rows)} rows")

# Migrate sentiment
rows = sqlite.execute("SELECT date, score, limit_up_count, limit_up_rate, details, created_at FROM sentiment").fetchall()
for r in rows:
    details = json.loads(r[4]) if r[4] else None
    pg_cur.execute(
        "INSERT INTO sentiment (date, score, limit_up_count, limit_up_rate, details, created_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (date) DO NOTHING",
        (r[0], r[1], r[2], r[3], json.dumps(details) if details else None, r[5])
    )
print(f"✅ sentiment: {len(rows)} rows")

# Migrate reports
rows = sqlite.execute("SELECT type, title, content, metadata, created_at FROM reports").fetchall()
for r in rows:
    metadata = json.loads(r[3]) if r[3] else None
    pg_cur.execute(
        "INSERT INTO reports (type, title, content, metadata, created_at) VALUES (%s,%s,%s,%s,%s)",
        (r[0], r[1], r[2], json.dumps(metadata) if metadata else None, r[4])
    )
print(f"✅ reports: {len(rows)} rows")

pg.commit()
pg_cur.close()
pg.close()
sqlite.close()
print("\n🎉 Migration complete!")
