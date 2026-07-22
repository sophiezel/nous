import { getThemePool } from "@/lib/db";
import { headers } from "next/headers";
import { isSuperAdmin } from "@/lib/auth";

export const dynamic = "force-dynamic";
export const revalidate = 300;

function ChangeBadge({ pct }: { pct: number }) {
  const color = pct > 0 ? "text-emerald-400" : pct < 0 ? "text-red-400" : "text-zinc-400";
  const bg = pct > 2 ? "bg-emerald-500/10 border-emerald-500/20" :
             pct < -2 ? "bg-red-500/10 border-red-500/20" : "bg-zinc-800/50 border-zinc-700/30";
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border tabular-nums font-mono ${color} ${bg}`}>
      {pct > 0 ? "+" : ""}{pct.toFixed(1)}%
    </span>
  );
}

function SourceBadge({ source }: { source: string }) {
  const colors: Record<string, string> = {
    "鳄鱼派核心": "bg-amber-500/10 text-amber-400 border-amber-500/20",
    "鳄鱼派-低位": "bg-amber-500/10 text-amber-400 border-amber-500/20",
    "鳄鱼派-新开": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    "鳄鱼派-波段": "bg-violet-500/10 text-violet-400 border-violet-500/20",
    "鳄鱼派-观望": "bg-zinc-800 text-zinc-400 border-zinc-700",
    "标准池-龙头": "bg-blue-500/10 text-blue-400 border-blue-500/20",
    "标准池": "bg-zinc-800 text-zinc-400 border-zinc-700",
  };
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded border ${colors[source] || colors["标准池"]}`}>
      {source || "标准池"}
    </span>
  );
}

export default async function SectorsPage() {
  const h = await headers();
  const role = h.get("X-User-Role") || "guest";
  if (!isSuperAdmin(role)) {
    return <div className="max-w-6xl mx-auto p-8 text-center text-zinc-500">无权访问 · 仅超管可见</div>;
  }

  const themes = getThemePool();

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">板块地图</h1>
        <p className="text-sm text-zinc-500 mt-1">
          6 个观察池 · {themes.reduce((s, t) => s + t.stocks.length, 0)} 只股票 · 按主题分段
        </p>
      </div>

      {themes.map((theme) => {
        // Group stocks by segment
        const segments = new Map<string, typeof theme.stocks>();
        for (const s of theme.stocks) {
          const seg = s.segment || "未分类";
          if (!segments.has(seg)) segments.set(seg, []);
          segments.get(seg)!.push(s);
        }

        // Count 鳄鱼派 tagged stocks
        const crocStocks = theme.stocks.filter(s => s.source?.includes("鳄鱼派"));
        const upStocks = theme.stocks.filter(s => s.change_pct > 0);

        return (
          <details key={theme.theme} className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden group" open>
            <summary className="flex items-center gap-3 p-4 cursor-pointer hover:bg-zinc-800/30 transition-colors">
              <span className="w-1.5 h-1.5 rounded-full bg-violet-400" />
              <span className="font-semibold text-zinc-200">{theme.theme}</span>
              <span className="text-xs text-zinc-500">{theme.stocks.length} 只</span>
              {crocStocks.length > 0 && (
                <span className="text-[10px] text-amber-400">鳄鱼派 {crocStocks.length}</span>
              )}
              <span className={`text-[10px] ${upStocks.length > theme.stocks.length / 2 ? "text-emerald-400" : "text-red-400"}`}>
                涨 {upStocks.length} / 跌 {theme.stocks.length - upStocks.length}
              </span>
            </summary>

            <div className="divide-y divide-zinc-800/50">
              {Array.from(segments.entries()).map(([segName, stocks]) => (
                <div key={segName} className="p-3">
                  <h3 className="text-[11px] font-medium text-zinc-400 mb-2 uppercase tracking-wider">
                    {segName} ({stocks.length}只)
                  </h3>
                  {/* Segment comment */}
                  {stocks[0]?.comment && (
                    <div className="mb-2 p-2 rounded bg-amber-500/5 border border-amber-500/10 text-[10px] text-zinc-400 leading-relaxed">
                      {stocks[0].comment}
                    </div>
                  )}
                  <div className="overflow-x-auto">
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr className="text-zinc-500 border-b border-zinc-800/50">
                          <th className="text-left py-1.5 pr-2 font-normal">代码</th>
                          <th className="text-left py-1.5 pr-2 font-normal">名称</th>
                          <th className="text-right py-1.5 px-2 font-normal">现价</th>
                          <th className="text-right py-1.5 px-2 font-normal">涨跌</th>
                          <th className="text-right py-1.5 px-2 font-normal">成交额</th>
                          <th className="text-left py-1.5 pl-2 font-normal">来源</th>
                        </tr>
                      </thead>
                      <tbody>
                        {stocks.map((s) => (
                          <tr key={s.symbol} className="border-b border-zinc-800/30 hover:bg-zinc-800/20">
                            <td className="py-1.5 pr-2 font-mono text-zinc-400">{s.symbol}</td>
                            <td className="py-1.5 pr-2 text-zinc-200 font-medium">{s.name}</td>
                            <td className="py-1.5 px-2 text-right tabular-nums text-zinc-300">
                              {s.price > 0 ? s.price.toFixed(2) : "—"}
                            </td>
                            <td className="py-1.5 px-2 text-right"><ChangeBadge pct={s.change_pct} /></td>
                            <td className="py-1.5 px-2 text-right tabular-nums text-zinc-400 text-[10px]">
                              {s.volume > 0 ? s.volume.toFixed(1) + "亿" : "—"}
                            </td>
                            <td className="py-1.5 pl-2"><SourceBadge source={s.source} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          </details>
        );
      })}

      {themes.length === 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-12 text-center text-zinc-500">
          暂无板块数据 · 等待 parse_theme_pools cron
        </div>
      )}
    </div>
  );
}
