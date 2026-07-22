import { SignJWT, jwtVerify } from "jose";
import { getUserByPhone, getUserByDeviceFp, parseDeviceFps } from "./db-auth";

const JWT_SECRET = new TextEncoder().encode(
  process.env.TIANGONG_JWT_SECRET || "dev-secret-change-me"
);

export interface TiangongSession {
  sub: number;        // user.id
  phone: string;
  role: string;       // guest | member | vip | super_admin
  device_fp: string;  // current device fingerprint
}

const COOKIE_NAME = "__tiangong_session";
const JWT_MAX_AGE = 30 * 24 * 60 * 60; // 30 days in seconds

// ─── JWT Operations ────────────────────────────────────

export async function signToken(session: TiangongSession): Promise<string> {
  return new SignJWT({ sub: String(session.sub), phone: session.phone, role: session.role, device_fp: session.device_fp })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime(`${JWT_MAX_AGE}s`)
    .sign(JWT_SECRET);
}

export async function verifyToken(token: string): Promise<TiangongSession | null> {
  try {
    const { payload } = await jwtVerify(token, JWT_SECRET);
    return payload as unknown as TiangongSession;
  } catch {
    return null;
  }
}

// ─── Cookie Helpers ────────────────────────────────────

export function getCookieName(): string {
  return COOKIE_NAME;
}

export function isSecureCtx(req?: { headers: { get: (k: string) => string | null } }): boolean {
  if (process.env.NODE_ENV !== "production") return false;
  if (!req) return true;
  const proto = req.headers.get("x-forwarded-proto");
  if (proto === "https") return true;
  const host = req.headers.get("host") || "";
  // localhost / LAN IP → allow plain HTTP
  if (host.startsWith("localhost") || host.startsWith("127.") || host.startsWith("192.168.") || host.startsWith("10.") || host.startsWith("172.16.")) return false;
  return true;
}

// ─── Auth Logic ────────────────────────────────────────

export async function authenticateByToken(token: string): Promise<TiangongSession | null> {
  return verifyToken(token);
}

export async function authenticateByDeviceFp(fp: string): Promise<TiangongSession | null> {
  if (!fp) return null;
  const user = getUserByDeviceFp(fp);
  if (!user) return null;
  return {
    sub: user.id,
    phone: user.phone,
    role: user.role,
    device_fp: fp,
  };
}

export function isSuperAdmin(role: string): boolean {
  return role === "super_admin";
}

export function canViewAgentData(role: string): boolean {
  return role === "vip" || role === "super_admin";
}

export function canManageUsers(role: string): boolean {
  return role === "super_admin";
}
