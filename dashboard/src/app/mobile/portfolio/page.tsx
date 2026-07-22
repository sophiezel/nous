import { PagePoller } from "@/components/PagePoller";
import { fetchAPI } from "@/lib/api";
import { headers } from "next/headers";
import { isSuperAdmin } from "@/lib/auth";

export const dynamic = "force-dynamic";
export const revalidate = 300;

export default async function MobilePortfolioPage() {
  const h = await headers();
  const role = h.get("X-User-Role") || "guest";
  if (!isSuperAdmin(role)) {
    return <div className="p-8 text-center text-zinc-500">无权访问</div>;
  }

  const [holdings, risk] = await Promise.all([
    fetchAPI("/v1/portfolio/live"),
    fetchAPI("/v1/risk/overview"),
  ]);
  const totalWeight = (holdings ?? []).reduce((s: number, h: any) => s + h.weight_pct, 0);
  const profitable = (holdings ?? []).filter((h: any) => h.pnl_pct > 0).length;
  const lossMaking = (holdings ?? []).filter((h: any) => h.pnl_pct < 0).length;
  const totalPnl = ((holdings ?? []).reduce((s: number, h: any) => s + h.pnl_pct * h.weight_pct, 0) / (totalWeight || 1));

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-bold">实盘持仓</h1>

      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-xl bg-zinc-900/50 border border-zinc-800 p-3">
          <span className="text-[9px] text-zinc-500">总盈亏</span>
          <p className={`text-lg font-bold tabular-nums ${totalPnl > 0 ? "text-emerald-400" : "text-red-400"}`}>
            {totalPnl > 0 ? "+" : ""}{totalPnl.toFixed(1)}%
          </p>
        </div>
        <div className="rounded-xl bg-zinc-900/50 border border-zinc-800 p-3">
          <span className="text-[9px] text-zinc-500">盈亏比</span>
          <p className="text-lg font-bold tabular-nums">
            <span className="text-emerald-400">{profitable}</span>
            <span className="text-zinc-600">/</span>
            <span className="text-red-400">{lossMaking}</span>
          </p>
        </div>
      </div>

      <div className="space-y-1">
        <span className="text-[10px] text-zinc-500">{(holdings ?? []).length} 只持仓</span>
        {(holdings ?? []).map((h: any) => (
          <div key={h.symbol} className="rounded-lg bg-zinc-900/50 border border-zinc-800 p-2.5">
            <div className="flex items-center justify-between mb-1">
              <div>
                <span className="font-mono text-[9px] text-zinc-500 mr-2">{h.symbol}</span>
                <span className="text-xs font-medium text-zinc-200">{h.name}</span>
              </div>
              <span className={`text-xs font-mono tabular-nums ${h.pnl_pct > 0 ? "text-emerald-400" : "text-red-400"}`}>
                {h.pnl_pct > 0 ? "+" : ""}{h.pnl_pct.toFixed(1)}%
              </span>
            </div>
            <div className="flex items-center gap-2 text-[9px] text-zinc-500">
              <span>{h.tier === "core" ? "核心" : h.tier === "satellite" ? "卫星" : h.tier}</span>
              <span>· {h.sector}</span>
              <span>· {h.weight_pct.toFixed(1)}%</span>
              <span>· {h.account}</span>
              {h.hold_days > 0 && <span>· {h.hold_days}天</span>}
            </div>
          </div>
        ))}
      </div>

      {risk && (
        <div className="rounded-xl border border-red-800/30 bg-zinc-900/50 p-3">
          <span className="text-[10px] text-zinc-500 block mb-2">风控</span>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
            <span className="text-zinc-500">VaR 95</span><span className="text-red-400 text-right">{(risk.var_95 * 100).toFixed(1)}%</span>
            <span className="text-zinc-500">VaR 99</span><span className="text-red-400 text-right">{(risk.var_99 * 100).toFixed(1)}%</span>
            <span className="text-zinc-500">CVaR 95</span><span className="text-red-400 text-right">{(risk.cvar_95 * 100).toFixed(1)}%</span>
            <span className="text-zinc-500">最大回撤</span><span className="text-red-400 text-right">{(risk.max_drawdown * 100).toFixed(1)}%</span>
          </div>
          {risk.alerts && risk.alerts.length > 0 && (
            <div className="mt-2 pt-2 border-t border-zinc-800">
              {risk.alerts.map((a: string, i: number) => (
                <div key={i} className="text-[9px] text-red-400/80">{a}</div>
              ))}
            </div>
          )}
        </div>
      )}
      <PagePoller pollKey="portfolio" />
    </div>
  );
}
