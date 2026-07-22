"use client";

import { cn } from "@/lib/utils";
import { ReactNode, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

interface KpiItem {
  icon: ReactNode;
  label: string;
  value: string | number;
  subtitle?: string;
  color: "emerald" | "amber" | "violet" | "blue" | "rose";
  detail?: ReactNode; // expandable detail
}

interface KpiGridProps {
  items: KpiItem[];
}

const borderColorMap = {
  emerald: "border-l-emerald-500",
  amber: "border-l-amber-500",
  violet: "border-l-violet-500",
  blue: "border-l-blue-500",
  rose: "border-l-rose-500",
};

const bgColorMap = {
  emerald: "bg-emerald-500/5",
  amber: "bg-amber-500/5",
  violet: "bg-violet-500/5",
  blue: "bg-blue-500/5",
  rose: "bg-rose-500/5",
};

export function KpiGrid({ items }: KpiGridProps) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  return (
    <div className="grid grid-cols-2 gap-2">
      {items.map((item, i) => {
        const isExpanded = expandedIdx === i;
        return (
          <button
            key={i}
            onClick={() =>
              item.detail && setExpandedIdx(isExpanded ? null : i)
            }
            className={cn(
              "relative text-left rounded-xl border border-zinc-800/60 border-l-2 p-3 transition-all duration-200 active:scale-[0.98]",
              borderColorMap[item.color],
              bgColorMap[item.color],
              item.detail && "cursor-pointer"
            )}
          >
            {/* Icon + Label */}
            <div className="flex items-center gap-1.5 mb-1.5">
              <span className="opacity-60">{item.icon}</span>
              <span className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">
                {item.label}
              </span>
              {item.detail && (
                <span className="ml-auto text-zinc-600">
                  {isExpanded ? (
                    <ChevronUp className="w-3 h-3" />
                  ) : (
                    <ChevronDown className="w-3 h-3" />
                  )}
                </span>
              )}
            </div>

            {/* Value */}
            <p className="text-xl font-bold tabular-nums text-zinc-100">
              {item.value}
            </p>

            {/* Subtitle */}
            {item.subtitle && (
              <p className="text-[10px] text-zinc-500 mt-0.5">{item.subtitle}</p>
            )}

            {/* Expandable detail */}
            {isExpanded && item.detail && (
              <div className="mt-2 pt-2 border-t border-zinc-800/50 text-xs text-zinc-400 animate-in fade-in slide-in-from-top-2 duration-200">
                {item.detail}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
