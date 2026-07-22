"use client";

import { ModuleCard } from "@/components/mobile/module-card";
import { TradeCard } from "@/components/mobile/trade-card";
import { SparklineChart } from "@/components/mobile/sparkline";
import { TrendingUp, Shield, BarChart3, Activity } from "lucide-react";
import { cn } from "@/lib/utils";
import type { BenchmarkComparison, RiskOverview } from "@/lib/types";

interface PaperSummary {
  positions: number;
  navLatest: number | null;
  dailyReturn: number | null;
  totalPnl: number;
  totalPnlPct: number;
  navData: number[];
}

interface QuantSummary {
  positions: number;
  navLatest: number | null;
  dailyReturn: number | null;
  navData: number[];
}

interface Props {
  paperA: PaperSummary;
  paperHk: PaperSummary;
  quantA: QuantSummary;
  quantHk: QuantSummary;
  livePositions: number;
  liveAvgPnl: number | null;
  risk: RiskOverview | null;
  benchmark: BenchmarkComparison[];
}

function pctStr(v: number | null): string {
  if (v == null) return "--";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function TradingOverviewClient(props: Props) {
  const { paperA, paperHk, quantA, quantHk, livePositions, liveAvgPnl, risk, benchmark } = props;

  return (
    <div className="space-y-3 pb-2">
      {/* Header */}
      <div className="flex items-center gap-2 mb-1">
        <TrendingUp className="w-4 h-4 text-emerald-400" />
        <h1 className="text-sm font-bold text-zinc-100">交易总览</h1>
      </div>

      {/* ─── 模拟盘 A股 ─── */}
      <TradeCard
        title="模拟盘 · A股"
        nav={paperA.navLatest}
        dailyReturn={paperA.dailyReturn}
        totalPnl={paperA.totalPnl}
        totalPnlPct={paperA.totalPnlPct}
        positionCount={paperA.positions}
        accent="emerald"
        navChart={
          paperA.navData.length >= 2 ? (
            <SparklineChart data={paperA.navData} color="#10b981" height={32} className="w-full h-8" />
          ) : undefined
        }
        href="/mobile/trading/paper/a"
      />

      {/* ─── 模拟盘 港股 ─── */}
      <TradeCard
        title="模拟盘 · 港股"
        nav={paperHk.navLatest}
        dailyReturn={paperHk.dailyReturn}
        totalPnl={paperHk.totalPnl}
        totalPnlPct={paperHk.totalPnlPct}
        positionCount={paperHk.positions}
        accent="blue"
        navChart={
          paperHk.navData.length >= 2 ? (
            <SparklineChart data={paperHk.navData} color="#3b82f6" height={32} className="w-full h-8" />
          ) : undefined
        }
        href="/mobile/trading/paper/hk"
      />

      {/* ─── 量化盘 A股 ─── */}
      <TradeCard
        title="量化盘 · A股"
        nav={quantA.navLatest}
        dailyReturn={quantA.dailyReturn}
        totalPnl={null}
        totalPnlPct={null}
        positionCount={quantA.positions}
        accent="violet"
        navChart={
          quantA.navData.length >= 2 ? (
            <SparklineChart data={quantA.navData} color="#8b5cf6" height={32} className="w-full h-8" />
          ) : undefined
        }
        href="/mobile/trading/quant/a"
      />

      {/* ─── 量化盘 港股 ─── */}
      <TradeCard
        title="量化盘 · 港股"
        nav={quantHk.navLatest}
        dailyReturn={quantHk.dailyReturn}
        totalPnl={null}
        totalPnlPct={null}
        positionCount={quantHk.positions}
        accent="amber"
        navChart={
          quantHk.navData.length >= 2 ? (
            <SparklineChart data={quantHk.navData} color="#f59e0b" height={32} className="w-full h-8" />
          ) : undefined
        }
        href="/mobile/trading/quant/hk"
      />

      {/* ─── 实盘分析 + 风控 ─── */}
      <div className="grid grid-cols-2 gap-2">
        <ModuleCard
          label="实盘" title="持仓分析"
          icon={<BarChart3 className="w-3.5 h-3.5 text-cyan-400" />}
          metric={`${livePositions}只`}
          metricSub={liveAvgPnl != null ? pctStr(liveAvgPnl) : undefined}
          subMetrics={benchmark.slice(0, 2).map(b => ({
            label: b.period,
            value: `${b.alpha >= 0 ? "+" : ""}${b.alpha.toFixed(2)}%`,
            color: b.alpha >= 0 ? "emerald" as const : "rose" as const,
          }))}
          href="/mobile/trading/portfolio"
          accent="cyan"
        />
        <ModuleCard
          label="风控" title="统一风控"
          icon={<Shield className="w-3.5 h-3.5 text-rose-400" />}
          metric={risk ? `${risk.current_drawdown_pct.toFixed(1)}%` : "--"}
          metricSub="当前回撤"
          subMetrics={risk ? [
            { label: "VaR95", value: `${(risk.var_95 * 100).toFixed(1)}%`, color: risk.var_95 > 0.03 ? "rose" as const : "emerald" as const },
            { label: "Sharpe", value: risk.sharpe.toFixed(2), color: risk.sharpe > 1 ? "emerald" as const : "amber" as const },
          ] : undefined}
          href="/mobile/trading/risk"
          accent="rose"
        />
      </div>

      {/* ─── 基准对比 ─── */}
      {benchmark.length > 0 && (
        <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/30 p-3">
          <p className="text-[10px] font-semibold text-zinc-400 mb-2 flex items-center gap-1.5">
            <Activity className="w-3 h-3" />
            基准对比
          </p>
          <div className="space-y-1.5">
            {benchmark.map((b) => (
              <div key={b.period} className="flex items-center justify-between text-[10px]">
                <span className="text-zinc-500 w-10">{b.period}</span>
                <span className={cn("tabular-nums w-16 text-right", b.portfolio_return >= 0 ? "text-emerald-400" : "text-rose-400")}>
                  {pctStr(b.portfolio_return)}
                </span>
                <span className="text-zinc-600 w-8 text-center">vs</span>
                <span className="tabular-nums w-16 text-right text-zinc-400">
                  {pctStr(b.benchmark_return)}
                </span>
                <span className={cn("tabular-nums w-14 text-right font-medium", b.alpha >= 0 ? "text-emerald-400" : "text-rose-400")}>
                  α {pctStr(b.alpha)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
