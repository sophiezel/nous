import { getThemePool, getRecentReports, getLatestReportByType, getScreenerDb, getRecPerformance, getRecPerformanceSummary } from "@/lib/db";
import { headers } from "next/headers";
import { isSuperAdmin } from "@/lib/auth";
import { TrendingUp, TrendingDown, Target } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 300;

function Tag({ label }: { label: string }) {
  const colors: Record<string, string> = {
    core: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    satellite: "bg-zinc-800 text-zinc-400 border-zinc-700",
    etf: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  };
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded border ${colors[label] || colors.satellite}`}>
      {{ core: "长线核心", satellite: "短线投机", etf: "ETF" }[label] || label}
    </span>
  );
}

export default async function PicksPage() {
  const h = await headers();
  const role = h.get("X-User-Role") || "guest";
  if (!isSuperAdmin(role)) {
    return <div className="max-w-6xl mx-auto p-8 text-center text-zinc-500">无权访问 · 仅超管可见</div>;
  }

  const themes = getThemePool();
  const dailyPicks = getLatestReportByType("daily_picks");

  // Extract long-term (tier=核心/核心持仓 from 鳄鱼派核心 source) from theme pool
  const seen = new Set<string>();
  const longTermStocks = themes.flatMap(t =>
    t.stocks.filter(s => {
      const match = s.source?.includes("鳄鱼派核心") || s.source?.includes("标准池-龙头");
      if (match && !seen.has(s.symbol)) { seen.add(s.symbol); return true; }
      return false;
    })
  ).slice(0, 10);

  // Extract short-term (鳄鱼派-新开, 鳄鱼派-波段, 鳄鱼派-低位) — no comment catch-all
  const shortTermStocks = themes.flatMap(t =>
    t.stocks.filter(s => {
      const match = s.source?.includes("鳄鱼派-新开") ||
                    s.source?.includes("鳄鱼派-波段") ||
                    s.source?.includes("鳄鱼派-低位");
      if (match && !seen.has(s.symbol)) { seen.add(s.symbol); return true; }
      return false;
    })
  ).slice(0, 10);

  // Quant picks from screener
  const quantStocks = (() => {
    try {
      const db = getScreenerDb();
      const rows = db.prepare(
        "SELECT symbol, MAX(name) as name, MAX(score) as score, MAX(pe) as pe, MAX(pb) as pb, MAX(rsi) as rsi, MAX(volume_ratio) as volume_ratio FROM screen_results WHERE screen_date=(SELECT MAX(screen_date) FROM screen_results) AND score>=8 GROUP BY symbol ORDER BY score DESC LIMIT 8"
      ).all() as any[];
      return rows;
    } catch { return []; }
  })();

  // Recommendation performance tracking
  const recHistory = getRecPerformance(30);
  const recSummary = getRecPerformanceSummary();

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">荐股列表</h1>
        <p className="text-sm text-zinc-500 mt-1">
          长线核心 {longTermStocks.length} · 短线投机 {shortTermStocks.length} · 量化补遗 {quantStocks.length}
          {dailyPicks?.created_at ? ` · ${dailyPicks.created_at.slice(5, 10)} 更新` : ""}
        </p>
      </div>

      {/* ── Recommendation Performance ── */}
      {recHistory.length > 0 && (
        <div className="rounded-xl border border-amber-800/30 bg-gradient-to-r from-amber-950/10 to-zinc-950 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Target className="w-4 h-4 text-amber-400" />
            <h2 className="text-sm font-semibold text-zinc-300">历史推荐表现</h2>
            <span className="text-[10px] text-zinc-500">
              {recSummary.total_recs} 次 · 均回报 {(recSummary.avg_return || 0) * 100}% {recHistory.length < 5 && " · 趋势图待数据积累"}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-zinc-500 border-b border-zinc-800/50">
                  <th className="text-left py-1 pr-2 font-normal">日期</th>
                  <th className="text-left py-1 pr-2 font-normal">股票</th>
                  <th className="text-right py-1 px-2 font-normal">评分</th>
                  <th className="text-right py-1 px-2 font-normal">推荐价</th>
                  <th className="text-right py-1 px-2 font-normal">1日</th>
                  <th className="text-right py-1 px-2 font-normal">5日</th>
                  <th className="text-right py-1 px-2 font-normal">累计</th>
                </tr>
              </thead>
              <tbody>
                {recHistory.map((r) => (
                  <tr key={`${r.rec_date}-${r.symbol}`} className="border-b border-zinc-800/30">
                    <td className="py-1 pr-2 text-zinc-500">{r.rec_date.slice(5)}</td>
                    <td className="py-1 pr-2"><span className="font-mono text-zinc-500 mr-1">{r.symbol}</span><span className="text-zinc-200">{r.name}</span></td>
                    <td className="py-1 px-2 text-right tabular-nums text-violet-400">{r.rec_score?.toFixed(1)}</td>
                    <td className="py-1 px-2 text-right tabular-nums text-zinc-300">{r.rec_close > 0 ? r.rec_close.toFixed(2) : "—"}</td>
                    <td className="py-1 px-2 text-right tabular-nums">{r.return_1d !== null ? <span className={r.return_1d > 0 ? "text-emerald-400" : "text-red-400"}>{(r.return_1d * 100).toFixed(1)}%</span> : <span className="text-zinc-600">—</span>}</td>
                    <td className="py-1 px-2 text-right tabular-nums">{r.return_5d !== null ? <span className={r.return_5d > 0 ? "text-emerald-400" : "text-red-400"}>{(r.return_5d * 100).toFixed(1)}%</span> : <span className="text-zinc-600">—</span>}</td>
                    <td className="py-1 px-2 text-right tabular-nums">{r.last_return !== null ? <span className={r.last_return > 0 ? "text-emerald-400" : "text-red-400"}>{(r.last_return * 100).toFixed(1)}%</span> : <span className="text-zinc-600">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Long-term picks */}
        <div className="rounded-xl border border-amber-800/30 bg-gradient-to-b from-amber-950/20 to-zinc-950 p-4">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-2 h-2 rounded-full bg-amber-400" />
            <h2 className="text-sm font-semibold text-amber-400">长线投资</h2>
            <span className="text-[10px] text-zinc-500 ml-auto">{longTermStocks.length} 只</span>
          </div>
          <div className="space-y-2">
            {longTermStocks.length === 0 ? (
              <p className="text-[11px] text-zinc-500 py-8 text-center">暂无数据</p>
            ) : (
              longTermStocks.map((s) => (
                <div key={s.symbol} className="rounded-lg bg-zinc-800/30 p-2.5 border border-zinc-700/30 hover:border-amber-700/30 transition-colors">
                  <div className="flex items-center justify-between mb-1">
                    <div>
                      <span className="font-mono text-[10px] text-zinc-500 mr-1.5">{s.symbol}</span>
                      <span className="text-sm font-medium text-zinc-200">{s.name}</span>
                    </div>
                    <span className={`text-xs font-mono tabular-nums ${s.change_pct > 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {s.change_pct > 0 ? "+" : ""}{s.change_pct.toFixed(1)}%
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 text-[10px] text-zinc-500">
                    <span>{s.price > 0 ? s.price.toFixed(2) : "—"}</span>
                    <span>· {s.segment}</span>
                    {s.comment && <span className="text-amber-400/70 truncate max-w-[200px]">· {s.comment.slice(0, 40)}</span>}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Short-term picks */}
        <div className="rounded-xl border border-emerald-800/30 bg-gradient-to-b from-emerald-950/20 to-zinc-950 p-4">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-2 h-2 rounded-full bg-emerald-400" />
            <h2 className="text-sm font-semibold text-emerald-400">短线投机</h2>
            <span className="text-[10px] text-zinc-500 ml-auto">{shortTermStocks.length} 只</span>
          </div>
          <div className="space-y-2">
            {shortTermStocks.length === 0 ? (
              <p className="text-[11px] text-zinc-500 py-8 text-center">暂无数据</p>
            ) : (
              shortTermStocks.map((s) => (
                <div key={s.symbol} className="rounded-lg bg-zinc-800/30 p-2.5 border border-zinc-700/30 hover:border-emerald-700/30 transition-colors">
                  <div className="flex items-center justify-between mb-1">
                    <div>
                      <span className="font-mono text-[10px] text-zinc-500 mr-1.5">{s.symbol}</span>
                      <span className="text-sm font-medium text-zinc-200">{s.name}</span>
                    </div>
                    <span className={`text-xs font-mono tabular-nums ${s.change_pct > 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {s.change_pct > 0 ? "+" : ""}{s.change_pct.toFixed(1)}%
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 text-[10px] text-zinc-500">
                    <span>{s.price > 0 ? s.price.toFixed(2) : "—"}</span>
                    <span>· {s.source}</span>
                    {s.comment && <span className="text-emerald-400/70 truncate max-w-[200px]">· {s.comment.slice(0, 50)}</span>}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Quant picks */}
        <div className="rounded-xl border border-violet-800/30 bg-gradient-to-b from-violet-950/20 to-zinc-950 p-4">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-2 h-2 rounded-full bg-violet-400" />
            <h2 className="text-sm font-semibold text-violet-400">量化补遗</h2>
            <span className="text-[10px] text-zinc-500 ml-auto">{quantStocks.length} 只</span>
          </div>
          <div className="space-y-2">
            {quantStocks.length === 0 ? (
              <p className="text-[11px] text-zinc-500 py-8 text-center">暂无数据</p>
            ) : (
              quantStocks.map((s: any) => (
                <div key={s.symbol} className="rounded-lg bg-zinc-800/30 p-2.5 border border-zinc-700/30 hover:border-violet-700/30 transition-colors">
                  <div className="flex items-center justify-between mb-1">
                    <div>
                      <span className="font-mono text-[10px] text-zinc-500 mr-1.5">{s.symbol}</span>
                      <span className="text-sm font-medium text-zinc-200">{s.name}</span>
                    </div>
                    <span className="text-xs font-mono tabular-nums text-violet-400">{s.score?.toFixed(1)}</span>
                  </div>
                  <div className="flex items-center gap-1.5 text-[10px] text-zinc-500">
                    <span>PE {s.pe?.toFixed(1) || "—"}</span>
                    <span>· 量比 {s.volume_ratio?.toFixed(1) || "—"}</span>
                    <span>· RSI {s.rsi?.toFixed(0) || "—"}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Latest daily_picks report excerpt */}
      {dailyPicks && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[10px] text-zinc-500">最新荐股报告 · {dailyPicks.created_at?.slice(0, 16)}</span>
          </div>
          <div className="text-[11px] text-zinc-400 leading-relaxed max-h-48 overflow-y-auto whitespace-pre-wrap">
            {dailyPicks.content.slice(0, 800)}
          </div>
        </div>
      )}
    </div>
  );
}
