import { getLivePortfolio, getPortfolioNavHistory, getRiskMetrics } from "@/lib/db";
import { headers } from "next/headers";
import { isSuperAdmin } from "@/lib/auth";
import { PortfolioChart } from "@/components/portfolio-pnl-chart";

export const dynamic = "force-dynamic";
export const revalidate = 300;

function PnlBar({ pct }: { pct: number }) {
  const color = pct > 0 ? "bg-emerald-500" : pct < 0 ? "bg-red-500" : "bg-zinc-600";
  const width = Math.min(Math.abs(pct) * 2, 100);
  return (
    <div className="flex items-center gap-2">
      <span className={`text-xs font-mono tabular-nums ${pct > 0 ? "text-emerald-400" : "text-red-400"} w-14 text-right`}>
        {pct > 0 ? "+" : ""}{pct.toFixed(1)}%
      </span>
      <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

export default async function PortfolioPage() {
  const h = await headers();
  const role = h.get("X-User-Role") || "guest";
  if (!isSuperAdmin(role)) {
    return <div className="max-w-6xl mx-auto p-8 text-center text-zinc-500">无权访问 · 仅超管可见</div>;
  }

  const holdings = getLivePortfolio();
  const navHistory = getPortfolioNavHistory(60).reverse(); // chronological
  const risk = getRiskMetrics();

  // Aggregate stats
  const totalWeight = holdings.reduce((s, h) => s + h.weight_pct, 0);
  const profitable = holdings.filter(h => h.pnl_pct > 0).length;
  const lossMaking = holdings.filter(h => h.pnl_pct < 0).length;
  const totalPnl = (holdings.reduce((s, h) => s + h.pnl_pct * h.weight_pct, 0) / (totalWeight || 1));
  const latestNav = navHistory[navHistory.length - 1];

  // Sector aggregation
  const sectorMap = new Map<string, { count: number; weight: number; pnl: number }>();
  for (const h of holdings) {
    const s = sectorMap.get(h.sector) || { count: 0, weight: 0, pnl: 0 };
    s.count++;
    s.weight += h.weight_pct;
    s.pnl += h.pnl_pct * h.weight_pct;
    sectorMap.set(h.sector, s);
  }
  const sectors = Array.from(sectorMap.entries())
    .map(([name, d]) => ({ name, count: d.count, weight: d.weight, avgPnl: d.pnl / (d.weight || 1) }))
    .sort((a, b) => b.weight - a.weight);

  // Tier aggregation
  const tierMap = new Map<string, { count: number; weight: number }>();
  for (const h of holdings) {
    const t = tierMap.get(h.tier) || { count: 0, weight: 0 };
    t.count++;
    t.weight += h.weight_pct;
    tierMap.set(h.tier, t);
  }
  const tiers = Array.from(tierMap.entries()).map(([name, d]) => ({ name, count: d.count, weight: d.weight }));

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">实盘持仓</h1>
        <p className="text-sm text-zinc-500 mt-1">
          {holdings.length} 只持仓 · 总盈亏 {totalPnl > 0 ? "+" : ""}{totalPnl.toFixed(1)}%
          · 盈 {profitable} 亏 {lossMaking}
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
          <span className="text-[10px] text-zinc-500">综合盈亏</span>
          <p className={`text-lg font-bold tabular-nums ${totalPnl > 0 ? "text-emerald-400" : "text-red-400"}`}>
            {totalPnl > 0 ? "+" : ""}{totalPnl.toFixed(1)}%
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
          <span className="text-[10px] text-zinc-500">盈利/亏损</span>
          <p className="text-lg font-bold tabular-nums">
            <span className="text-emerald-400">{profitable}</span>
            <span className="text-zinc-600"> / </span>
            <span className="text-red-400">{lossMaking}</span>
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
          <span className="text-[10px] text-zinc-500">累计净值</span>
          <p className="text-lg font-bold tabular-nums text-zinc-200">
            {latestNav?.nav?.toFixed(4) || "—"}
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
          <span className="text-[10px] text-zinc-500">最大回撤 (VaR95)</span>
          <p className="text-lg font-bold tabular-nums text-red-400">
            {risk?.var_95 ? `${(risk.var_95 * 100).toFixed(1)}%` : "—"}
          </p>
        </div>
      </div>

      {/* PNL chart */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
        <h2 className="text-sm font-semibold text-zinc-300 mb-3">净值走势</h2>
        {navHistory.length >= 2 ? (
          <PortfolioChart data={navHistory} />
        ) : (
          <p className="text-[11px] text-zinc-500 py-12 text-center">数据积累中 · 需多日净值快照</p>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Holdings list */}
        <div className="lg:col-span-2 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <h2 className="text-sm font-semibold text-zinc-300 mb-3">持仓明细</h2>
          <div className="space-y-1.5">
            {holdings.map((h) => (
              <div key={h.symbol} className="flex items-center gap-3 py-1.5 border-b border-zinc-800/30 last:border-0">
                <div className="w-20 shrink-0">
                  <span className="font-mono text-[10px] text-zinc-500 block">{h.symbol}</span>
                  <span className="text-xs font-medium text-zinc-200">{h.name}</span>
                </div>
                <div className="flex-1">
                  <PnlBar pct={h.pnl_pct} />
                </div>
                <div className="text-right shrink-0 w-16">
                  <span className="text-[10px] text-zinc-500">{h.tier === "core" ? "核心" : h.tier === "satellite" ? "卫星" : h.tier}</span>
                  <span className="text-[10px] text-zinc-600 block">{h.weight_pct.toFixed(1)}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Sidebar: sector + risk */}
        <div className="space-y-4">
          {/* Sector concentration */}
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
            <h2 className="text-sm font-semibold text-zinc-300 mb-3">行业分布</h2>
            <div className="space-y-1.5">
              {sectors.map((s) => (
                <div key={s.name} className="flex items-center gap-2 text-[11px]">
                  <span className="w-20 text-zinc-500 truncate">{s.name}</span>
                  <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                    <div className="h-full bg-violet-500/60 rounded-full" style={{ width: `${Math.min(s.weight, 100)}%` }} />
                  </div>
                  <span className="w-12 text-right tabular-nums text-zinc-400">{s.weight.toFixed(1)}%</span>
                </div>
              ))}
            </div>
          </div>

          {/* Tier distribution */}
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
            <h2 className="text-sm font-semibold text-zinc-300 mb-3">持仓层级</h2>
            <div className="grid grid-cols-3 gap-2 text-center">
              {tiers.map((t) => (
                <div key={t.name} className="rounded bg-zinc-800/30 p-2">
                  <div className="text-xs text-zinc-300">
                    {{ core: "核心", satellite: "卫星", etf: "ETF" }[t.name] || t.name}
                  </div>
                  <div className="text-[10px] text-zinc-500">{t.count}只</div>
                  <div className="text-xs font-bold tabular-nums text-zinc-200">{t.weight.toFixed(1)}%</div>
                </div>
              ))}
            </div>
          </div>

          {/* Risk metrics */}
          {risk && (
            <div className="rounded-xl border border-red-800/30 bg-zinc-900/50 p-4">
              <h2 className="text-sm font-semibold text-zinc-300 mb-3">风控指标</h2>
              <div className="space-y-1.5 text-[11px]">
                <div className="flex justify-between">
                  <span className="text-zinc-500">VaR 95%</span>
                  <span className="text-red-400 tabular-nums">{(risk.var_95 * 100).toFixed(1)}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">VaR 99%</span>
                  <span className="text-red-400 tabular-nums">{(risk.var_99 * 100).toFixed(1)}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">CVaR 95%</span>
                  <span className="text-red-400 tabular-nums">{(risk.cvar_95 * 100).toFixed(1)}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">最大回撤</span>
                  <span className="text-red-400 tabular-nums">{(risk.max_drawdown * 100).toFixed(1)}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">行业集中度</span>
                  <span className="text-amber-400 tabular-nums">{(risk.sector_concentration * 100).toFixed(1)}%</span>
                </div>
              </div>

              {/* Factor exposures */}
              {risk.factor_exposures && Object.keys(risk.factor_exposures).length > 0 && (
                <div className="mt-3 pt-3 border-t border-zinc-800">
                  <span className="text-[10px] text-zinc-500 block mb-1.5">因子暴露</span>
                  <div className="space-y-1">
                    {Object.entries(risk.factor_exposures).map(([k, v]) => (
                      <div key={k} className="flex items-center gap-1.5 text-[10px]">
                        <span className="w-12 text-zinc-500 truncate">{k}</span>
                        <div className="flex-1 h-1 bg-zinc-800 rounded-full overflow-hidden">
                          <div className={`h-full rounded-full ${(v as number) > 0 ? "bg-emerald-500/60" : "bg-red-500/60"}`}
                            style={{ width: `${Math.min(Math.abs(v as number) * 100, 100)}%`, marginLeft: (v as number) >= 0 ? "0" : "auto", marginRight: (v as number) >= 0 ? "auto" : "0" }} />
                        </div>
                        <span className="w-8 text-right tabular-nums text-zinc-400">{(v as number).toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Alerts */}
              {risk.alerts && risk.alerts.length > 0 && (
                <div className="mt-3 pt-3 border-t border-zinc-800">
                  <span className="text-[10px] text-red-400 block mb-1">⚠️ 风控告警</span>
                  {risk.alerts.map((a, i) => (
                    <div key={i} className="text-[10px] text-red-400/80">{a}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
