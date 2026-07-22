import { NextRequest, NextResponse } from "next/server";
import { getInviteCode, consumeInviteCode, activateUser } from "@/lib/db-auth";
import { signToken, getCookieName, isSecureCtx } from "@/lib/auth";

export async function POST(req: NextRequest) {
  const maxRetries = 3;
  let lastError: any;

  // Read body once, outside retry loop (req.json() can only be called once)
  let code: string;
  try {
    const body = await req.json();
    code = body?.code;
  } catch {
    return NextResponse.json({ error: "请求格式错误" }, { status: 400 });
  }

  if (!code) {
    return NextResponse.json({ error: "请输入邀请码" }, { status: 400 });
  }

  const deviceFp = req.headers.get("X-Device-Fingerprint") || "";
  if (!deviceFp) {
    return NextResponse.json({ error: "无法获取设备指纹，请刷新后重试" }, { status: 400 });
  }

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const invite = getInviteCode(code.trim().toUpperCase());
      if (!invite) {
        return NextResponse.json({ error: "邀请码无效或已使用" }, { status: 400 });
      }

      // Check expiration
      if (invite.expires_at < new Date().toISOString().replace("T", " ").slice(0, 19)) {
        return NextResponse.json({ error: "邀请码已过期，请联系管理员重新申请" }, { status: 400 });
      }

      // Activate user
      activateUser(invite.phone, invite.role, deviceFp);

      // Mark code as used
      consumeInviteCode(invite.code);

      // Import getUserByPhone lazily to avoid circular dep
      const { getUserByPhone } = await import("@/lib/db-auth");
      const user = getUserByPhone(invite.phone);
      if (!user) {
        return NextResponse.json({ error: "激活失败，用户不存在" }, { status: 500 });
      }

      // Sign JWT
      const token = await signToken({
        sub: user.id,
        phone: user.phone,
        role: user.role,
        device_fp: deviceFp,
      });

      const res = NextResponse.json({ ok: true });
      res.cookies.set(getCookieName(), token, {
        httpOnly: true,
        secure: isSecureCtx(req),
        sameSite: "lax",
        path: "/",
        maxAge: 30 * 24 * 60 * 60,
      });

      return res;
    } catch (e: any) {
      lastError = e;
      // SQLITE_BUSY — retry after short delay
      if (e?.code === 'SQLITE_BUSY' && attempt < maxRetries - 1) {
        console.warn(`[activate/verify] SQLITE_BUSY, retry ${attempt + 1}/${maxRetries - 1}`);
        await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
        continue;
      }
      console.error("[activate/verify]", e);
      break;
    }
  }

  return NextResponse.json({ error: "服务器错误" }, { status: 500 });
}
