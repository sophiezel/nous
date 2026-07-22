import type { Metadata } from "next";
import "../globals.css";
import { Sidebar } from "@/components/sidebar";

export const metadata: Metadata = {
  title: "天工 Dashboard",
  description: "量化投研数据平台",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="min-h-screen bg-zinc-950 text-zinc-100 antialiased">
        <div className="flex h-screen">
          <Sidebar />
          <main className="flex-1 overflow-y-auto p-6 lg:p-8">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
