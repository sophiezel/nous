"use client";

import { ModuleCard } from "@/components/mobile/module-card";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { HoldingsList } from "@/components/mobile/holdings-list";
import { cn } from "@/lib/utils";
import { TrendingUp, TrendingDown, History, Wallet } from "lucide-react";

interface PositionItem {
  symbol: string;
  name: string;
  weight_pct: number;
  pnl_pct: number;
  market_value: number;
  shares: number;
  cost_price: number;
  current_price: number;
  pnl: number;
}

interface NavPoint {
  trade_date: string;
  nav: number;
  daily_return: number;
  total_pnl: number;
  total_invested: number;
}

interface TradeItem {
  trade_date: string;
  symbol: string;
  name: string;
  direction: string;
  price: number;
  shares: number;
  amount: number;
  pnl: number | null;
}

interface Props {
  market: string;
  positions: PositionItem[];
  navData: NavPoint[];
  trades: TradeItem[];
  totalPnl: number;
  totalPnlPct: number;
}

export function PaperDetailClient({ market, positions, navData, trades, totalPnl, totalPnlPct }: Props) {
  const navLatest = navData[0];
  const navValues = navData.map(n => n.nav).reverse();
  const navLabels = navData.map(n => n.trade_date).reverse();
  const isPnlPositive = totalPnl >= 0;

  return (
    <div className="space-y-3 pb-2">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Wallet className="w-4 h-4 text-emerald-400" />
          <h1 className="text-sm font-bold text-zinc-100">{market}模拟盘</h1>
        </div>
        <span className={cn(
          "text-xs font-bold tabular-nums",
          isPnlPositive ? "text-emerald-400" : "text-rose-400"
        )}>
          {isPnlPositive ? "+" : ""}{totalPnlPct.toFixed(2)}%
        </span>
      </div>

      {/* NAV Overview */}
      {navLatest && (
        <ModuleCard
          label="净值" title={`最新 ${navLatest.nav.toFixed(4)}`}
          accent={navLatest.daily_return >= 0 ? "emerald" : "rose"}
          icon={navLatest.daily_return >= 0 ? <TrendingUp className="w-3.5 h-3.5 text-emerald-400" /> : <TrendingDown className="w-3.5 h-3.5 text-rose-400" />}
          metric={navLatest.nav.toFixed(4)}
          metricSub={`${navLatest.daily_return >= 0 ? "+" : ""}${navLatest.daily_return.toFixed(2)}%`}
          subMetrics={[
            { label: "总投入", value: `${(navLatest.total_invested).toFixed(0)}`, color: "blue" as const },
            { label: "总盈亏", value: `${navLatest.total_pnl >= 0 ? "+" : ""}${navLatest.total_pnl.toFixed(0)}`, color: navLatest.total_pnl >= 0 ? "emerald" as const : "rose" as const },
          ]}
          chart={navValues.length >= 2 ? <ZoomableSparkline data={navValues} labels={navLabels} color="#10b981" height={40} className="w-full" /> : undefined}
        />
      )}

      {/* Holdings */}
      <ModuleCard
        label="持仓" title={`${positions.length} 只标的`}
        accent="emerald"
        icon={<Wallet className="w-3.5 h-3.5 text-emerald-400" />}
        body={
          <HoldingsList
            items={positions.map(p => ({
              symbol: p.symbol,
              name: p.name,
              weight_pct: p.weight_pct,
              pnl_pct: p.pnl_pct,
              market_value: p.market_value,
              shares: p.shares,
              cost_price: p.cost_price,
              current_price: p.current_price,
              pnl: p.pnl,
            }))}
            variant="sim"
            maxItems={15}
          />
        }
      />

      {/* Recent Trades */}
      <ModuleCard
        label="交易记录" title={`最近 ${trades.length} 笔`}
        accent="amber"
        icon={<History className="w-3.5 h-3.5 text-amber-400" />}
        body={
          <div className="space-y-1">
            {trades.slice(0, 20).map((t, i) => (
              <div key={i} className="flex items-center justify-between text-[10px] px-2 py-1 rounded hover:bg-zinc-800/30">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-zinc-500 w-14 tabular-nums">{t.trade_date.slice(5)}</span>
                  <span className={cn(
                    "text-[9px] px-1 py-0.5 rounded font-medium",
                    t.direction === "buy" ? "bg-emerald-500/15 text-emerald-400" : "bg-rose-500/15 text-rose-400"
                  )}>
                    {t.direction === "buy" ? "买" : "卖"}
                  </span>
                  <span className="text-zinc-200 font-medium">{t.symbol}</span>
                  <span className="text-zinc-500 truncate">{t.name}</span>
                </div>
                <div className="flex items-center gap-2 tabular-nums">
                  <span className="text-zinc-400">{t.shares}股</span>
                  <span className="text-zinc-400">@{t.price.toFixed(2)}</span>
                  {t.pnl != null && (
                    <span className={cn("font-medium", t.pnl >= 0 ? "text-emerald-400" : "text-rose-400")}>
                      {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(0)}
                    </span>
                  )}
                </div>
              </div>
            ))}
            {trades.length === 0 && (
              <p className="text-[10px] text-zinc-500 text-center py-2">暂无交易记录</p>
            )}
          </div>
        }
      />
    </div>
  );
}
