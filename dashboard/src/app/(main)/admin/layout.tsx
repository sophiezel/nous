import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "用户管理 - 天工",
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
