"use client";

import { cn } from "@/lib/utils";

interface AttributionItem {
  label: string;
  contribution: number;
  color?: string;
}

interface AttributionWaterfallProps {
  title?: string;
  totalReturn: number;
  items: AttributionItem[];
  className?: string;
}

function barColor(val: number): string {
  if (val >= 0) return "bg-emerald-500";
  return "bg-rose-500";
}

export function AttributionWaterfall({
  title,
  totalReturn,
  items,
  className,
}: AttributionWaterfallProps) {
  if (!items || items.length === 0) {
    return (
      <div className={cn("text-[10px] text-zinc-500 text-center py-4", className)}>
        暂无归因数据
      </div>
    );
  }

  const maxAbs = Math.max(
    ...items.map((i) => Math.abs(i.contribution)),
    Math.abs(totalReturn),
    0.01
  );

  // Running sum for waterfall display
  let running = 0;

  return (
    <div className={cn("space-y-2", className)}>
      {title && (
        <p className="text-[10px] font-semibold text-zinc-400">{title}</p>
      )}

      <div className="space-y-1">
        {items.map((item, i) => {
          const pct = (item.contribution / maxAbs) * 100;
          const isPositive = item.contribution >= 0;
          running += item.contribution;

          return (
            <div key={i} className="flex items-center gap-2">
              {/* Label */}
              <span className="w-16 text-[10px] text-zinc-400 text-right truncate">
                {item.label}
              </span>

              {/* Bar */}
              <div className="flex-1 h-5 relative">
                {/* Zero line */}
                <div className="absolute inset-y-0 left-1/2 w-px bg-zinc-700" />
                {/* Bar */}
                <div
                  className={cn(
                    "absolute top-0.5 h-4 rounded-sm transition-all",
                    barColor(item.contribution)
                  )}
                  style={{
                    left: isPositive ? "50%" : `${50 - Math.abs(pct)}%`,
                    width: `${Math.abs(pct)}%`,
                    minWidth: pct !== 0 ? "4px" : "0",
                  }}
                />
              </div>

              {/* Value */}
              <span
                className={cn(
                  "w-14 text-[10px] tabular-nums text-right font-medium",
                  isPositive ? "text-emerald-400" : "text-rose-400"
                )}
              >
                {item.contribution >= 0 ? "+" : ""}{item.contribution.toFixed(2)}%
              </span>
            </div>
          );
        })}

        {/* Total bar */}
        <div className="flex items-center gap-2 pt-1 border-t border-zinc-800/50 mt-1">
          <span className="w-16 text-[10px] text-zinc-300 font-medium text-right truncate">
            总计
          </span>
          <div className="flex-1 h-5 relative">
            <div className="absolute inset-y-0 left-1/2 w-px bg-zinc-600" />
            <div
              className={cn(
                "absolute top-0.5 h-4 rounded-sm",
                totalReturn >= 0 ? "bg-emerald-400" : "bg-rose-400"
              )}
              style={{
                left: totalReturn >= 0 ? "50%" : `${50 - Math.min(100, Math.abs(totalReturn / maxAbs) * 100)}%`,
                width: `${Math.min(100, Math.abs(totalReturn / maxAbs) * 100)}%`,
                minWidth: "4px",
              }}
            />
          </div>
          <span
            className={cn(
              "w-14 text-[10px] tabular-nums text-right font-bold",
              totalReturn >= 0 ? "text-emerald-300" : "text-rose-300"
            )}
          >
            {totalReturn >= 0 ? "+" : ""}{totalReturn.toFixed(2)}%
          </span>
        </div>
      </div>
    </div>
  );
}
