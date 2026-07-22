"use client";

import { TradeCard } from "@/components/mobile/trade-card";
import { SparklineChart } from "@/components/mobile/sparkline";
import { TrendingUp } from "lucide-react";

interface Props {
  aCount: number;
  aNavLatest: number | null;
  aDailyReturn: number | null;
  aTotalPnl: number;
  aTotalPnlPct: number;
  aNavData: number[];
  hkCount: number;
  hkNavLatest: number | null;
  hkDailyReturn: number | null;
  hkTotalPnl: number;
  hkTotalPnlPct: number;
  hkNavData: number[];
}

export function PaperMarketClient(props: Props) {
  const { aCount, aNavLatest, aDailyReturn, aTotalPnl, aTotalPnlPct, aNavData,
    hkCount, hkNavLatest, hkDailyReturn, hkTotalPnl, hkTotalPnlPct, hkNavData } = props;

  return (
    <div className="space-y-3 pb-2">
      <div className="flex items-center gap-2 mb-1">
        <TrendingUp className="w-4 h-4 text-emerald-400" />
        <h1 className="text-sm font-bold text-zinc-100">模拟盘</h1>
      </div>

      <TradeCard
        title="A股模拟盘"
        nav={aNavLatest}
        dailyReturn={aDailyReturn}
        totalPnl={aTotalPnl}
        totalPnlPct={aTotalPnlPct}
        positionCount={aCount}
        accent="emerald"
        navChart={
          aNavData.length >= 2 ? (
            <SparklineChart data={aNavData} color="#10b981" height={36} className="w-full h-9" />
          ) : undefined
        }
        href="/mobile/trading/paper/a"
      />

      <TradeCard
        title="港股模拟盘"
        nav={hkNavLatest}
        dailyReturn={hkDailyReturn}
        totalPnl={hkTotalPnl}
        totalPnlPct={hkTotalPnlPct}
        positionCount={hkCount}
        accent="blue"
        navChart={
          hkNavData.length >= 2 ? (
            <SparklineChart data={hkNavData} color="#3b82f6" height={36} className="w-full h-9" />
          ) : undefined
        }
        href="/mobile/trading/paper/hk"
      />
    </div>
  );
}
