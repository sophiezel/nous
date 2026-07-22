import { NextRequest, NextResponse } from "next/server";
import Database from "better-sqlite3";
import * as path from "path";
import * as os from "os";
import { cacheClear } from "@/lib/cache";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

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

// ─── DB path ──────────────────────────────────────────

function getScreenerDbPath(): string {
  if (process.env.SCREENER_DB_PATH) return process.env.SCREENER_DB_PATH;
  return path.join(os.homedir(), "code/stock-screener/data/screener.db");
}

// ─── Write DB singleton (shared with sync route) ──────

/** Module-level write DB connection */
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

/** Close and reopen the write DB connection after file replacement */
function reloadWriteDb(): boolean {
  try {
    if (_writeDb) {
      _writeDb.close();
      _writeDb = null;
    }
    // Re-open
    getWriteDb();
    return true;
  } catch (err: any) {
    console.error("[reload] Failed to reopen write DB:", err.message || String(err));
    return false;
  }
}

// ─── Route ────────────────────────────────────────────

/**
 * POST /api/data/reload
 *
 * Closes and reopens the DB connection. Useful after an rsync/rsync
 * has replaced the underlying screener.db file, ensuring the process
 * picks up the new data without a full restart.
 *
 * Request: { }
 * Response: { ok: true, dbPath: string }
 */
export async function POST(req: NextRequest) {
  // 1. Auth
  const authResult = authorize(req);
  if (!authResult.ok) {
    return NextResponse.json({ error: authResult.reason }, { status: 401 });
  }

  // 2. Reload DB
  const dbPath = getScreenerDbPath();
  const success = reloadWriteDb();

  // 3. Clear all caches
  cacheClear();

  if (!success) {
    return NextResponse.json(
      { ok: false, error: "Failed to reopen DB connection", dbPath },
      { status: 500 }
    );
  }

  console.log(`[reload] DB reloaded successfully: ${dbPath}`);
  return NextResponse.json({ ok: true, dbPath });
}

/**
 * GET /api/data/reload — health check
 */
export async function GET() {
  return NextResponse.json({ status: "ok", message: "Data reload endpoint. Use POST to trigger DB reload." });
}
