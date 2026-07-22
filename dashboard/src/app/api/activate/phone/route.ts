import { NextRequest, NextResponse } from "next/server";
import { getUserByPhone, createUser } from "@/lib/db-auth";

export async function POST(req: NextRequest) {
  try {
    const { phone } = await req.json();
    if (!phone || !/^\d{11}$/.test(phone)) {
      return NextResponse.json({ error: "请输入11位手机号" }, { status: 400 });
    }

    const existing = getUserByPhone(phone);
    if (existing) {
      if (existing.status === "disabled") {
        return NextResponse.json({ error: "该账号已被禁用" }, { status: 403 });
      }
      if (existing.status === "active") {
        return NextResponse.json({ error: "该手机号已激活，无需重复注册" }, { status: 409 });
      }
      // pending or other — allow re-submit
    }

    createUser(phone);
    return NextResponse.json({ ok: true, message: "已提交，等待管理员激活" });
  } catch (e: any) {
    return NextResponse.json({ error: "服务器错误" }, { status: 500 });
  }
}
