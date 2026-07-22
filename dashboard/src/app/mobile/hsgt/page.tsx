import { getHsgtStockTop, getHsgtSectorTop, getEtfFlowTop, getHsgtTotalTrend, getHsgtStockTrends } from "@/lib/db";
import { SparklineChart } from "@/components/mobile/sparkline";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { ArrowUpDown, TrendingUp, Info } from "lucide-react";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const revalidate = 60;

function fmtB(n: number | null): string {
  if (n == null) return "--";
  return `${n >= 0 ? "+" : ""}${(n / 1e8).toFixed(1)}亿`;
}
function pctText(n: number | null): string {
  if (n == null) return "--";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

export default function MobileHsgtPage() {
  const northTrend = [...getHsgtTotalTrend("北向", 120)].reverse();
  const southTrend = [...getHsgtTotalTrend("南向", 120)].reverse();
  const northStocks = getHsgtStockTop("北向", undefined, 20);
  const southStocks = getHsgtStockTop("南向", undefined, 20);
  const northSectors = getHsgtSectorTop("北向", undefined, 10);
  const etfFlows = getEtfFlowTop(17);

  // ── Individual stock trends ──
  const northTrends = getHsgtStockTrends("北向", northStocks.slice(0, 20).map(s => s.symbol), 30);
  const southTrends = getHsgtStockTrends("南向", southStocks.slice(0, 20).map(s => s.symbol), 30);

  const latestNorth = northTrend[northTrend.length - 1];
  const latestSouth = southTrend[southTrend.length - 1];
  const northDelta = northTrend.length > 5
    ? (northTrend[northTrend.length-1].total - northTrend[northTrend.length-6].total) / Math.abs(northTrend[northTrend.length-6].total || 1) * 100 : null;
  const southDelta = southTrend.length > 5
    ? (southTrend[southTrend.length-1].total - southTrend[southTrend.length-6].total) / Math.abs(southTrend[southTrend.length-6].total || 1) * 100 : null;

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-bold">资金流向</h1>

      {/* ──── Hero: 北向 ──── */}
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 mb-2">
          <ArrowUpDown className="w-4 h-4 text-blue-400" />
          <span className="text-xs font-medium text-zinc-300">北向资金 · 持股市值变化</span>
        </div>
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold tabular-nums text-blue-400">
            {latestNorth ? fmtB(latestNorth.total) : "--"}
          </span>
          {northDelta != null && (
            <span className={cn("text-xs", northDelta >= 0 ? "text-emerald-400" : "text-rose-400")}>
              {northDelta >= 0 ? "↑" : "↓"}{Math.abs(northDelta).toFixed(0)}%(周)
            </span>
          )}
          <span className="text-[10px] text-zinc-500 ml-auto">{latestNorth?.trade_date?.slice(0, 10)}</span>
        </div>
      </div>

      {/* ──── 北向趋势 ──── */}
      {northTrend.length >= 2 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
          <p className="text-xs font-medium text-zinc-400 mb-2">北向总额趋势</p>
          <ZoomableSparkline
            data={northTrend.map(r => r.total / 1e8)}
            labels={northTrend.map(r => r.trade_date)}
            color="#3b82f6" height={50}
          />
        </div>
      )}

      {/* ──── 北向板块 ──── */}
      {northSectors.length > 0 && (
        <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden" open>
          <summary className="p-3 flex items-center gap-2 list-none cursor-pointer">
            <TrendingUp className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-xs font-medium text-zinc-400">北向板块 TOP{northSectors.length}</span>
          </summary>
          <div className="px-3 pb-3 space-y-1">
            {northSectors.map(s => (
              <div key={s.sector} className="flex items-center gap-2 text-[10px] py-0.5 border-b border-zinc-800/30 last:border-0">
                <span className="text-zinc-300 flex-1">{s.sector}</span>
                <span className={cn("tabular-nums font-mono", s.total_net_buy >= 0 ? "text-emerald-400" : "text-rose-400")}>
                  {fmtB(s.total_net_buy)}
                </span>
                <span className="text-zinc-500 text-[9px]">{s.buy_count}买{s.sell_count}卖</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* ──── 北向个股 ──── */}
      <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden" open>
        <summary className="p-3 flex items-center gap-2 list-none cursor-pointer">
          <span className="text-xs font-medium text-zinc-400">北向个股 TOP{Math.min(northStocks.length, 20)}</span>
          <span className="ml-auto text-[10px] text-zinc-500">{northStocks[0]?.trade_date?.slice(5) || ""}</span>
        </summary>
        <div className="px-3 pb-3 space-y-0.5">
          {northStocks.map(s => {
              const trend = northTrends[s.symbol] || [];
              return (
                <div key={s.symbol} className="flex items-center gap-2 text-[10px] py-0.5 border-b border-zinc-800/30 last:border-0">
                  <span className="text-zinc-300 truncate flex-1">{s.name || s.symbol}</span>
                  <span className={cn("tabular-nums font-mono", s.net_inflow >= 0 ? "text-emerald-400" : "text-rose-400")}>{fmtB(s.net_inflow)}</span>
                  <span className={cn("tabular-nums", s.change_pct >= 0 ? "text-emerald-400" : "text-rose-400")}>{pctText(s.change_pct)}</span>
                  {trend.length >= 2 && (
                    <span className="w-12 shrink-0">
                      <SparklineChart data={trend} color={s.net_inflow >= 0 ? "#10b981" : "#ef4444"} height={18} className="w-12 h-[18px]" />
                    </span>
                  )}
                </div>
              );
            })}
        </div>
      </details>

      {/* ──── 南向 Hero ──── */}
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 mb-2">
          <ArrowUpDown className="w-4 h-4 text-violet-400" />
          <span className="text-xs font-medium text-zinc-300">南向资金 · 持股市值变化</span>
        </div>
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold tabular-nums text-violet-400">
            {latestSouth ? fmtB(latestSouth.total) : "--"}
          </span>
          {southDelta != null && (
            <span className={cn("text-xs", southDelta >= 0 ? "text-emerald-400" : "text-rose-400")}>
              {southDelta >= 0 ? "↑" : "↓"}{Math.abs(southDelta).toFixed(0)}%(周)
            </span>
          )}
          <span className="text-[10px] text-zinc-500 ml-auto">{latestSouth?.trade_date?.slice(0, 10)}</span>
        </div>
      </div>

      {/* ──── 南向趋势 ──── */}
      {southTrend.length >= 2 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
          <p className="text-xs font-medium text-zinc-400 mb-2">南向总额趋势</p>
          <ZoomableSparkline
            data={southTrend.map(r => r.total / 1e8)}
            labels={southTrend.map(r => r.trade_date)}
            color="#8b5cf6" height={50}
          />
        </div>
      )}

      {/* ──── 南向个股 ──── */}
      <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <summary className="p-3 flex items-center gap-2 list-none cursor-pointer">
          <span className="text-xs font-medium text-zinc-400">南向个股 TOP{Math.min(southStocks.length, 20)}</span>
          <span className="ml-auto text-[10px] text-zinc-500">{southStocks[0]?.trade_date?.slice(5) || ""}</span>
        </summary>
        <div className="px-3 pb-3 space-y-0.5">
          {southStocks.map(s => {
              const trend = southTrends[s.symbol] || [];
              return (
                <div key={s.symbol} className="flex items-center gap-2 text-[10px] py-0.5 border-b border-zinc-800/30 last:border-0">
                  <span className="text-zinc-300 truncate flex-1">{s.name || s.symbol}</span>
                  <span className={cn("tabular-nums font-mono", s.net_inflow >= 0 ? "text-emerald-400" : "text-rose-400")}>{fmtB(s.net_inflow)}</span>
                  <span className={cn("tabular-nums", s.change_pct >= 0 ? "text-emerald-400" : "text-rose-400")}>{pctText(s.change_pct)}</span>
                  {trend.length >= 2 && (
                    <span className="w-12 shrink-0">
                      <SparklineChart data={trend} color={s.net_inflow >= 0 ? "#10b981" : "#ef4444"} height={18} className="w-12 h-[18px]" />
                    </span>
                  )}
                </div>
              );
            })}
        </div>
      </details>

      {/* ──── ETF ──── */}
      {etfFlows.length > 0 && (
        <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
          <summary className="p-3 flex items-center gap-2 list-none cursor-pointer">
            <span className="text-xs font-medium text-zinc-400">ETF 资金流</span>
          </summary>
          <div className="px-3 pb-3 grid grid-cols-3 gap-x-3 gap-y-1 text-[10px]">
            {etfFlows.map(e => (
              <div key={e.symbol} className="flex justify-between">
                <span className="text-zinc-500 truncate">{e.name}</span>
                <span className={cn("tabular-nums", e.pct_change >= 0 ? "text-emerald-400" : "text-rose-400")}>{pctText(e.pct_change)}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* ──── 指标详解 ──── */}
      <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <summary className="p-3 text-xs font-medium text-zinc-400 list-none cursor-pointer">📖 指标详解</summary>
        <div className="px-3 pb-3 space-y-2 text-[10px] leading-relaxed">
          {[
            { k: "北向资金", d: "外资通过沪/深股通买入A股的渠道。持股市值变化=当日持仓市值变动(含价格因素)。正值=外资增持，负值=外资减持。连续多日净流出=外资撤退信号。" },
            { k: "南向资金", d: "内地资金通过港股通买入港股的渠道。南向持续净流入=内地资金看好港股；南向大幅流出=避险。通常南向与恒生指数正相关。" },
            { k: "板块聚合", d: "将个股按行业分组汇总北向净买入，识别外资偏好板块。银行/保险持续净买=外资防御配置；半导体/新能源=成长进攻。" },
            { k: "ETF资金流", d: "核心ETF的当日涨跌幅反映市场风格偏好。宽基ETF普涨=beta行情；行业ETF分化=结构性行情；跨境ETF异动=外盘传导。" },
            { k: "周度delta", d: "本周总额-上周总额的变化比例。正值=资金加速流入；负值=流入放缓或反转。连续2周负值=趋势转弱信号。" },
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
