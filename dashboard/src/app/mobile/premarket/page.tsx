import { getPremarketSummary, getMarginShortHistory, getHsgtHistory, getGlobalIndexHistory, getLhbTop, getScreenerDb } from "@/lib/db";
import { SparklineChart } from "@/components/mobile/sparkline";
import { BarChart } from "@/components/mobile/bar-chart";
import { TrendingUp, DollarSign, Activity, Globe, ArrowUpDown } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 60;

function fmt(v: number | null | undefined, unit = ""): string {
  if (v == null) return "--";
  if (Math.abs(v) >= 1e12) return (v / 1e12).toFixed(2) + "万亿" + unit;
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(1) + "亿" + unit;
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(1) + "万" + unit;
  return v.toFixed(2) + unit;
}

function pctColor(v: number | null | undefined): string {
  if (v == null) return "text-zinc-400";
  return v >= 0 ? "text-emerald-400" : "text-red-400";
}

function pctStr(v: number | null | undefined): string {
  if (v == null) return "--";
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

export default function MobilePremarketPage() {
  const today = new Date().toISOString().slice(0, 10);
  const isWeekend = [0, 6].includes(new Date().getDay());

  // 两融做空
  const marginShort = [...getMarginShortHistory(60)].reverse();
  const msLatest = marginShort[marginShort.length - 1];
  const msPrev = marginShort.length >= 2 ? marginShort[marginShort.length - 2] : null;
  const msChange = msLatest && msPrev ? ((msLatest.margin_balance - msPrev.margin_balance) / msPrev.margin_balance) * 100 : 0;
  const msShortChange = msLatest && msPrev ? ((msLatest.short_balance - msPrev.short_balance) / msPrev.short_balance) * 100 : 0;

  // 北向/南向
  const hsgtAll = [...getHsgtHistory(60)].reverse();
  const north = hsgtAll.filter((r: any) => r.direction === "north");
  const south = hsgtAll.filter((r: any) => r.direction === "south");
  const northLatest = north[north.length - 1];
  const southLatest = south[south.length - 1];

  // KWEB / CWEB
  const kw = [...getGlobalIndexHistory("KWEB", 30)].reverse();
  const cw = [...getGlobalIndexHistory("CWEB", 30)].reverse();
  const kwLatest = kw[kw.length - 1];
  const cwLatest = cw[cw.length - 1];
  const kwChange = kw.length >= 2 ? ((kwLatest.close - kw[kw.length - 2].close) / kw[kw.length - 2].close) * 100 : 0;
  const cwChange = cw.length >= 2 ? ((cwLatest.close - cw[cw.length - 2].close) / cw[cw.length - 2].close) * 100 : 0;

  // 龙虎榜
  const lhbList = getLhbTop(10);

  // 状态
  const statusLabel = isWeekend ? "休市" : today <= "09:30" ? "盘前准备" : "交易时段";
  const statusColor = isWeekend ? "bg-zinc-500" : "bg-emerald-500";

  // Trend cards
  const trendCards = [
    {
      label: "融资余额", value: msLatest ? fmt(msLatest.margin_balance) : "--",
      change: msChange, changeLabel: "日环比",
      data: marginShort.map(r => r.margin_balance / 1e8), color: "#3b82f6", icon: DollarSign,
    },
    {
      label: "融券余额", value: msLatest ? fmt(msLatest.short_balance) : "--",
      change: msShortChange, changeLabel: "日环比",
      data: marginShort.map(r => r.short_balance / 1e8), color: "#8b5cf6", icon: ArrowUpDown,
    },
    {
      label: "北向净买", value: northLatest ? fmt(northLatest.net_buy) : "--",
      change: northLatest?.net_buy ? 0 : null,
      changeLabel: "最新值",
      data: north.slice(-20).map((r: any) => r.net_buy / 1e8), color: "#f59e0b", icon: TrendingUp,
    },
    {
      label: "南向净买", value: southLatest ? fmt(southLatest.net_buy) : "--",
      change: southLatest?.net_buy ? 0 : null,
      changeLabel: "最新值",
      data: south.slice(-20).map((r: any) => r.net_buy / 1e8), color: "#10b981", icon: TrendingUp,
    },
    {
      label: "KWEB", value: kwLatest ? kwLatest.close.toFixed(2) : "--",
      change: kwChange, changeLabel: "日环比",
      data: kw.map(r => r.close), color: "#ef4444", icon: Globe,
    },
    {
      label: "CWEB", value: cwLatest ? cwLatest.close.toFixed(2) : "--",
      change: cwChange, changeLabel: "日环比",
      data: cw.map(r => r.close), color: "#f97316", icon: Globe,
    },
  ];

  return (
    <div className="space-y-3">
      {/* 顶部日期+状态 */}
      <div className="flex items-center gap-2">
        <div className="w-5 h-5 rounded bg-emerald-500 flex items-center justify-center shrink-0">
          <Activity className="w-3 h-3 text-white" />
        </div>
        <h1 className="text-sm font-bold">盘前参考</h1>
        <span className="text-[10px] text-zinc-500 ml-auto">{today.slice(5)}</span>
        <span className={`text-[9px] px-2 py-0.5 rounded-full text-white ${statusColor}`}>{statusLabel}</span>
      </div>

      {/* 水平滚动趋势卡片 */}
      <div className="overflow-x-auto -mx-4 px-4 scrollbar-none">
        <div className="flex gap-2.5 pb-1" style={{ minWidth: "max-content" }}>
          {trendCards.map(card => {
            const Icon = card.icon;
            const chg = card.change != null ? card.change : null;
            return (
              <div key={card.label} className="w-36 shrink-0 rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
                <div className="flex items-center gap-1 mb-1.5">
                  <Icon className="w-3 h-3" style={{ color: card.color }} />
                  <span className="text-[9px] text-zinc-500">{card.label}</span>
                </div>
                <div className="text-base font-bold tabular-nums text-zinc-100 truncate">{card.value}</div>
                {chg != null && (
                  <span className={`text-[9px] font-medium ${pctColor(chg)}`}>
                    {card.changeLabel}: {pctStr(chg)}
                  </span>
                )}
                {chg == null && card.changeLabel && (
                  <span className="text-[9px] text-zinc-500">{card.changeLabel}</span>
                )}
                {card.data.length >= 2 && (
                  <SparklineChart data={card.data.slice(-20)} color={card.color} height={28} className="w-full h-7 mt-1" />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 龙虎榜净买入排行 */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
        <div className="flex items-center gap-2 mb-2">
          <DollarSign className="w-4 h-4 text-amber-400" />
          <span className="text-xs font-medium text-zinc-400">龙虎榜净买入排行</span>
          {lhbList.length > 0 && (
            <span className="text-[9px] text-zinc-600 ml-auto">{lhbList[0].trade_date?.slice(5)}</span>
          )}
        </div>
        {lhbList.length === 0 ? (
          <p className="text-[10px] text-zinc-500 text-center py-4">暂无龙虎榜数据</p>
        ) : (
          <div className="space-y-1">
            {lhbList.map((item: any, i: number) => (
              <div key={item.symbol || i} className="flex items-center gap-2 text-[10px] py-1.5 border-b border-zinc-800/30 last:border-0">
                <span className="text-zinc-600 w-4 text-center">{i + 1}</span>
                <span className="font-mono text-zinc-500 w-16 shrink-0">{item.symbol}</span>
                <span className="text-zinc-200 flex-1 truncate">{item.name}</span>
                <span className={pctColor(item.pct_change)}>{pctStr(item.pct_change)}</span>
                <span className={pctColor(item.net_amount)}>{fmt(item.net_amount)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
