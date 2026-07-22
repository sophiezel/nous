import { fetchAPI } from "@/lib/api";
import { SparklineChart } from "@/components/mobile/sparkline";
import { Globe, TrendingUp } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 60;

interface IndexCard {
  label: string;
  data: number[];
  latest: number | null;
  date: string;
  color: string;
}

export default async function MobileIndexPage() {
  const [globalRes, shRes, szRes] = await Promise.all([
    fetchAPI("/v1/global/index?symbols=spx,ndx,dji,vix"),
    fetchAPI("/v1/index/daily?codes=IDX_000001&days=60"),
    fetchAPI("/v1/index/daily?codes=IDX_399001&days=60"),
  ]);

  const spxData: any[] = globalRes?.spx ?? [];
  const ndxData: any[] = globalRes?.ndx ?? [];
  const djiData: any[] = globalRes?.dji ?? [];
  const vixData: any[] = globalRes?.vix ?? [];

  const shData: any[] = shRes ?? [];
  const szData: any[] = szRes ?? [];

  const spx: any[] = (Array.isArray(spxData) ? spxData : []).reverse();
  const ndx: any[] = (Array.isArray(ndxData) ? ndxData : []).reverse();
  const dji: any[] = (Array.isArray(djiData) ? djiData : []).reverse();
  const vix: any[] = (Array.isArray(vixData) ? vixData : []).reverse();
  const sh: any[] = (Array.isArray(shData) ? shData : []).reverse();
  const sz: any[] = (Array.isArray(szData) ? szData : []).reverse();

  const cards: IndexCard[] = [
    { label: "上证指数", data: sh.map(r => r.close), latest: sh[sh.length-1]?.close ?? null, date: sh[sh.length-1]?.trade_date?.slice(5) ?? "", color: "#ef4444" },
    { label: "深证成指", data: sz.map(r => r.close), latest: sz[sz.length-1]?.close ?? null, date: sz[sz.length-1]?.trade_date?.slice(5) ?? "", color: "#f59e0b" },
    { label: "标普500", data: spx.map(r => r.close), latest: spx[spx.length-1]?.close ?? null, date: spx[spx.length-1]?.trade_date?.slice(5) ?? "", color: "#10b981" },
    { label: "纳斯达克", data: ndx.map(r => r.close), latest: ndx[ndx.length-1]?.close ?? null, date: ndx[ndx.length-1]?.trade_date?.slice(5) ?? "", color: "#3b82f6" },
    { label: "道琼斯", data: dji.map(r => r.close), latest: dji[dji.length-1]?.close ?? null, date: dji[dji.length-1]?.trade_date?.slice(5) ?? "", color: "#8b5cf6" },
    { label: "VIX恐慌", data: vix.map(r => r.close), latest: vix[vix.length-1]?.close ?? null, date: vix[vix.length-1]?.trade_date?.slice(5) ?? "", color: "#ef4444" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="w-5 h-5 rounded bg-emerald-500 flex items-center justify-center shrink-0">
          <Globe className="w-3 h-3 text-white" />
        </div>
        <h1 className="text-sm font-bold">全球指数</h1>
      </div>

      {cards.map(card => (
        <div key={card.label} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="w-4 h-4" style={{color:card.color}} />
            <span className="text-xs font-medium text-zinc-400">{card.label}</span>
            <span className="text-[10px] ml-auto font-bold text-zinc-200">
              {card.latest ? card.latest.toFixed(card.label === "VIX恐慌" ? 2 : 0) : "--"}
            </span>
          </div>
          {card.data.length >= 2 && (
            <SparklineChart data={card.data.slice(-30)} color={card.color} height={40} className="w-full h-10" />
          )}
          <span className="text-[9px] text-zinc-600 mt-1 block">{card.data.length} 个交易日</span>
        </div>
      ))}
    </div>
  );
}
