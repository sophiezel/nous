import { getThemePool, getThemeFundFlow, getThemeTrend, getStockTrend } from "@/lib/db";
import { TrendingUp, LayoutGrid, Compass, Target, Info } from "lucide-react";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { cn } from "@/lib/utils";
import Link from "next/link";
import type { ThemePool } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 300;

function fmtB(n: number | null): string {
  if (n == null) return "--";
  return `${n >= 0 ? "+" : ""}${(n / 1e8).toFixed(1)}亿`;
}

export default async function MobileSectorsPage() {
  let themes: ThemePool[] = [];
  try { themes = getThemePool(); } catch { /* empty */ }

  const mainThemes = themes.slice(0, 3);
  const potentialThemes = themes.slice(3, 6);

  // ── Build data using bundled db functions ──
  const themeData = new Map<string, {
    flow: number | null;
    trendLabels: string[];
    trendValues: number[];
    stockTrends: Map<string, number[]>;
    upCount: number;
    downCount: number;
    stocks: typeof themes[0]["stocks"];
  }>();

  for (const t of themes) {
    const symbols = t.stocks.map(s => s.symbol);
    const upCount = t.stocks.filter(s => s.change_pct > 0).length;
    const downCount = t.stocks.filter(s => s.change_pct < 0).length;

    let flow: number | null = null;
    let trendLabels: string[] = [];
    let trendValues: number[] = [];
    const stockTrends = new Map<string, number[]>();

    try {
      flow = getThemeFundFlow(symbols);

      const trendRows = getThemeTrend(symbols, 61);
      if (trendRows.length >= 2) {
        trendLabels = trendRows.map(r => r.trade_date.slice(5));
        trendValues = trendRows.map(r => r.avg_pct);
      }

      const top5 = [...t.stocks].sort((a, b) => b.change_pct - a.change_pct).slice(0, 5);
      for (const s of top5) {
        const tvals = getStockTrend(s.symbol, 31);
        if (tvals.length >= 5) stockTrends.set(s.symbol, tvals);
      }
    } catch { /* ignore */ }

    themeData.set(t.theme, {
      flow, trendLabels, trendValues, stockTrends,
      upCount, downCount,
      stocks: t.stocks,
    });
  }

  return (
    <div className="space-y-4 pb-2">
      <h1 className="text-lg font-bold">板块地图</h1>

      {mainThemes.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Target className="w-4 h-4 text-emerald-400" />
            <span className="text-xs font-semibold text-emerald-400">主线板块</span>
          </div>
          {mainThemes.map(t => renderThemeCard(t.theme, [...t.stocks].sort((a, b) => b.change_pct - a.change_pct), themeData.get(t.theme)!))}
        </div>
      )}

      {potentialThemes.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 mt-2">
            <Compass className="w-4 h-4 text-amber-400" />
            <span className="text-xs font-semibold text-amber-400">潜力主线</span>
          </div>
          {potentialThemes.map(t => renderThemeCard(t.theme, [...t.stocks].sort((a, b) => b.change_pct - a.change_pct), themeData.get(t.theme)!))}
        </div>
      )}

      <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <summary className="p-3 text-xs font-medium text-zinc-400 list-none cursor-pointer"><Info className="w-3 h-3 inline mr-1" />指标详解</summary>
        <div className="px-3 pb-3 space-y-2 text-[10px] leading-relaxed">
          {[
            { k: "板块趋势", d: "该板块所有个股日均涨跌幅的均值。ZoomableSparkline 支持 3M/6M/1Y/2Y/ALL 切换。" },
            { k: "龙头个股", d: "板块内当日涨幅最大的5只标的，每只展示30日涨跌幅趋势。" },
            { k: "细分板块", d: "点击进入该板块的子赛道详情。" },
          ].map(t => (
            <div key={t.k} className="bg-zinc-800/40 rounded-lg p-2">
              <span className="font-medium text-zinc-300">{t.k}</span>
              <p className="text-zinc-500 mt-0.5">{t.d}</p>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

function renderThemeCard(
  themeName: string,
  stocks: typeof themes extends any[] ? any : any,
  data: { flow: number | null; trendLabels: string[]; trendValues: number[]; stockTrends: Map<string, number[]>; upCount: number; downCount: number }
) {
  const segments = new Map<string, any[]>();
  for (const s of stocks) {
    const seg = s.segment || "未分类";
    if (!segments.has(seg)) segments.set(seg, []);
    segments.get(seg)!.push(s);
  }

  const best = stocks[0];

  return (
    <div key={themeName} className="rounded-2xl border border-zinc-800 bg-zinc-900/40 overflow-hidden">
      <div className="p-4 flex items-start justify-between">
        <div>
          <h2 className="text-base font-bold text-zinc-100">{themeName}</h2>
          <div className="flex gap-2 text-[10px] text-zinc-500 mt-1">
            <span>{stocks.length}只</span>
            <span className="text-emerald-400">涨{data.upCount}</span>
            <span className="text-rose-400">跌{data.downCount}</span>
            {data.flow != null && (
              <span className={data.flow >= 0 ? "text-emerald-400" : "text-rose-400"}>
                主力{fmtB(data.flow)}
              </span>
            )}
          </div>
        </div>
        <span className={cn("tabular-nums text-sm font-semibold", best?.change_pct >= 0 ? "text-emerald-400" : "text-rose-400")}>
          {best ? `${best.change_pct >= 0 ? "+" : ""}${best.change_pct.toFixed(1)}%` : "--"}
        </span>
      </div>

      {data.trendValues.length >= 2 && (
        <div className="px-4 pb-3">
          <p className="text-[10px] text-zinc-500 mb-1">板块趋势 · 日均涨跌幅</p>
          <ZoomableSparkline data={data.trendValues} labels={data.trendLabels} color="#10b981" height={52} />
        </div>
      )}

      <div className="border-t border-zinc-800/50">
        <div className="flex items-center gap-2 px-4 py-2">
          <TrendingUp className="w-3 h-3 text-emerald-400" />
          <span className="text-[10px] font-medium text-zinc-400">龙头个股 TOP5</span>
        </div>
        <div className="divide-y divide-zinc-800/30 px-4">
          {stocks.slice(0, 5).map(s => {
            const tv = data.stockTrends.get(s.symbol) || [];
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

      <div className="border-t border-zinc-800/50 px-4 py-3 flex flex-wrap gap-2">
        {Array.from(segments.entries()).map(([seg, segStocks]) => (
          <Link
            key={seg}
            href={`/mobile/sectors/${encodeURIComponent(themeName)}/${encodeURIComponent(seg)}`}
            className="flex items-center gap-1.5 text-[10px] px-2 py-1 rounded-lg border border-zinc-700/50 bg-zinc-800/30 text-zinc-400 hover:text-zinc-200 hover:border-zinc-600 active:scale-[0.97] transition-all"
          >
            <LayoutGrid className="w-3 h-3" />
            <span>{seg}</span>
            <span className="text-zinc-600">({segStocks.length})</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
