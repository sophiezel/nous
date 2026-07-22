"use client";

import { ModuleCard } from "@/components/mobile/module-card";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { HoldingsList } from "@/components/mobile/holdings-list";
import { FactorHeatmap } from "@/components/mobile/factor-heatmap";
import { AttributionWaterfall } from "@/components/mobile/attribution-waterfall";
import { cn } from "@/lib/utils";
import { BrainCircuit, TrendingUp, TrendingDown, History, Activity } from "lucide-react";
import type { QuantPosition, QuantTrade, FactorIC, QuantNav } from "@/lib/types";

interface NavPoint {
  trade_date: string;
  nav: number;
  daily_return: number;
  benchmark_return: number;
}

interface Props {
  market: string;
  positions: QuantPosition[];
  navData: NavPoint[];
  trades: QuantTrade[];
  factorIC: FactorIC[];
}

export function QuantDetailClient({ market, positions, navData, trades, factorIC }: Props) {
  const navLatest = navData[0];
  const navValues = navData.map(n => n.nav).reverse();
  const navLabels = navData.map(n => n.trade_date).reverse();
  const benchmarkValues = navData.map(n => n.benchmark_return).reverse();

  // Attribution: sector contributions (approximate from positions)
  const sectorMap = new Map<string, { weight: number; ret: number }>();
  for (const p of positions) {
    const existing = sectorMap.get(p.sector) || { weight: 0, ret: 0 };
    existing.weight += p.weight_pct;
    existing.ret += p.weight_pct * p.expected_return;
    sectorMap.set(p.sector, existing);
  }
  const attributionItems = Array.from(sectorMap.entries()).map(([sector, data]) => ({
    label: sector.length > 4 ? sector.slice(0, 4) : sector,
    contribution: data.ret / Math.max(data.weight, 0.01),
  }));

  return (
    <div className="space-y-3 pb-2">
      {/* Header */}
      <div className="flex items-center gap-2 mb-1">
        <BrainCircuit className="w-4 h-4 text-violet-400" />
        <h1 className="text-sm font-bold text-zinc-100">{market}量化盘</h1>
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
            { label: "基准", value: `${navLatest.benchmark_return >= 0 ? "+" : ""}${navLatest.benchmark_return.toFixed(2)}%`, color: navLatest.benchmark_return >= 0 ? "emerald" as const : "rose" as const },
          ]}
          chart={navValues.length >= 2 ? <ZoomableSparkline data={navValues} labels={navLabels} color="#8b5cf6" height={40} className="w-full" /> : undefined}
        />
      )}

      {/* Positions */}
      <ModuleCard
        label="持仓" title={`${positions.length} 只标的`}
        accent="violet"
        icon={<Activity className="w-3.5 h-3.5 text-violet-400" />}
        body={
          <HoldingsList
            items={positions.map(p => ({
              symbol: p.symbol,
              name: p.name,
              weight_pct: p.weight_pct,
              factor_score: p.factor_score,
              expected_return: p.expected_return,
              sector: p.sector,
            }))}
            variant="quant"
            showSector
            showFactor
            maxItems={15}
          />
        }
      />

      {/* Factor IC Heatmap */}
      {factorIC.length > 0 && (
        <ModuleCard
          label="因子IC" title={`近${factorIC.length}日`}
          accent="emerald"
          icon={<Activity className="w-3.5 h-3.5 text-emerald-400" />}
          body={<FactorHeatmap data={factorIC} />}
        />
      )}

      {/* Attribution Waterfall */}
      {attributionItems.length > 0 && (
        <ModuleCard
          label="归因分析" title="行业贡献"
          accent="amber"
          icon={<Activity className="w-3.5 h-3.5 text-amber-400" />}
          body={
            <AttributionWaterfall
              totalReturn={navLatest?.daily_return ?? 0}
              items={attributionItems}
            />
          }
        />
      )}

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
                  {t.reason && <span className="text-zinc-600 text-[9px] truncate max-w-[60px]">{t.reason}</span>}
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
