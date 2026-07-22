import { NextRequest, NextResponse } from "next/server";
import Database from "better-sqlite3";
import * as path from "path";
import * as os from "os";
import { cacheInvalidate } from "@/lib/cache";

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

// ─── Write DB singleton (separate from the read-only one in db.ts) ──

let _writeDb: Database.Database | null = null;

function getWriteDb(): Database.Database {
  if (_writeDb) return _writeDb;
  const dbPath = getScreenerDbPath();
  const db = new Database(dbPath);
  db.pragma("journal_mode = WAL");
  db.pragma("busy_timeout = 5000");
  _writeDb = db;
  return db;
}

// ─── Dynamic INSERT ───────────────────────────────────

/**
 * Build and execute INSERT OR REPLACE for a batch of rows.
 * On conflict (row-level), skip that single row instead of failing the batch.
 */
function insertRows(
  db: Database.Database,
  table: string,
  rows: Record<string, unknown>[]
): { applied: number; errors: string[] } {
  if (rows.length === 0) return { applied: 0, errors: [] };

  // Collect all unique column keys across all rows
  const columns = new Set<string>();
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      columns.add(key);
    }
  }
  const colList = [...columns];

  if (colList.length === 0) return { applied: 0, errors: [] };

  // Build placeholders: (?,?,?) x N
  const placeholders = `(${colList.map(() => "?").join(",")})`;
  const insertSQL = `INSERT OR IGNORE INTO ${table} (${colList.map((c) => `"${c}"`).join(",")}) VALUES ${placeholders}`;

  // Also try UPDATE if row exists (upsert). Use INSERT OR REPLACE instead.
  // Actually, INSERT OR IGNORE is safer — skip rows that conflict.
  // If the caller wants upsert behavior we can switch to INSERT OR REPLACE.
  // Let's use INSERT OR REPLACE for now — it matches the task "upsert" semantics.
  const upsertSQL = `INSERT OR REPLACE INTO ${table} (${colList.map((c) => `"${c}"`).join(",")}) VALUES ${placeholders}`;

  let applied = 0;
  const errors: string[] = [];

  const stmt = db.prepare(upsertSQL);

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const values = colList.map((c) => {
      const v = row[c];
      return v === undefined ? null : v;
    });

    try {
      stmt.run(...values);
      applied++;
    } catch (err: any) {
      errors.push(`row ${i}: ${err.message || String(err)}`);
    }
  }

  return { applied, errors };
}

// ─── Route ────────────────────────────────────────────

/**
 * Process a single batch (table + rows).
 */
function processBatch(
  db: Database.Database,
  table: string,
  rows: Record<string, unknown>[]
): { applied: number; errors: string[] } {
  if (rows.length === 0) return { applied: 0, errors: [] };
  const transaction = db.transaction(() => {
    return insertRows(db, table, rows);
  });
  return transaction();
}

/**
 * Sync response item for a single table or batch.
 */
interface SyncResult {
  table: string;
  received: number;
  applied: number;
  errors?: string[];
}

export async function POST(req: NextRequest) {
  // 1. Auth
  const authResult = authorize(req);
  if (!authResult.ok) {
    return NextResponse.json({ error: authResult.reason }, { status: 401 });
  }

  // 2. Parse body
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { table, rows, source, batches } = body;
  const db = getWriteDb();
  const results: SyncResult[] = [];

  // ─── Batches mode ───────────────────────────────────
  if (batches !== undefined) {
    if (!Array.isArray(batches)) {
      return NextResponse.json({ error: "'batches' must be an array" }, { status: 400 });
    }
    for (let i = 0; i < batches.length; i++) {
      const batch = batches[i];
      if (!batch || typeof batch.table !== "string" || !Array.isArray(batch.rows)) {
        return NextResponse.json(
          { error: `batches[${i}] must have 'table' (string) and 'rows' (array)` },
          { status: 400 }
        );
      }
      let result: { applied: number; errors: string[] };
      try {
        result = processBatch(db, batch.table, batch.rows);
      } catch (err: any) {
        result = { applied: 0, errors: [`transaction failed: ${err.message || String(err)}`] };
      }
      cacheInvalidate(batch.table);
      console.log(
        `[sync] ${source || "unknown"} -> ${batch.table}: ${result.applied}/${batch.rows.length} rows${result.errors.length ? `, ${result.errors.length} errors` : " OK"}`
      );
      results.push({
        table: batch.table,
        received: batch.rows.length,
        applied: result.applied,
        errors: result.errors.length > 0 ? result.errors.slice(0, 20) : undefined,
      });
    }
    return NextResponse.json({ ok: true, batches: results });
  }

  // ─── Single-table mode (legacy) ────────────────────
  if (!table || typeof table !== "string") {
    return NextResponse.json({ error: "Missing or invalid 'table'" }, { status: 400 });
  }
  if (!Array.isArray(rows)) {
    return NextResponse.json({ error: "Missing or invalid 'rows' (must be an array)" }, { status: 400 });
  }

  const received = rows.length;
  let result: { applied: number; errors: string[] };
  try {
    result = processBatch(db, table, rows);
  } catch (err: any) {
    return NextResponse.json(
      { error: `Transaction failed: ${err.message || String(err)}`, received, applied: 0 },
      { status: 500 }
    );
  }

  cacheInvalidate(table);

  if (result.errors.length > 0) {
    console.warn(`[sync] ${source || "unknown"} -> ${table}: ${result.applied}/${received} rows, ${result.errors.length} errors`, result.errors.slice(0, 5));
  } else {
    console.log(`[sync] ${source || "unknown"} -> ${table}: ${result.applied}/${received} rows OK`);
  }

  return NextResponse.json({
    received,
    applied: result.applied,
    errors: result.errors.length > 0 ? result.errors.slice(0, 20) : undefined,
  });
}

/**
 * GET /api/data/sync — health check
 */
export async function GET() {
  return NextResponse.json({ status: "ok", message: "Data sync endpoint. Use POST to ingest data." });
}
