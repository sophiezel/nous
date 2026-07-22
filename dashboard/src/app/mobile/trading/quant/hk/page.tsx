import {
  getQuantPortfolio,
  getQuantNav,
  getQuantTrades,
  getFactorIC,
} from "@/lib/db";
import { QuantDetailClient } from "../a/QuantDetailClient";

export const dynamic = "force-dynamic";
export const revalidate = 15;

export default function QuantHkDetailPage() {
  const positions = getQuantPortfolio("hk", 20);
  const nav = getQuantNav("hk", 60);
  const trades = getQuantTrades("hk", 30);
  const factorIC = getFactorIC(30);

  return (
    <QuantDetailClient
      market="港股"
      positions={positions}
      navData={nav.map(n => ({
        trade_date: n.trade_date,
        nav: n.nav,
        daily_return: n.daily_return,
        benchmark_return: n.benchmark_return,
      }))}
      trades={trades}
      factorIC={factorIC}
    />
  );
}
