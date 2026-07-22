import { getThemePool, getThemeFundFlow, getThemeTrend, getStockTrend } from "@/lib/db";
import { ArrowLeft } from "lucide-react";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { cn } from "@/lib/utils";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ThemePoolStock } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 300;

function fmtB(n: number | null): string {
  if (n == null) return "--";
  return `${n >= 0 ? "+" : ""}${(n / 1e8).toFixed(1)}亿`;
}

export default async function SegmentPage(props: any) {
  const themeSlug = decodeURIComponent(props.params.slug);
  const segmentSlug = decodeURIComponent(props.params.segment);

  let themes: any[] = [];
  try { themes = getThemePool(); } catch { /* empty */ }
  const theme = themes.find((t: any) => t.theme === themeSlug);
  if (!theme) notFound();

  const segStocks = (theme.stocks as ThemePoolStock[]).filter((s: ThemePoolStock) => (s.segment || "未分类") === segmentSlug);
  if (segStocks.length === 0) notFound();

  const stocks = [...segStocks].sort((a, b) => b.change_pct - a.change_pct);
  const symbols = stocks.map(s => s.symbol);

  let segFlow: number | null = null;
  let segLabels: string[] = [];
  let segValues: number[] = [];
  const stockTrends = new Map<string, number[]>();

  try {
    segFlow = getThemeFundFlow(symbols);

    const trendRows = getThemeTrend(symbols, 61);
    if (trendRows.length >= 2) {
      segLabels = trendRows.map(r => r.trade_date.slice(5));
      segValues = trendRows.map(r => r.avg_pct);
    }

    for (const s of stocks) {
      const tvals = getStockTrend(s.symbol, 31);
      if (tvals.length >= 5) stockTrends.set(s.symbol, tvals);
    }
  } catch { /* ignore */ }

  const upCount = stocks.filter(s => s.change_pct > 0).length;
  const avgPct = stocks.reduce((sum, s) => sum + s.change_pct, 0) / stocks.length;
  const best = stocks[0];

  return (
    <div className="space-y-4">
      <Link href="/mobile/sectors" className="inline-flex items-center gap-1 text-xs text-zinc-500">
        <ArrowLeft className="w-3 h-3" /> 板块地图
      </Link>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 overflow-hidden">
        <div className="p-4 flex items-start justify-between">
          <div>
            <h1 className="text-base font-bold text-zinc-100">{segmentSlug}</h1>
            <div className="text-[10px] text-zinc-500 mt-0.5">{themeSlug} · 细分板块</div>
            <div className="flex gap-2 text-[10px] mt-1">
              <span className="text-zinc-500">{stocks.length}只</span>
              <span className="text-emerald-400">涨{upCount}</span>
              <span className="text-rose-400">跌{stocks.length - upCount}</span>
              <span className={cn("tabular-nums font-medium", avgPct >= 0 ? "text-emerald-400" : "text-rose-400")}>
                均{avgPct >= 0 ? "+" : ""}{avgPct.toFixed(2)}%
              </span>
              {segFlow != null && (
                <span className={segFlow >= 0 ? "text-emerald-400" : "text-rose-400"}>主力{fmtB(segFlow)}</span>
              )}
            </div>
          </div>
          <span className={cn("tabular-nums text-sm font-semibold", best?.change_pct >= 0 ? "text-emerald-400" : "text-rose-400")}>
            {best ? `${best.change_pct >= 0 ? "+" : ""}${best.change_pct.toFixed(1)}%` : "--"}
          </span>
        </div>

        {segValues.length >= 2 && (
          <div className="px-4 pb-3">
            <p className="text-[10px] text-zinc-500 mb-1">板块趋势 · 日均涨跌幅</p>
            <ZoomableSparkline data={segValues} labels={segLabels} color="#10b981" height={52} />
          </div>
        )}

        <div className="border-t border-zinc-800/50 divide-y divide-zinc-800/30 px-4">
          {stocks.map(s => {
            const tv = stockTrends.get(s.symbol) || [];
            const tl = Array.from({length: tv.length}, (_,i) => `D-${tv.length-i}`);
            return (
              <div key={s.symbol} className="py-2 space-y-1">
                <div className="flex items-center gap-2 text-[10px]">
                  <span className="font-mono text-zinc-500 w-14 shrink-0">{s.symbol}</span>
                  <span className="text-zinc-300 truncate flex-1">{s.name}</span>
                  <span className={cn("tabular-nums font-mono", s.change_pct >= 0 ? "text-emerald-400" : "text-rose-400")}>
                    {s.change_pct >= 0 ? "+" : ""}{s.change_pct.toFixed(1)}%
                  </span>
                  {s.source && (
                    <span className="text-[8px] px-1 rounded bg-zinc-800 text-zinc-500">{s.source.length > 6 ? s.source.slice(0, 6) : s.source}</span>
                  )}
                </div>
                {tv.length >= 5 && (
                  <ZoomableSparkline data={tv} labels={tl} color={s.change_pct >= 0 ? "#10b981" : "#ef4444"} height={36} />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
