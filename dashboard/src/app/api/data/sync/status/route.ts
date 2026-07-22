import { NextRequest, NextResponse } from "next/server";
import Database from "better-sqlite3";
import * as path from "path";
import * as os from "os";
import * as crypto from "crypto";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// ─── DB path ──────────────────────────────────────────

function getScreenerDbPath(): string {
  if (process.env.SCREENER_DB_PATH) return process.env.SCREENER_DB_PATH;
  return path.join(os.homedir(), "code/stock-screener/data/screener.db");
}

// ─── Auth ─────────────────────────────────────────────

const SYNC_SECRET = process.env.DATA_SYNC_SECRET || "test";

function authorize(req: NextRequest): { ok: true } | { ok: false; reason: string } {
  const auth = req.headers.get("authorization");
  if (!auth || !auth.startsWith("Bearer ")) {
    return { ok: false, reason: "Missing or malformed Authorization header" };
  }
  const token = auth.slice(7).trim();
  if (token !== SYNC_SECRET) {
    return { ok: false, reason: "Invalid token" };
  }
  return { ok: true };
}

// ─── Read-only DB connection ──────────────────────────

let _readDb: Database.Database | null = null;

function getDb(): Database.Database {
  if (_readDb) return _readDb;
  const dbPath = getScreenerDbPath();
  const db = new Database(dbPath, { readonly: true });
  db.pragma("journal_mode = WAL");
  _readDb = db;
  return db;
}

// ─── Helpers ──────────────────────────────────────────

/**
 * Compute a hash of a table's column definitions (stable schema fingerprint).
 */
function schemaHash(db: Database.Database, table: string): string {
  try {
    const rows = db.prepare(`PRAGMA table_info("${table}")`).all() as { name: string; type: string }[];
    const cols = rows.map((r) => `${r.name}:${r.type}`).sort().join(",");
    return crypto.createHash("md5").update(cols).digest("hex").slice(0, 12);
  } catch {
    return "error";
  }
}

/**
 * Try to find a reasonable "latest timestamp" column for a table.
 * Looks for columns named: trade_date, ts, timestamp, updated_at, date, _ts.
 */
function latestTimestamp(
  db: Database.Database,
  table: string
): string | null {
  const tsCols = ["trade_date", "ts", "timestamp", "updated_at", "date", "_ts", "created_at", "trade_date"];
  // Get actual columns
  let cols: { name: string }[];
  try {
    cols = db.prepare(`PRAGMA table_info("${table}")`).all() as { name: string }[];
  } catch {
    return null;
  }
  const colNames = cols.map((c) => c.name);
  for (const candidate of tsCols) {
    if (colNames.includes(candidate)) {
      try {
        const row = db.prepare(`SELECT "${candidate}" AS ts FROM "${table}" ORDER BY "${candidate}" DESC LIMIT 1`).get() as { ts: string | null } | undefined;
        if (row && row.ts !== null) {
          return String(row.ts);
        }
      } catch {
        // column might not be sortable as-is; try MAX
        try {
          const row = db.prepare(`SELECT MAX("${candidate}") AS ts FROM "${table}"`).get() as { ts: string | null } | undefined;
          if (row && row.ts !== null) {
            return String(row.ts);
          }
        } catch {
          // skip
        }
      }
    }
  }
  return null;
}

// ─── Route ────────────────────────────────────────────

export async function GET(req: NextRequest) {
  // Auth
  const authResult = authorize(req);
  if (!authResult.ok) {
    return NextResponse.json({ error: authResult.reason }, { status: 401 });
  }

  const db = getDb();

  // Get all user tables (exclude sqlite_*)
  const tables = db
    .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    .all() as { name: string }[];

  const tablesInfo: Record<string, { row_count: number; latest_ts: string | null; schema_hash: string }> = {};

  for (const { name: table } of tables) {
    try {
      const countRow = db.prepare(`SELECT COUNT(*) AS cnt FROM "${table}"`).get() as { cnt: number };
      tablesInfo[table] = {
        row_count: countRow.cnt,
        latest_ts: latestTimestamp(db, table),
        schema_hash: schemaHash(db, table),
      };
    } catch (err: any) {
      tablesInfo[table] = {
        row_count: -1,
        latest_ts: null,
        schema_hash: "error",
      };
    }
  }

  return NextResponse.json({
    ok: true,
    table_count: tables.length,
    tables: tablesInfo,
  });
}
