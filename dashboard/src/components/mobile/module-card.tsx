"use client";

import { cn } from "@/lib/utils";
import { ChevronRight } from "lucide-react";
import Link from "next/link";

interface ModuleCardProps {
  /** Module number label (M1-M9) */
  label: string;
  /** Card title */
  title: string;
  /** Icon or emoji */
  icon?: React.ReactNode;
  /** Primary metric display */
  metric?: string | number;
  /** Metric subtitle / unit */
  metricSub?: React.ReactNode;
  /** Sub-metrics (optional row of small stats) */
  subMetrics?: { label: string; value: string; color?: string }[];
  /** Mini chart (optional) */
  chart?: React.ReactNode;
  /** Link href — if provided, entire card becomes clickable */
  href?: string;
  /** Click handler (overrides href) */
  onClick?: () => void;
  /** Accent color for left border */
  accent?: "emerald" | "amber" | "blue" | "violet" | "rose" | "cyan";
  /** Badge text (e.g. "数据接入中") */
  badge?: string;
  /** Custom body content (overrides metric/subMetrics/chart) */
  body?: React.ReactNode;
  /** Extra class */
  className?: string;
}

const accentMap: Record<string, string> = {
  emerald: "bg-emerald-500/[0.03]",
  amber: "bg-amber-500/[0.03]",
  blue: "bg-blue-500/[0.03]",
  violet: "bg-violet-500/[0.03]",
  rose: "bg-rose-500/[0.03]",
  cyan: "bg-cyan-500/[0.03]",
};

const accentDotMap: Record<string, string> = {
  emerald: "bg-emerald-400",
  amber: "bg-amber-400",
  blue: "bg-blue-400",
  violet: "bg-violet-400",
  rose: "bg-rose-400",
  cyan: "bg-cyan-400",
};

export function ModuleCard({
  label,
  title,
  icon,
  metric,
  metricSub,
  subMetrics,
  chart,
  body,
  href,
  onClick,
  accent = "emerald",
  badge,
  className,
}: ModuleCardProps) {
  const content = (
    <div
      className={cn(
        "relative rounded-2xl border border-zinc-800/80 overflow-hidden",
        accentMap[accent],
        (href || onClick) && "active:scale-[0.98] transition-transform",
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <div className="flex items-center gap-2">
          {icon && <span className="text-sm">{icon}</span>}
          <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">
            {label}
          </span>
          <span className="text-xs font-medium text-zinc-300">{title}</span>
          {badge && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500">
              {badge}
            </span>
          )}
        </div>
        {(href || onClick) && (
          <ChevronRight className="w-3.5 h-3.5 text-zinc-600" />
        )}
      </div>

      {/* Body */}
      <div className="px-3 py-2 space-y-1.5">
        {body ? body : (
          <>
            {/* Primary metric */}
            {metric !== undefined && (
              <div className="flex items-baseline gap-1.5">
                <span className="text-lg font-bold tabular-nums text-zinc-100">
                  {metric}
                </span>
                {metricSub && (
                  <span className="text-[10px] text-zinc-500">{metricSub}</span>
                )}
              </div>
            )}

            {/* Sub-metrics row */}
            {subMetrics && subMetrics.length > 0 && (
              <div className="flex gap-3 flex-wrap">
                {subMetrics.map((sm, i) => (
                  <div key={i} className="flex items-center gap-1">
                    {sm.color && (
                      <span
                        className={cn("w-1.5 h-1.5 rounded-full", accentDotMap[sm.color] || sm.color)}
                      />
                    )}
                    <span className="text-[10px] text-zinc-500">{sm.label}</span>
                    <span className="text-[11px] font-medium tabular-nums text-zinc-300">
                      {sm.value}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Mini chart */}
            {chart && <div className="pt-0.5">{chart}</div>}
          </>
        )}
      </div>
    </div>
  );

  if (href) {
    return (
      <Link href={href} className="block">
        {content}
      </Link>
    );
  }

  if (onClick) {
    return (
      <button onClick={onClick} className="block w-full text-left">
        {content}
      </button>
    );
  }

  return content;
}
