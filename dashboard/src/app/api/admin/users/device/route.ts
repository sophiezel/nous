import { NextRequest, NextResponse } from "next/server";
import { removeUserDevice, getUserById } from "@/lib/db-auth";
import { isSuperAdmin } from "@/lib/auth";

export async function DELETE(req: NextRequest) {
  const role = req.headers.get("X-User-Role") || "";
  if (!isSuperAdmin(role)) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const { id } = await req.json();
  const user = getUserById(id);
  if (!user) return NextResponse.json({ error: "Not found" }, { status: 404 });

  removeUserDevice(id);
  return NextResponse.json({ ok: true });
}
