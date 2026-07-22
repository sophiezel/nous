"use client";

import { ModuleCard } from "@/components/mobile/module-card";
import { cn } from "@/lib/utils";
import { TrendingUp, TrendingDown, History, BarChart3 } from "lucide-react";
import type { Recommendation, StrategyPerf } from "@/lib/types";

// ── Props ──────────────────────────────────────────────

interface Props {
  aLong: Recommendation[];
  aShort: Recommendation[];
  hkLong: Recommendation[];
  hkShort: Recommendation[];
  history: Recommendation[];
  perf: StrategyPerf[];
}

// ── Helpers ────────────────────────────────────────────

function pctStr(v: number): string {
  if (v == null || !Number.isFinite(v)) return "--";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function pctClass(v: number): string {
  if (v > 0) return "text-emerald-400";
  if (v < 0) return "text-red-400";
  return "text-zinc-400";
}

function strategyLabel(t: string): string {
  if (t === "long_term") return "长期";
  if (t === "short_term") return "短线";
  return t;
}

function quadrantHeader(label: string, count: number, color: "amber" | "emerald") {
  const dotColors: Record<string, string> = { amber: "bg-amber-400", emerald: "bg-emerald-400" };
  return (
    <div className="flex items-center gap-2 mb-2">
      <div className={cn("w-2 h-2 rounded-full", dotColors[color])} />
      <span className="text-xs font-semibold text-zinc-100">{label}</span>
      <span className="text-[10px] text-zinc-500 ml-auto tabular-nums">{count}支</span>
    </div>
  );
}

function QuadrantCard({
  label,
  stocks,
  color,
}: {
  label: string;
  stocks: Recommendation[];
  color: "amber" | "emerald";
}) {
  const borderColors: Record<string, string> = {
    amber: "border-amber-800/30 bg-gradient-to-b from-amber-950/20 to-zinc-950",
    emerald: "border-emerald-800/30 bg-gradient-to-b from-emerald-950/20 to-zinc-950",
  };
  return (
    <div className={cn("rounded-xl border p-3", borderColors[color])}>
      {quadrantHeader(label, stocks.length, color)}
      {stocks.length === 0 ? (
        <p className="text-[10px] text-zinc-600 py-2 text-center">暂无推荐</p>
      ) : (
        <div className="space-y-0.5">
          {stocks.map((s) => (
            <div
              key={`${s.market}-${s.strategy_type}-${s.symbol}`}
              className="py-1 border-b border-zinc-800/30 last:border-0 flex items-center gap-2 text-xs"
            >
              <span className="font-mono text-zinc-500 w-16 shrink-0 text-[10px]">{s.symbol}</span>
              <span className="text-zinc-200 flex-1 truncate">{s.name}</span>
              <span className={cn("tabular-nums text-[10px]", pctClass(s.pnl_pct))}>
                {pctStr(s.pnl_pct)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Strategy Performance Row ──────────────────────────

function toPerfLabel(market: string, strategy: string): string {
  const m = market === "A" ? "A股" : market === "HK" ? "港股" : market;
  const s = strategy === "long_term" ? "长期" : strategy === "short_term" ? "短线" : strategy;
  return `${m} ${s}`;
}

// ── Main Component ────────────────────────────────────

export function PicksClient({ aLong, aShort, hkLong, hkShort, history, perf }: Props) {
  const activeCount = aLong.length + aShort.length + hkLong.length + hkShort.length;

  return (
    <div className="space-y-3 pb-2">
      {/* Header */}
      <div className="flex items-center gap-2">
        <TrendingUp className="w-4 h-4 text-emerald-400" />
        <h1 className="text-sm font-bold text-zinc-100">荐股列表</h1>
        {activeCount > 0 && (
          <span className="text-[10px] text-zinc-500 tabular-nums ml-auto">
            持仓 {activeCount} 支
          </span>
        )}
      </div>

      {/* ── Four Quadrant Grid (2x2) ───────────────────────── */}
      <div className="grid grid-cols-2 gap-2">
        <QuadrantCard label="A股长期投资" stocks={aLong} color="amber" />
        <QuadrantCard label="A股短线投机" stocks={aShort} color="emerald" />
        <QuadrantCard label="港股长期投资" stocks={hkLong} color="amber" />
        <QuadrantCard label="港股短线投机" stocks={hkShort} color="emerald" />
      </div>

      {/* ── 策略绩效汇总 ──────────────────────────────── */}
      {perf.length > 0 && (
        <ModuleCard
          label="策略绩效"
          title=""
          icon={<BarChart3 className="w-3.5 h-3.5 text-violet-400" />}
          accent="violet"
          body={
            <div className="space-y-1.5">
              {perf.map((p) => {
                const winRate = p.total_trades > 0
                  ? ((p.wins / p.total_trades) * 100).toFixed(0)
                  : "--";
                return (
                  <div
                    key={`${p.market}-${p.strategy_type}`}
                    className="flex items-center justify-between text-[10px] border-b border-zinc-800/30 last:border-0 py-1"
                  >
                    <span className="text-zinc-400 w-20 shrink-0">
                      {toPerfLabel(p.market, p.strategy_type)}
                    </span>
                    <span className="text-zinc-500 tabular-nums">
                      {p.total_trades}笔
                    </span>
                    <span className={cn("tabular-nums font-medium", pctClass(p.avg_return))}>
                      {pctStr(p.avg_return)}
                    </span>
                    <span className="text-zinc-500 tabular-nums">
                      {winRate}%胜率
                    </span>
                  </div>
                );
              })}
            </div>
          }
        />
      )}

      {/* ── 历史荐股 ──────────────────────────────────── */}
      <ModuleCard
        label="历史荐股"
        title={history.length > 0 ? `最近 ${history.length} 条` : ""}
        icon={<History className="w-3.5 h-3.5 text-blue-400" />}
        accent="blue"
        body={
          history.length === 0 ? (
            <p className="text-[10px] text-zinc-600 py-2 text-center">暂无历史记录</p>
          ) : (
            <div className="space-y-0.5 max-h-64 overflow-y-auto">
              {history.map((r, i) => (
                <div
                  key={r.id || i}
                  className="py-1 border-b border-zinc-800/30 last:border-0 flex items-center gap-1 text-[10px]"
                >
                  <span className="text-zinc-500 w-14 shrink-0 tabular-nums">
                    {r.entry_date?.slice(5, 10) ?? "--"}
                  </span>
                  <span className="font-mono text-zinc-500 w-14 shrink-0">{r.symbol}</span>
                  <span className="text-zinc-200 flex-1 truncate">{r.name}</span>
                  <span className={cn("tabular-nums", pctClass(r.pnl_pct))}>
                    {pctStr(r.pnl_pct)}
                  </span>
                  <span className="text-zinc-600 w-16 text-right truncate">
                    {r.exit_reason ?? strategyLabel(r.strategy_type)}
                  </span>
                </div>
              ))}
            </div>
          )
        }
      />
    </div>
  );
}
