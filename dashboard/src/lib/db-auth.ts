import { getScreenerDb, connect_readonly } from "./db";
import * as crypto from "crypto";

// ─── Types ────────────────────────────────────────────

export interface User {
  id: number;
  phone: string;
  device_fps: string; // JSON array
  role: "guest" | "member" | "vip" | "super_admin";
  status: "pending" | "active" | "disabled";
  created_at: string;
  activated_at: string | null;
}

export interface InviteCode {
  id: number;
  code: string;
  phone: string;
  role: string;
  expires_at: string;
  used: number;
  created_at: string;
  used_at: string | null;
}

// ─── Users ─────────────────────────────────────────────

export function getUserByPhone(phone: string): User | undefined {
  return connect_readonly()
    .prepare("SELECT * FROM users WHERE phone = ?")
    .get(phone) as User | undefined;
}

export function getUserById(id: number): User | undefined {
  return connect_readonly()
    .prepare("SELECT * FROM users WHERE id = ?")
    .get(id) as User | undefined;
}

export function getUserByDeviceFp(fp: string): User | undefined {
  // device_fps is JSON array, search via LIKE
  return connect_readonly()
    .prepare("SELECT * FROM users WHERE device_fps LIKE ? AND status = 'active'")
    .get(`%${fp}%`) as User | undefined;
}

export function createUser(phone: string): User {
  const db = getScreenerDb();
  db.prepare(
    "INSERT OR IGNORE INTO users (phone) VALUES (?)"
  ).run(phone);
  return getUserByPhone(phone)!;
}

export function activateUser(
  phone: string,
  role: string,
  deviceFp: string
): void {
  const db = getScreenerDb();
  // Append device_fp to JSON array
  db.prepare(`
    UPDATE users
    SET role = ?,
        device_fps = CASE
          WHEN device_fps = '[]' THEN json_insert(device_fps, '$[#]', ?)
          WHEN device_fps NOT LIKE '%' || ? || '%'
            THEN json_insert(device_fps, '$[#]', ?)
          ELSE device_fps
        END,
        status = 'active',
        activated_at = datetime('now')
    WHERE phone = ?
  `).run(role, deviceFp, deviceFp, deviceFp, phone);
}

export function updateUserRole(id: number, role: string): void {
  getScreenerDb()
    .prepare("UPDATE users SET role = ? WHERE id = ?")
    .run(role, id);
}

export function disableUser(id: number): void {
  getScreenerDb()
    .prepare("UPDATE users SET status = 'disabled' WHERE id = ?")
    .run(id);
}

export function removeUserDevice(id: number, deviceIndex?: number): void {
  const db = getScreenerDb();
  if (deviceIndex !== undefined) {
    // Remove specific device by index
    db.prepare(`
      UPDATE users
      SET device_fps = json_remove(device_fps, '$[' || ? || ']')
      WHERE id = ?
    `).run(deviceIndex, id);
  } else {
    // Clear all devices
    db.prepare("UPDATE users SET device_fps = '[]' WHERE id = ?").run(id);
  }
}

export function getAllUsers(): User[] {
  return connect_readonly()
    .prepare("SELECT * FROM users ORDER BY created_at DESC")
    .all() as User[];
}

// ─── Invite Codes ──────────────────────────────────────

export function getInviteCode(code: string): InviteCode | undefined {
  return connect_readonly()
    .prepare("SELECT * FROM invite_codes WHERE code = ? AND used = 0")
    .get(code) as InviteCode | undefined;
}

export function createInviteCode(
  phone: string,
  role: string,
  ttlMinutes: number
): InviteCode {
  const db = getScreenerDb();
  const code = "TG-" + randomHex(4) + "-" + randomHex(4) + "-" + randomHex(4);
  db.prepare(`
    INSERT INTO invite_codes (code, phone, role, expires_at)
    VALUES (?, ?, ?, datetime('now', '+' || ? || ' minutes'))
  `).run(code, phone, role, ttlMinutes);
  return db
    .prepare("SELECT * FROM invite_codes WHERE code = ?")
    .get(code) as InviteCode;
}

export function consumeInviteCode(code: string): void {
  getScreenerDb()
    .prepare("UPDATE invite_codes SET used = 1, used_at = datetime('now') WHERE code = ?")
    .run(code);
}

export function getUnusedInviteCodes(phone: string): InviteCode[] {
  return connect_readonly()
    .prepare(
      "SELECT * FROM invite_codes WHERE phone = ? AND used = 0 AND expires_at > datetime('now') ORDER BY created_at DESC"
    )
    .all(phone) as InviteCode[];
}

// ─── Helpers ───────────────────────────────────────────

function randomHex(bytes: number): string {
  return Array.from(new Uint8Array(crypto.randomBytes(bytes)))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase();
}

export function parseDeviceFps(json: string): string[] {
  try {
    return JSON.parse(json);
  } catch {
    return [];
  }
}
