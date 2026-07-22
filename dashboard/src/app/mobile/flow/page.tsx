import { fetchAPI } from "@/lib/api";
import { PagePoller } from "@/components/PagePoller";
import { SparklineChart } from "@/components/mobile/sparkline";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { DollarSign, TrendingUp, TrendingDown, ArrowUpRight, ArrowDownRight, Info } from "lucide-react";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const revalidate = 60;

function fmtBillions(n: number | null): string {
  if (n == null) return "--";
  return `${(n / 1e8).toFixed(1)}亿`;
}

export default async function MobileFlowPage() {
  const [marginRes, lhbRes, blockTradesRes] = await Promise.all([
    fetchAPI("/v1/flow/margin?days=120"),
    fetchAPI("/v1/flow/lhb?limit=20"),
    fetchAPI("/v1/flow/block?limit=20"),
  ]);

  const rawMargin: any[] = marginRes ?? [];
  const lhb: any[] = lhbRes ?? [];
  const blockTrades: any[] = blockTradesRes ?? [];

  // Split margin response by type: 'balance' → margin, 'buy' → marginBuy, 'short' → marginShort
  const margin: any[] = [];
  const marginBuy: any[] = [];
  const marginShort: any[] = [];

  for (const row of rawMargin) {
    if (row.type === "balance" || row.margin_balance != null) {
      margin.push(row);
    }
    if (row.type === "buy" || row.margin_buy != null) {
      marginBuy.push(row);
    }
    if (row.type === "short" || row.short_balance != null) {
      marginShort.push(row);
    }
  }

  // Fallback: if splitting didn't produce results, use all data as-is
  const marginData = margin.length > 0 ? [...margin].reverse() : [...rawMargin].reverse();
  const marginBuyData = marginBuy.length > 0 ? [...marginBuy].reverse() : [...rawMargin].reverse();
  const marginShortData = marginShort.length > 0 ? [...marginShort].reverse() : [...rawMargin].reverse();

  const turnoverRes = await fetchAPI<{total: number | null}>("/v1/market/turnover").catch(() => ({total: null}));
  const marketTurnover = turnoverRes?.total ?? null;

  // ── Computed metrics ──
  const latestMargin = marginData[marginData.length - 1];
  const latestMarginBuy = marginBuyData[marginBuyData.length - 1];
  const latestShort = marginShortData.length > 0 ? marginShortData[marginShortData.length - 1] : null;
  const shortPrev = marginShortData.length > 1 ? marginShortData[marginShortData.length - 2] : null;
  const shortDelta = latestShort && shortPrev?.short_balance
    ? ((latestShort.short_balance - shortPrev.short_balance) / shortPrev.short_balance * 100)
    : null;
  const leverageRate = latestMarginBuy && marketTurnover
    ? (latestMarginBuy.margin_buy / marketTurnover * 100) : null;

  // ── Total LHB buy/sell ──
  const lhbTotalBuy = lhb.reduce((s: number, r: any) => s + (r.l_buy || 0), 0);
  const lhbTotalSell = lhb.reduce((s: number, r: any) => s + (r.l_sell || 0), 0);

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-bold">两融资金</h1>

      {/* ──── Hero: 融资余额 ──── */}
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-3">
          <DollarSign className="w-5 h-5 text-amber-400 shrink-0" />
          <div>
            <div className="flex items-baseline gap-1">
              <span className="text-2xl font-bold tabular-nums text-amber-400">
                {latestMargin ? (latestMargin.margin_balance / 1e8).toFixed(0) : "--"}
              </span>
              <span className="text-xs text-zinc-500">亿</span>
            </div>
            <div className="flex gap-3 text-[10px] mt-0.5">
              <span className="text-zinc-500">融资余额</span>
              <span className="text-zinc-600">{latestMargin?.trade_date?.slice(0, 10)}</span>
            </div>
          </div>
          <div className="ml-auto text-right">
            <p className="text-[10px] text-zinc-500">今日买入</p>
            <p className="text-sm font-bold tabular-nums text-emerald-400">
              {latestMarginBuy ? (latestMarginBuy.margin_buy / 1e8).toFixed(0) + "亿" : "--"}
            </p>
          </div>
        </div>
        {leverageRate != null && (
          <div className="mt-3 flex items-center gap-2 text-[10px]">
            <span className={cn("font-semibold", leverageRate > 12 ? "text-rose-400" : leverageRate > 8 ? "text-amber-400" : "text-emerald-400")}>
              杠杆率 {leverageRate.toFixed(1)}%
            </span>
            <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div className={cn("h-full rounded-full", leverageRate > 12 ? "bg-rose-500" : leverageRate > 8 ? "bg-amber-500" : "bg-emerald-500")}
                style={{ width: `${Math.min(leverageRate / 15 * 100, 100)}%` }} />
            </div>
          </div>
        )}
      </div>

      {/* ──── 融资余额趋势 ──── */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
        <p className="text-xs font-medium text-zinc-400 mb-2">融资余额趋势</p>
        {marginData.length >= 2 && (
          <ZoomableSparkline
            data={marginData.map(r => r.margin_balance / 1e8)}
            labels={marginData.map(r => r.trade_date)}
            color="#f59e0b" height={50}
          />
        )}
      </div>

      {/* ──── 融资买入趋势 ──── */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
        <p className="text-xs font-medium text-zinc-400 mb-2">融资买入趋势</p>
        {marginBuyData.length >= 2 && (
          <ZoomableSparkline
            data={marginBuyData.map(r => r.margin_buy / 1e8)}
            labels={marginBuyData.map(r => r.trade_date)}
            color="#10b981" height={50}
          />
        )}
      </div>

      {/* ──── 融券趋势 ──── */}
      <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden" open>
        <summary className="p-3 flex items-center gap-2 list-none cursor-pointer">
          <TrendingDown className="w-3.5 h-3.5 text-rose-400" />
          <span className="text-xs font-medium text-zinc-400">融券余额</span>
          <span className="ml-auto text-[10px] tabular-nums font-mono text-rose-400">
            {latestShort ? (latestShort.short_balance / 1e8).toFixed(1) + "亿" : "--"}
          </span>
          {shortDelta != null && (
            <span className={cn("text-[10px]", shortDelta >= 0 ? "text-rose-400" : "text-emerald-400")}>
              {shortDelta >= 0 ? "↑" : "↓"}{Math.abs(shortDelta).toFixed(1)}%
            </span>
          )}
        </summary>
        <div className="px-3 pb-3">
          {marginShortData.length >= 2 && (
            <SparklineChart
              data={marginShortData.map(r => r.short_balance / 1e8)}
              color="#ef4444" height={36} className="w-full h-9"
            />
          )}
        </div>
      </details>

      {/* ──── 龙虎榜 ──── */}
      <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden" open>
        <summary className="p-3 flex items-center gap-2 list-none cursor-pointer">
          <ArrowUpRight className="w-3.5 h-3.5 text-violet-400" />
          <span className="text-xs font-medium text-zinc-400">龙虎榜 TOP{Math.min(lhb.length, 20)}</span>
          <span className="ml-auto text-[10px] text-zinc-500">
            {lhb[0]?.trade_date?.slice(5) || ""}
          </span>
        </summary>
        <div className="px-3 pb-3 space-y-1">
          <div className="flex gap-2 text-[9px] text-zinc-500 mb-1">
            <span>总买 {fmtBillions(lhbTotalBuy)}</span>
            <span>总卖 {fmtBillions(lhbTotalSell)}</span>
          </div>
          {lhb.slice(0, 15).map((r: any) => (
            <div key={r.symbol} className="flex items-center gap-2 text-[10px] py-0.5 border-b border-zinc-800/30 last:border-0">
              <span className="font-mono text-zinc-500 w-12 shrink-0">{r.symbol}</span>
              <span className="text-zinc-300 truncate flex-1">{r.name}</span>
              <span className={cn("tabular-nums font-mono shrink-0", r.pct_change >= 0 ? "text-emerald-400" : "text-rose-400")}>
                {r.pct_change >= 0 ? "+" : ""}{r.pct_change.toFixed(1)}%
              </span>
              <span className={cn("tabular-nums font-mono shrink-0 w-16 text-right", r.net_amount >= 0 ? "text-emerald-400" : "text-rose-400")}>
                {r.net_amount >= 0 ? "+" : ""}{(r.net_amount / 1e8).toFixed(2)}亿
              </span>
              <span className="text-[8px] text-zinc-600 w-16 truncate text-right">{r.reason?.replace(/的证券$/, "").slice(0, 16)}</span>
            </div>
          ))}
        </div>
      </details>

      {/* ──── 大宗交易 ──── */}
      {blockTrades.length > 0 && (
        <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
          <summary className="p-3 flex items-center gap-2 list-none cursor-pointer">
            <ArrowDownRight className="w-3.5 h-3.5 text-cyan-400" />
            <span className="text-xs font-medium text-zinc-400">大宗交易 TOP{Math.min(blockTrades.length, 20)}</span>
            <span className="ml-auto text-[10px] text-zinc-500">
              {blockTrades[0]?.trade_date?.slice(5) || ""}
            </span>
          </summary>
          <div className="px-3 pb-3 space-y-1">
            <div className="flex gap-2 text-[9px] text-zinc-500 mb-1">
              <span>大宗价 vs 收盘价 → 折/溢价</span>
            </div>
            {blockTrades.slice(0, 10).map((b: any) => {
              const discPrem = b.close ? ((b.price - b.close) / b.close * 100) : null;
              return (
                <div key={b.symbol + b.price} className="flex items-center gap-2 text-[10px] py-0.5 border-b border-zinc-800/30 last:border-0">
                  <span className="font-mono text-zinc-500 w-12 shrink-0">{b.symbol}</span>
                  <span className="text-zinc-300 truncate flex-1">{b.name}</span>
                  <span className="tabular-nums text-zinc-500 shrink-0">{b.price.toFixed(2)}</span>
                  {discPrem != null && (
                    <span className={cn("tabular-nums font-mono shrink-0 w-14 text-right", discPrem > 1 ? "text-emerald-400" : discPrem < -1 ? "text-rose-400" : "text-zinc-500")}>
                      {discPrem > 0 ? "溢价" : discPrem < 0 ? "折价" : "平价"}{discPrem !== 0 ? Math.abs(discPrem).toFixed(1) + "%" : ""}
                    </span>
                  )}
                  <span className="tabular-nums text-zinc-500 shrink-0">{(b.volume / 1e4).toFixed(0)}万股</span>
                  <span className="tabular-nums font-mono text-cyan-400 shrink-0">{(b.amount / 1e8).toFixed(2)}亿</span>
                </div>
              );
            })}
          </div>
        </details>
      )}

      {/* ──── 指标详解 ──── */}
      <details className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <summary className="p-3 text-xs font-medium text-zinc-400 list-none cursor-pointer">📖 指标详解</summary>
        <div className="px-3 pb-3 space-y-2 text-[10px] leading-relaxed">
          {[
            { k: "融资余额", d: "投资者通过券商借入资金买入股票的总余额。余额持续上升=杠杆资金看多；余额快速下降=去杠杆→踩踏风险。1.4万亿以上=偏高。" },
            { k: "融资买入额", d: "当日新增融资买入金额。反映当日杠杆资金的入场意愿。比余额更敏感的短线指标。日均1500亿以上=交投活跃。" },
            { k: "杠杆率", d: "融资买入额/全市场成交额。>12%=杠杆资金主导→过热预警；<6%=杠杆资金谨慎→正常或冰点。>15%=极端风险。" },
            { k: "融券余额", d: "投资者借入股票卖出(做空)的总余额。余额上升=做空力量增加→利空；余额下降=空头回补→短期利好。" },
            { k: "龙虎榜", d: "当日涨跌幅、换手率、振幅异常的个股，披露前五大买卖营业部。净买入=游资看好；净卖出=游资出逃。上榜原因是判断股价性质的关键。" },
            { k: "大宗交易", d: "单笔交易量达到规定最低限额的场外交易。折价大宗=股东减持或机构对倒；溢价大宗=机构看好、抢筹信号。" },
          ].map(t => (
            <div key={t.k} className="bg-zinc-800/40 rounded-lg p-2">
              <span className="font-medium text-zinc-300">{t.k}</span>
              <p className="text-zinc-500 mt-0.5">{t.d}</p>
            </div>
          ))}
        </div>
      </details>
      <PagePoller pollKey="flow" />
    </div>
  );
}
