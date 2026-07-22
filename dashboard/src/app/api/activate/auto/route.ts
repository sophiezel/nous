import { NextRequest, NextResponse } from "next/server";
import { authenticateByDeviceFp, signToken, getCookieName, isSecureCtx } from "@/lib/auth";

// Auto-relogin when cookie expired but device FP matches
export async function GET(req: NextRequest) {
  const deviceFp = req.headers.get("X-Device-Fingerprint") || "";

  if (!deviceFp) {
    return NextResponse.json({ ok: false, error: "no_fingerprint" }, { status: 400 });
  }

  const session = await authenticateByDeviceFp(deviceFp);
  if (!session) {
    return NextResponse.json({ ok: false, error: "device_not_recognized" }, { status: 401 });
  }

  const token = await signToken(session);
  const res = NextResponse.json({ ok: true });
  res.cookies.set(getCookieName(), token, {
    httpOnly: true,
    secure: isSecureCtx(req),
    sameSite: "lax",
    path: "/",
    maxAge: 30 * 24 * 60 * 60,
  });

  return res;
}
