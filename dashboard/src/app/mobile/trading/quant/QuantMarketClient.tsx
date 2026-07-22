"use client";

import { TradeCard } from "@/components/mobile/trade-card";
import { SparklineChart } from "@/components/mobile/sparkline";
import { TrendingUp, BrainCircuit } from "lucide-react";

interface Props {
  aCount: number;
  aNavLatest: number | null;
  aDailyReturn: number | null;
  aNavData: number[];
  hkCount: number;
  hkNavLatest: number | null;
  hkDailyReturn: number | null;
  hkNavData: number[];
}

export function QuantMarketClient(props: Props) {
  const { aCount, aNavLatest, aDailyReturn, aNavData,
    hkCount, hkNavLatest, hkDailyReturn, hkNavData } = props;

  return (
    <div className="space-y-3 pb-2">
      <div className="flex items-center gap-2 mb-1">
        <BrainCircuit className="w-4 h-4 text-violet-400" />
        <h1 className="text-sm font-bold text-zinc-100">量化盘</h1>
      </div>

      <TradeCard
        title="A股量化盘"
        nav={aNavLatest}
        dailyReturn={aDailyReturn}
        totalPnl={null}
        totalPnlPct={null}
        positionCount={aCount}
        accent="violet"
        navChart={
          aNavData.length >= 2 ? (
            <SparklineChart data={aNavData} color="#8b5cf6" height={36} className="w-full h-9" />
          ) : undefined
        }
        href="/mobile/trading/quant/a"
      />

      <TradeCard
        title="港股量化盘"
        nav={hkNavLatest}
        dailyReturn={hkDailyReturn}
        totalPnl={null}
        totalPnlPct={null}
        positionCount={hkCount}
        accent="amber"
        navChart={
          hkNavData.length >= 2 ? (
            <SparklineChart data={hkNavData} color="#f59e0b" height={36} className="w-full h-9" />
          ) : undefined
        }
        href="/mobile/trading/quant/hk"
      />
    </div>
  );
}
