"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

interface IndexItem {
  name: string;
  value: string;
  change: number; // percentage
}

// Static mock data — in production, fetch from API
const MOCK_INDICES: IndexItem[] = [
  { name: "上证", value: "4256.32", change: +0.87 },
  { name: "深证", value: "13820.15", change: +1.23 },
  { name: "创业板", value: "3125.68", change: -0.45 },
  { name: "恒指", value: "24150.80", change: +0.62 },
  { name: "恒科", value: "5820.45", change: +1.85 },
  { name: "金龙", value: "8125.30", change: +2.10 },
];

export function IndexBar() {
  const [indices] = useState<IndexItem[]>(MOCK_INDICES);

  return (
    <div className="relative">
      {/* Gradient fade edges */}
      <div className="absolute left-0 top-0 bottom-0 w-6 bg-gradient-to-r from-zinc-950 to-transparent z-10 pointer-events-none" />
      <div className="absolute right-0 top-0 bottom-0 w-6 bg-gradient-to-r from-transparent to-zinc-950 z-10 pointer-events-none" />

      {/* Scrollable row */}
      <div className="flex gap-3 overflow-x-auto scrollbar-none px-1 py-2">
        {indices.map((item) => (
          <div
            key={item.name}
            className="shrink-0 flex items-center gap-2 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-800/50"
          >
            <span className="text-[11px] font-medium text-zinc-400">
              {item.name}
            </span>
            <span className="text-xs font-bold tabular-nums text-zinc-200">
              {item.value}
            </span>
            <span
              className={cn(
                "text-[11px] font-semibold tabular-nums",
                item.change >= 0 ? "text-emerald-400" : "text-red-400"
              )}
            >
              {item.change >= 0 ? "+" : ""}
              {item.change.toFixed(2)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
