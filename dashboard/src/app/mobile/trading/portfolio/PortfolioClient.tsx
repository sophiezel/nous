"use client";

import { ModuleCard } from "@/components/mobile/module-card";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { HoldingsList } from "@/components/mobile/holdings-list";
import { cn } from "@/lib/utils";
import { BarChart3, TrendingUp, TrendingDown, Activity, Shield, AlertTriangle } from "lucide-react";
import type { LivePosition, LiveDiagnosis, BenchmarkComparison } from "@/lib/types";

interface NavPoint {
  trade_date: string;
  nav: number;
  daily_return: number;
}

interface Props {
  positions: LivePosition[];
  diagnosis: LiveDiagnosis[];
  benchmark: BenchmarkComparison[];
  navData: NavPoint[];
}

function pctStr(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function PortfolioClient({ positions, diagnosis, benchmark, navData }: Props) {
  const navValues = navData.map(n => n.nav).reverse();
  const navLabels = navData.map(n => n.trade_date).reverse();

  return (
    <div className="space-y-3 pb-2">
      <div className="flex items-center gap-2 mb-1">
        <BarChart3 className="w-4 h-4 text-cyan-400" />
        <h1 className="text-sm font-bold text-zinc-100">实盘分析</h1>
      </div>

      {/* NAV */}
      {navData.length > 0 && (
        <ModuleCard
          label="净值" title={navData[0]?.trade_date ?? ""}
          accent={navData[0]?.daily_return >= 0 ? "emerald" : "rose"}
          icon={navData[0]?.daily_return >= 0 ? <TrendingUp className="w-3.5 h-3.5 text-emerald-400" /> : <TrendingDown className="w-3.5 h-3.5 text-rose-400" />}
          metric={navData[0]?.nav.toFixed(4) ?? "--"}
          metricSub={navData[0]?.daily_return != null ? pctStr(navData[0].daily_return) : undefined}
          chart={navValues.length >= 2 ? <ZoomableSparkline data={navValues} labels={navLabels} color="#06b6d4" height={40} className="w-full" /> : undefined}
        />
      )}

      {/* Benchmark Comparison */}
      {benchmark.length > 0 && (
        <ModuleCard
          label="基准" title="收益对比"
          accent="emerald"
          icon={<Activity className="w-3.5 h-3.5 text-emerald-400" />}
          body={
            <div className="space-y-1.5 px-1">
              {benchmark.map((b) => (
                <div key={b.period} className="flex items-center justify-between text-[10px]">
                  <span className="text-zinc-500 w-10">{b.period}</span>
                  <span className="tabular-nums w-16 text-right text-zinc-200">{pctStr(b.portfolio_return)}</span>
                  <span className="text-zinc-600 w-8 text-center">vs</span>
                  <span className="tabular-nums w-16 text-right text-zinc-400">{pctStr(b.benchmark_return)}</span>
                  <span className={cn("tabular-nums w-14 text-right font-medium", b.alpha >= 0 ? "text-emerald-400" : "text-rose-400")}>
                    α{pctStr(b.alpha)}
                  </span>
                </div>
              ))}
            </div>
          }
        />
      )}

      {/* Desensitized Holdings */}
      <ModuleCard
        label="持仓" title={`${positions.length} 只标的 (脱敏)`}
        accent="cyan"
        icon={<BarChart3 className="w-3.5 h-3.5 text-cyan-400" />}
        body={
          <HoldingsList
            items={positions.map(p => ({
              symbol: p.symbol,
              name: "",
              weight_pct: p.weight_pct,
              pnl_pct: p.pnl_pct,
              sector: p.sector,
            }))}
            variant="live"
            showPnl
            showSector
            maxItems={15}
          />
        }
      />

      {/* Diagnosis */}
      {diagnosis.length > 0 && (
        <ModuleCard
          label="诊断" title="组合健康度"
          accent="violet"
          icon={<Activity className="w-3.5 h-3.5 text-violet-400" />}
          body={
            <div className="grid grid-cols-2 gap-2 px-1">
              {diagnosis.map((d) => (
                <div key={d.metric} className="flex items-center justify-between text-[10px] px-2 py-1.5 rounded-lg bg-zinc-800/30">
                  <span className="text-zinc-400">{d.metric}</span>
                  <div className="flex items-center gap-1.5">
                    <span className={cn(
                      "tabular-nums font-medium",
                      d.status === "good" ? "text-emerald-400" : d.status === "warning" ? "text-amber-400" : "text-rose-400"
                    )}>
                      {d.value.toFixed(2)}
                    </span>
                    <span className={cn(
                      "w-1.5 h-1.5 rounded-full",
                      d.status === "good" ? "bg-emerald-400" : d.status === "warning" ? "bg-amber-400" : "bg-rose-400"
                    )} />
                  </div>
                </div>
              ))}
            </div>
          }
        />
      )}
    </div>
  );
}
