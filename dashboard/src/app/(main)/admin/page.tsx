import { headers } from "next/headers";
import { getAllUsers } from "@/lib/db-auth";
import { isSuperAdmin } from "@/lib/auth";
import { AdminClient } from "./AdminClient";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const h = await headers();
  const role = h.get("X-User-Role") || "";

  if (!isSuperAdmin(role)) {
    return (
      <div className="flex items-center justify-center h-96">
        <p className="text-zinc-500">无权访问</p>
      </div>
    );
  }

  const users = getAllUsers();

  return <AdminClient users={users} />;
}
