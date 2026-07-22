import {
  getPaperPortfolio,
  getPaperNav,
} from "@/lib/db";
import { PaperMarketClient } from "./PaperMarketClient";

export const dynamic = "force-dynamic";
export const revalidate = 30;

export default function PaperMarketPage() {
  const aPositions = getPaperPortfolio("a") as any[];
  const hkPositions = getPaperPortfolio("hk") as any[];
  const aNav = getPaperNav("a", 30);
  const hkNav = getPaperNav("hk", 30);

  const aTotalPnl = aPositions.reduce((s, p) => s + (p.pnl || 0), 0);
  const aTotalVal = aPositions.reduce((s, p) => s + (p.market_value || 0), 0);
  const hkTotalPnl = hkPositions.reduce((s, p) => s + (p.pnl || 0), 0);
  const hkTotalVal = hkPositions.reduce((s, p) => s + (p.market_value || 0), 0);

  return (
    <PaperMarketClient
      aCount={aPositions.length}
      aNavLatest={aNav[0]?.nav ?? null}
      aDailyReturn={aNav[0]?.daily_return ?? null}
      aTotalPnl={aTotalPnl}
      aTotalPnlPct={aTotalVal > 0 ? (aTotalPnl / (aTotalVal - aTotalPnl)) * 100 : 0}
      aNavData={aNav.map(n => n.nav).reverse()}
      hkCount={hkPositions.length}
      hkNavLatest={hkNav[0]?.nav ?? null}
      hkDailyReturn={hkNav[0]?.daily_return ?? null}
      hkTotalPnl={hkTotalPnl}
      hkTotalPnlPct={hkTotalVal > 0 ? (hkTotalPnl / (hkTotalVal - hkTotalPnl)) * 100 : 0}
      hkNavData={hkNav.map(n => n.nav).reverse()}
    />
  );
}
