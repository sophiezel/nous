import {
  getQuantPortfolio,
  getQuantNav,
  getQuantTrades,
} from "@/lib/db";
import { QuantMarketClient } from "./QuantMarketClient";

export const dynamic = "force-dynamic";
export const revalidate = 30;

export default function QuantMarketPage() {
  const aPositions = getQuantPortfolio("a", 10);
  const hkPositions = getQuantPortfolio("hk", 10);
  const aNav = getQuantNav("a", 30);
  const hkNav = getQuantNav("hk", 30);

  return (
    <QuantMarketClient
      aCount={aPositions.length}
      aNavLatest={aNav[0]?.nav ?? null}
      aDailyReturn={aNav[0]?.daily_return ?? null}
      aNavData={aNav.map(n => n.nav).reverse()}
      hkCount={hkPositions.length}
      hkNavLatest={hkNav[0]?.nav ?? null}
      hkDailyReturn={hkNav[0]?.daily_return ?? null}
      hkNavData={hkNav.map(n => n.nav).reverse()}
    />
  );
}
