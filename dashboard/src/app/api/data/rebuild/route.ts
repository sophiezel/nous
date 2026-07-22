import { NextRequest, NextResponse } from "next/server";
import Database from "better-sqlite3";
import * as path from "path";
import * as os from "os";
import { cacheInvalidate, cacheClear } from "@/lib/cache";

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

// ─── Write DB singleton ───────────────────────────────

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

// ─── Dynamic INSERT (same as sync route) ──────────────

function insertRows(
  db: Database.Database,
  table: string,
  rows: Record<string, unknown>[]
): { applied: number; errors: string[] } {
  if (rows.length === 0) return { applied: 0, errors: [] };

  const columns = new Set<string>();
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      columns.add(key);
    }
  }
  const colList = [...columns];
  if (colList.length === 0) return { applied: 0, errors: [] };

  const placeholders = `(${colList.map(() => "?").join(",")})`;
  const upsertSQL = `INSERT OR REPLACE INTO "${table}" (${colList.map((c) => `"${c}"`).join(",")}) VALUES ${placeholders}`;

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

// ─── Rebuild a single table ───────────────────────────

interface RebuildResult {
  table: string;
  ok: boolean;
  rows_before: number;
  rows_after: number;
  error?: string;
}

function rebuildTable(
  db: Database.Database,
  table: string,
  newRows: Record<string, unknown>[]
): RebuildResult {
  try {
    // Get row count before
    const before = (db.prepare(`SELECT COUNT(*) AS cnt FROM "${table}"`).get() as { cnt: number }).cnt;

    // Truncate the table
    db.prepare(`DELETE FROM "${table}"`).run();

    // Insert new rows
    let applied = 0;
    const errors: string[] = [];
    if (newRows.length > 0) {
      const result = insertRows(db, table, newRows);
      applied = result.applied;
      errors.push(...result.errors);
    }

    const after = (db.prepare(`SELECT COUNT(*) AS cnt FROM "${table}"`).get() as { cnt: number }).cnt;

    return {
      table,
      ok: errors.length === 0,
      rows_before: before,
      rows_after: after,
      error: errors.length > 0 ? errors.slice(0, 5).join("; ") : undefined,
    };
  } catch (err: any) {
    return {
      table,
      ok: false,
      rows_before: -1,
      rows_after: -1,
      error: err.message || String(err),
    };
  }
}

// ─── Route ────────────────────────────────────────────

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

  const { table, rows, batches } = body;
  const db = getWriteDb();

  // ─── Batches mode ───────────────────────────────────
  if (batches !== undefined) {
    if (!Array.isArray(batches)) {
      return NextResponse.json({ error: "'batches' must be an array" }, { status: 400 });
    }

    const results: RebuildResult[] = [];
    const transaction = db.transaction(() => {
      for (let i = 0; i < batches.length; i++) {
        const batch = batches[i];
        if (!batch || typeof batch.table !== "string") {
          results.push({
            table: batch?.table || `batches[${i}]`,
            ok: false,
            rows_before: -1,
            rows_after: -1,
            error: "Missing 'table' (string)",
          });
          continue;
        }
        const rows = Array.isArray(batch.rows) ? batch.rows : [];
        const result = rebuildTable(db, batch.table, rows);
        results.push(result);
        cacheInvalidate(batch.table);
        console.log(`[rebuild] ${batch.table}: ${result.rows_before} rows -> ${result.rows_after} rows ${result.ok ? "OK" : `ERROR: ${result.error}`}`);
      }
    });

    try {
      transaction();
    } catch (err: any) {
      return NextResponse.json(
        { error: `Rebuild transaction failed: ${err.message || String(err)}` },
        { status: 500 }
      );
    }

    return NextResponse.json({ ok: true, results });
  }

  // ─── Single-table mode ────────────────────────────
  if (!table || typeof table !== "string") {
    return NextResponse.json({ error: "Missing or invalid 'table'" }, { status: 400 });
  }
  const rowData = Array.isArray(rows) ? rows : [];

  let result: RebuildResult;
  const transaction = db.transaction(() => {
    result = rebuildTable(db, table, rowData);
    return result;
  });

  try {
    result = transaction();
  } catch (err: any) {
    return NextResponse.json(
      { error: `Rebuild failed: ${err.message || String(err)}`, table },
      { status: 500 }
    );
  }

  cacheInvalidate(table);
  console.log(`[rebuild] ${table}: ${result.rows_before} rows -> ${result.rows_after} rows ${result.ok ? "OK" : `ERROR: ${result.error}`}`);

  return NextResponse.json({ ok: result.ok, table: result.table, rows_before: result.rows_before, rows_after: result.rows_after });
}

/**
 * GET /api/data/rebuild — health check
 */
export async function GET() {
  return NextResponse.json({ status: "ok", message: "Data rebuild endpoint. Use POST to rebuild a table." });
}
