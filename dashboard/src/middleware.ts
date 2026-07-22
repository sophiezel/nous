import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { rateLimit, RATE_LIMITS } from "@/lib/rate-limit";
import { logLogin } from "@/lib/audit";
import { authenticateByToken, authenticateByDeviceFp, canManageUsers, canViewAgentData, signToken, getCookieName, isSecureCtx } from "@/lib/auth";
import crypto from "crypto";

export const runtime = "nodejs";

const MOBILE_REGEX = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile/i;
const BLOCKED_AGENTS = [
  "sqlmap","nikto","nmap","masscan","zgrab","gobuster","dirbuster","wfuzz","ffuf",
  "curl","wget","python-requests","python-urllib","go-http-client","libwww",
];

function getIp(r: NextRequest) { return r.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || r.headers.get("x-real-ip") || "127.0.0.1"; }
function nonce() { return crypto.randomBytes(16).toString("base64"); }
function getExternalBaseUrl(request: NextRequest): string {
  const proto = request.headers.get("x-forwarded-proto") || "http";
  const host = request.headers.get("x-forwarded-host") || request.headers.get("host") || "localhost:3000";
  return `${proto}://${host}`;
}

function securePublic(res: NextResponse, n: string) {
  res.headers.set("X-Next-Nonce", n);
  res.headers.set("Content-Security-Policy",
    `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self' `
  );
  res.headers.set("Referrer-Policy","strict-origin-when-cross-origin");
  res.headers.set("X-Content-Type-Options","nosniff");
  res.headers.set("X-Frame-Options","DENY");
  return res;
}

function securePrivate(res: NextResponse, n: string, req: NextRequest) {
  res.headers.set("X-Next-Nonce", n);
  const base = `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'; connect-src 'self'`;
  res.headers.set("Content-Security-Policy", base);
  res.headers.set("Referrer-Policy","strict-origin-when-cross-origin");
  res.headers.set("X-Content-Type-Options","nosniff");
  res.headers.set("X-Frame-Options","DENY");
  res.headers.set("X-XSS-Protection","1; mode=block");
  res.headers.set("Permissions-Policy","camera=(), microphone=(), geolocation=(), payment=()");
  if (req.headers.get("x-forwarded-proto") === "https") {
    res.headers.set("Cross-Origin-Opener-Policy","same-origin");
    res.headers.set("Cross-Origin-Resource-Policy","same-origin");
  }
  return res;
}

export default async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const ua = (request.headers.get("user-agent") || "").toLowerCase();
  const ip = getIp(request);

  // Public paths — no auth required
  const isPublicPath =
    pathname.startsWith("/activate") ||
    pathname.startsWith("/api/activate") ||
    pathname.startsWith("/api/health") ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon.ico") ||
    pathname === "/robots.txt";

  if (isPublicPath) {
    return securePublic(NextResponse.next(), nonce());
  }

  // Block crawlers
  if (BLOCKED_AGENTS.some(b => ua.includes(b))) {
    return new NextResponse("Forbidden", { status: 403 });
  }

  // Rate limit: activation
  if (pathname.startsWith("/api/activate/")) {
    if (!rateLimit(`activate:${ip}`, 5, 60_000).success) {
      return new NextResponse("Too many requests", { status: 429 });
    }
  }

  // Rate limit: API
  if (pathname.startsWith("/api/") && !pathname.startsWith("/api/activate/")) {
    if (!rateLimit(`api:${ip}`, RATE_LIMITS.API_DATA.limit, RATE_LIMITS.API_DATA.windowMs).success) {
      return new NextResponse("Too Many Requests", { status: 429 });
    }
  }

  // ── Authentication ──────────────────────────────────

  const cookieName = getCookieName();
  const token = request.cookies.get(cookieName)?.value;
  let session = token ? await authenticateByToken(token) : null;

  // If token expired/invalid, try device fingerprint auto-login
  if (!session) {
    const deviceFp = request.headers.get("X-Device-Fingerprint") || "";
    if (deviceFp) {
      session = await authenticateByDeviceFp(deviceFp);
    }
  }

  // Not authenticated
  if (!session) {
    const baseUrl = getExternalBaseUrl(request);
    const u = new URL("/activate", baseUrl);
    u.searchParams.set("callbackUrl", `${baseUrl}${request.nextUrl.pathname}${request.nextUrl.search}`);
    return NextResponse.redirect(u);
  }

  // ── Authorization ───────────────────────────────────

  // Admin routes: super_admin only
  if (pathname.startsWith("/admin") || pathname.startsWith("/api/admin")) {
    if (!canManageUsers(session.role)) {
      return new NextResponse("Forbidden", { status: 403 });
    }
  }

  // Agent data routes: vip or super_admin only
  const agentPages = ["/reports", "/signals", "/mobile/reports", "/mobile/signals"];
  if (agentPages.some(p => pathname.startsWith(p))) {
    if (!canViewAgentData(session.role)) {
      const u = new URL("/mobile", getExternalBaseUrl(request));
      return NextResponse.redirect(u);
    }
  }

  // ── Device fingerprint check ────────────────────────

  const clientFp = request.headers.get("X-Device-Fingerprint");
  if (clientFp && session.device_fp && clientFp !== session.device_fp) {
    const deviceSession = await authenticateByDeviceFp(clientFp);
    if (deviceSession && deviceSession.sub === session.sub) {
      session = deviceSession;
    } else {
      const u = new URL("/activate", getExternalBaseUrl(request));
      u.searchParams.set("reason", "device_mismatch");
      return NextResponse.redirect(u);
    }
  }

  // ── Mobile redirect ─────────────────────────────────

  if (pathname === "/" && MOBILE_REGEX.test(ua)) {
    return NextResponse.redirect(new URL("/mobile", getExternalBaseUrl(request)));
  }

  // ── Set/refresh cookie + inject user context ────────

  const res = securePrivate(NextResponse.next(), nonce(), request);

  const newToken = await signToken({
    sub: session.sub,
    phone: session.phone,
    role: session.role,
    device_fp: session.device_fp,
  });
  res.cookies.set(cookieName, newToken, {
    httpOnly: true,
    secure: isSecureCtx(request),
    sameSite: "lax",
    path: "/",
    maxAge: 30 * 24 * 60 * 60,
  });

  res.headers.set("X-User-Id", String(session.sub));
  res.headers.set("X-User-Role", session.role);
  res.headers.set("X-User-Phone", session.phone);

  return res;
}

export const config = { matcher: ["/((?!_next/static|_next/image|api/data/sync|api/data/reload|api/recommendations/today).*)"] };
