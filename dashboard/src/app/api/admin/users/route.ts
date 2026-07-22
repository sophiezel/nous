import { NextRequest, NextResponse } from "next/server";
import { getAllUsers, updateUserRole, disableUser, getUserById } from "@/lib/db-auth";
import { isSuperAdmin } from "@/lib/auth";

// GET /api/admin/users — list all users
export async function GET(req: NextRequest) {
  const role = req.headers.get("X-User-Role") || "";
  if (!isSuperAdmin(role)) return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  return NextResponse.json(getAllUsers());
}

// PUT /api/admin/users — update user role
export async function PUT(req: NextRequest) {
  const role = req.headers.get("X-User-Role") || "";
  if (!isSuperAdmin(role)) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const { id, role: newRole } = await req.json();
  const user = getUserById(id);
  if (!user) return NextResponse.json({ error: "Not found" }, { status: 404 });
  if (user.role === "super_admin") return NextResponse.json({ error: "Cannot modify super_admin" }, { status: 403 });

  updateUserRole(id, newRole);
  return NextResponse.json({ ok: true });
}

// DELETE /api/admin/users — disable user
export async function DELETE(req: NextRequest) {
  const role = req.headers.get("X-User-Role") || "";
  if (!isSuperAdmin(role)) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const { id } = await req.json();
  const user = getUserById(id);
  if (!user) return NextResponse.json({ error: "Not found" }, { status: 404 });
  if (user.role === "super_admin") return NextResponse.json({ error: "Cannot delete super_admin" }, { status: 403 });

  disableUser(id);
  return NextResponse.json({ ok: true });
}
