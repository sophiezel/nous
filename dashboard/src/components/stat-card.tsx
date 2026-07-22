"use client";

import { cn } from "@/lib/utils";
import { ObfuscatedValue } from "@/components/ObfuscatedValue";

interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: React.ReactNode;
  trend?: "up" | "down" | "neutral";
  className?: string;
  /** Use CSS pseudo-element rendering — value not in DOM text nodes */
  obfuscate?: boolean;
}

export function StatCard({
  title,
  value,
  subtitle,
  icon,
  trend,
  className,
  obfuscate = false,
}: StatCardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-zinc-800 bg-zinc-900/50 p-4",
        className
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-zinc-500 font-medium uppercase tracking-wider">
          {title}
        </span>
        {icon && <span className="text-zinc-400">{icon}</span>}
      </div>
      <div className="flex items-baseline gap-2">
        {obfuscate ? (
          <ObfuscatedValue
            value={value}
            fontSize="text-2xl"
            fontWeight="font-bold"
          />
        ) : (
          <span className="text-2xl font-bold tabular-nums">{value}</span>
        )}
        {trend && (
          <span
            className={cn(
              "text-xs font-medium",
              trend === "up" && "text-emerald-400",
              trend === "down" && "text-red-400",
              trend === "neutral" && "text-zinc-400"
            )}
          >
            {trend === "up" ? "↑" : trend === "down" ? "↓" : "→"}
          </span>
        )}
      </div>
      {subtitle && (
        <p className="text-xs text-zinc-500 mt-1">{subtitle}</p>
      )}
    </div>
  );
}
