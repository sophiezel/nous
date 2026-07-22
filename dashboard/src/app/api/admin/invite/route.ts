import { NextRequest, NextResponse } from "next/server";
import { createInviteCode } from "@/lib/db-auth";
import { isSuperAdmin } from "@/lib/auth";

export async function POST(req: NextRequest) {
  const role = req.headers.get("X-User-Role") || "";
  if (!isSuperAdmin(role)) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const { phone, role: userRole, ttl_minutes } = await req.json();
  if (!phone || !/^\d{11}$/.test(phone)) {
    return NextResponse.json({ error: "手机号格式错误" }, { status: 400 });
  }
  if (!["member", "vip"].includes(userRole)) {
    return NextResponse.json({ error: "角色无效" }, { status: 400 });
  }
  const ttl = Math.min(Math.max(ttl_minutes || 30, 5), 1440);

  const invite = createInviteCode(phone, userRole, ttl);
  return NextResponse.json({
    code: invite.code,
    expires_at: invite.expires_at,
  });
}
