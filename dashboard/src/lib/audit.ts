import fs from "fs";
import path from "path";
import os from "os";

const AUDIT_LOG = path.join(os.homedir(), ".hermes", "logs", "audit.jsonl");

export interface AuditEntry {
  ts: string;
  user: string;
  action: string;
  resource: string;
  ip: string;
  success: boolean;
  detail?: string;
}

export function auditLog(entry: AuditEntry): void {
  try {
    const line = JSON.stringify(entry) + "\n";
    fs.appendFileSync(AUDIT_LOG, line, { mode: 0o600 });
  } catch {
    // Fail silently — audit logging should never break the app
    console.error("[AUDIT] Failed to write audit log");
  }
}

/** Log a login attempt */
export function logLogin(ip: string, success: boolean, detail?: string): void {
  auditLog({
    ts: new Date().toISOString(),
    user: "admin",
    action: success ? "login" : "login_fail",
    resource: "/api/auth/callback/credentials",
    ip,
    success,
    detail,
  });
}

/** Log a page view */
export function logPageView(ip: string, pathname: string): void {
  auditLog({
    ts: new Date().toISOString(),
    user: "admin",
    action: "page_view",
    resource: pathname,
    ip,
    success: true,
  });
}

/** Log an API access */
export function logApiAccess(
  ip: string,
  pathname: string,
  success: boolean
): void {
  auditLog({
    ts: new Date().toISOString(),
    user: "admin",
    action: "api_access",
    resource: pathname,
    ip,
    success,
  });
}
