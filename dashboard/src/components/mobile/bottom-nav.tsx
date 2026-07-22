"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard, TrendingUp, Bell, Sparkles,
} from "lucide-react";

const tabs = [
  { href: "/mobile", label: "概览", icon: LayoutDashboard },
  { href: "/mobile/picks", label: "荐股", icon: Sparkles },
  { href: "/mobile/trading", label: "交易", icon: TrendingUp },
  { href: "/mobile/messages", label: "消息", icon: Bell },
];

export function BottomNav() {
  const pathname = usePathname();

  const isActive = (href: string) => {
    if (href === "/mobile") return pathname === "/mobile";
    if (href === "/mobile/messages") return pathname.startsWith("/mobile/messages");
    if (href === "/mobile/trading") return pathname.startsWith("/mobile/trading");
    return pathname.startsWith(href);
  };

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 bg-zinc-950/95 backdrop-blur-xl border-t border-zinc-800/80 safe-area-bottom">
      <div className="flex items-center justify-around h-16 px-1 max-w-lg mx-auto">
        {tabs.map((tab) => {
          const active = isActive(tab.href);
          const Icon = tab.icon;

          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={cn(
                "relative flex flex-col items-center justify-center gap-0.5 min-w-0 flex-1 py-1 transition-all duration-200",
                active ? "-translate-y-0.5" : "translate-y-0"
              )}
            >
              <div className={cn(
                "relative p-1.5 rounded-xl transition-all duration-200",
                active ? "bg-emerald-500/10 scale-110" : "scale-100"
              )}>
                <Icon
                  className={cn(
                    "w-[22px] h-[22px] transition-all duration-200",
                    active ? "text-emerald-400" : "text-zinc-500"
                  )}
                  strokeWidth={active ? 2.5 : 1.8}
                />
              </div>
              <span className={cn(
                "text-[10px] font-medium transition-colors duration-200",
                active ? "text-emerald-400" : "text-zinc-500"
              )}>
                {tab.label}
              </span>
              {active && (
                <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-emerald-400" />
              )}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
