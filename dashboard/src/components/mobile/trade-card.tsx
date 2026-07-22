"use client";

import { cn } from "@/lib/utils";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

interface TradeCardProps {
  title: string;
  nav: number | null;
  dailyReturn: number | null;
  totalPnl: number | null;
  totalPnlPct: number | null;
  positionCount: number;
  navChart?: React.ReactNode;
  href?: string;
  accent?: "emerald" | "violet" | "blue" | "amber";
  className?: string;
}

const accentBorderMap: Record<string, string> = {
  emerald: "border-emerald-500/20",
  violet: "border-violet-500/20",
  blue: "border-blue-500/20",
  amber: "border-amber-500/20",
};

export function TradeCard({
  title,
  nav,
  dailyReturn,
  totalPnl,
  totalPnlPct,
  positionCount,
  navChart,
  href,
  accent = "emerald",
  className,
}: TradeCardProps) {
  const isPositive = dailyReturn != null && dailyReturn >= 0;
  const pnlPositive = totalPnl != null && totalPnl >= 0;

  return (
    <div
      className={cn(
        "rounded-2xl border bg-zinc-900/50 overflow-hidden",
        accentBorderMap[accent],
        className
      )}
    >
      <div className="px-4 pt-3 pb-2">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">
            {title}
          </span>
          {dailyReturn != null && (
            <div className={cn(
              "flex items-center gap-1 text-xs font-semibold tabular-nums",
              isPositive ? "text-emerald-400" : "text-rose-400"
            )}>
              {isPositive ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
              {dailyReturn >= 0 ? "+" : ""}{dailyReturn.toFixed(2)}%
            </div>
          )}
        </div>

        {/* NAV */}
        <div className="flex items-baseline gap-2">
          <span className="text-xl font-bold tabular-nums text-zinc-100">
            {nav != null ? nav.toFixed(4) : "--"}
          </span>
          <span className="text-[10px] text-zinc-500">净值</span>
        </div>

        {/* PnL + positions */}
        <div className="flex items-center gap-3 mt-1.5">
          {totalPnl != null && (
            <span className={cn(
              "text-[11px] font-medium tabular-nums",
              pnlPositive ? "text-emerald-400" : "text-rose-400"
            )}>
              总盈亏 {pnlPositive ? "+" : ""}{totalPnl.toFixed(2)}{totalPnlPct != null ? ` (${totalPnlPct >= 0 ? "+" : ""}${totalPnlPct.toFixed(2)}%)` : ""}
            </span>
          )}
          <span className="text-[10px] text-zinc-500">
            {positionCount} 只持仓
          </span>
        </div>
      </div>

      {/* Chart */}
      {navChart && <div className="px-2 pb-2">{navChart}</div>}

      {/* Link footer */}
      {href && (
        <a
          href={href}
          className="block text-center text-[10px] text-zinc-500 hover:text-zinc-300 py-2 border-t border-zinc-800/50 transition-colors"
        >
          查看详情 →
        </a>
      )}
    </div>
  );
}
