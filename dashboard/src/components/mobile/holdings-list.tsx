"use client";

import { cn } from "@/lib/utils";
import { TrendingUp, TrendingDown } from "lucide-react";

interface HoldingItem {
  symbol: string;
  name: string;
  weight_pct: number;
  pnl_pct?: number;
  market_value?: number;
  shares?: number;
  cost_price?: number;
  current_price?: number;
  pnl?: number;
  factor_score?: number;
  expected_return?: number;
  sector?: string;
}

interface HoldingsListProps {
  items: HoldingItem[];
  maxItems?: number;
  showPnl?: boolean;
  showSector?: boolean;
  showFactor?: boolean;
  variant?: "sim" | "quant" | "live";
  className?: string;
}

function formatMoney(val: number | undefined): string {
  if (val == null) return "--";
  if (Math.abs(val) >= 1e8) return `${(val / 1e8).toFixed(2)}亿`;
  if (Math.abs(val) >= 1e4) return `${(val / 1e4).toFixed(1)}万`;
  return val.toFixed(2);
}

export function HoldingsList({
  items,
  maxItems = 10,
  showPnl = true,
  showSector = false,
  showFactor = false,
  variant = "sim",
  className,
}: HoldingsListProps) {
  const display = items.slice(0, maxItems);

  return (
    <div className={cn("space-y-1", className)}>
      {/* Column headers */}
      <div className="flex items-center text-[9px] text-zinc-600 px-2 pb-1 border-b border-zinc-800/50">
        <span className="flex-1">标的</span>
        {showSector && <span className="w-10 text-right">行业</span>}
        <span className="w-14 text-right">权重</span>
        {showPnl && <span className="w-16 text-right">盈亏</span>}
        {variant === "sim" && <span className="w-14 text-right">市值</span>}
        {showFactor && <span className="w-12 text-right">因子</span>}
        {showFactor && <span className="w-14 text-right">预期收益</span>}
      </div>

      {display.map((item, i) => (
        <div
          key={item.symbol + i}
          className={cn(
            "flex items-center px-2 py-1.5 rounded-lg transition-colors hover:bg-zinc-800/30",
            "text-[11px]"
          )}
        >
          {/* Symbol + name */}
          <div className="flex-1 min-w-0">
            <span className="text-zinc-200 font-medium">{item.symbol}</span>
            <span className="text-zinc-500 ml-1.5 truncate">{item.name}</span>
          </div>

          {/* Sector */}
          {showSector && (
            <span className="w-10 text-right text-zinc-500 text-[10px] truncate">
              {item.sector ?? "--"}
            </span>
          )}

          {/* Weight */}
          <span className="w-14 text-right tabular-nums text-zinc-300">
            {(item.weight_pct * 100).toFixed(1)}%
          </span>

          {/* PnL */}
          {showPnl && (
            <span className={cn(
              "w-16 text-right tabular-nums font-medium",
              item.pnl_pct != null && item.pnl_pct >= 0 ? "text-emerald-400" : "text-rose-400"
            )}>
              {item.pnl_pct != null ? `${item.pnl_pct >= 0 ? "+" : ""}${item.pnl_pct.toFixed(2)}%` : "--"}
            </span>
          )}

          {/* Market value (sim only) */}
          {variant === "sim" && (
            <span className="w-14 text-right tabular-nums text-zinc-400 text-[10px]">
              {formatMoney(item.market_value)}
            </span>
          )}

          {/* Factor score */}
          {showFactor && (
            <span className="w-12 text-right tabular-nums text-zinc-400 text-[10px]">
              {item.factor_score?.toFixed(2) ?? "--"}
            </span>
          )}

          {/* Expected return */}
          {showFactor && (
            <span className={cn(
              "w-14 text-right tabular-nums text-[10px]",
              item.expected_return != null && item.expected_return >= 0 ? "text-emerald-400" : "text-rose-400"
            )}>
              {item.expected_return != null ? `${item.expected_return >= 0 ? "+" : ""}${item.expected_return.toFixed(2)}%` : "--"}
            </span>
          )}
        </div>
      ))}

      {items.length > maxItems && (
        <p className="text-center text-[9px] text-zinc-600 pt-1">
          还有 {items.length - maxItems} 只...
        </p>
      )}
    </div>
  );
}
