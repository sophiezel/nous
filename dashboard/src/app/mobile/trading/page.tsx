import {
  getPaperPortfolio,
  getPaperNav,
  getQuantPortfolio,
  getQuantNav,
  getLivePortfolio,
  getRiskOverview,
  getBenchmarkComparison,
} from "@/lib/db";
import { TradingOverviewClient } from "./TradingOverviewClient";

export const dynamic = "force-dynamic";
export const revalidate = 30;

export default function TradingPage() {
  // Paper A
  const paperAPositions = getPaperPortfolio("a") as any[];
  const paperANav = getPaperNav("a", 30);
  const paperATotalPnl = paperAPositions.reduce((s, p) => s + (p.pnl || 0), 0);
  const paperATotalVal = paperAPositions.reduce((s, p) => s + (p.market_value || 0), 0);
  const paperATotalPnlPct = paperATotalVal > 0 ? (paperATotalPnl / (paperATotalVal - paperATotalPnl)) * 100 : 0;
  const paperANavLatest = paperANav[0]?.nav ?? null;
  const paperADailyReturn = paperANav[0]?.daily_return ?? null;

  // Paper HK
  const paperHkPositions = getPaperPortfolio("hk") as any[];
  const paperHkNav = getPaperNav("hk", 30);
  const paperHkTotalPnl = paperHkPositions.reduce((s, p) => s + (p.pnl || 0), 0);
  const paperHkTotalVal = paperHkPositions.reduce((s, p) => s + (p.market_value || 0), 0);
  const paperHkTotalPnlPct = paperHkTotalVal > 0 ? (paperHkTotalPnl / (paperHkTotalVal - paperHkTotalPnl)) * 100 : 0;
  const paperHkNavLatest = paperHkNav[0]?.nav ?? null;
  const paperHkDailyReturn = paperHkNav[0]?.daily_return ?? null;

  // Quant A
  const quantAPositions = getQuantPortfolio("a", 10);
  const quantANav = getQuantNav("a", 30);
  const quantANavLatest = quantANav[0]?.nav ?? null;
  const quantADailyReturn = quantANav[0]?.daily_return ?? null;

  // Quant HK
  const quantHkPositions = getQuantPortfolio("hk", 10);
  const quantHkNav = getQuantNav("hk", 30);
  const quantHkNavLatest = quantHkNav[0]?.nav ?? null;
  const quantHkDailyReturn = quantHkNav[0]?.daily_return ?? null;

  // Live
  const livePositions = getLivePortfolio();
  const liveTotalWeight = livePositions.reduce((s, p) => s + p.weight_pct, 0);
  const liveAvgPnl = livePositions.length > 0
    ? livePositions.reduce((s, p) => s + p.pnl_pct, 0) / livePositions.length
    : null;

  // Risk
  const risk = getRiskOverview();

  // Benchmark
  const benchmark = getBenchmarkComparison();

  return (
    <TradingOverviewClient
      paperA={{ positions: paperAPositions.length, navLatest: paperANavLatest, dailyReturn: paperADailyReturn, totalPnl: paperATotalPnl, totalPnlPct: paperATotalPnlPct, navData: paperANav.map(n => n.nav).reverse() }}
      paperHk={{ positions: paperHkPositions.length, navLatest: paperHkNavLatest, dailyReturn: paperHkDailyReturn, totalPnl: paperHkTotalPnl, totalPnlPct: paperHkTotalPnlPct, navData: paperHkNav.map(n => n.nav).reverse() }}
      quantA={{ positions: quantAPositions.length, navLatest: quantANavLatest, dailyReturn: quantADailyReturn, navData: quantANav.map(n => n.nav).reverse() }}
      quantHk={{ positions: quantHkPositions.length, navLatest: quantHkNavLatest, dailyReturn: quantHkDailyReturn, navData: quantHkNav.map(n => n.nav).reverse() }}
      livePositions={livePositions.length}
      liveAvgPnl={liveAvgPnl}
      risk={risk}
      benchmark={benchmark}
    />
  );
}
