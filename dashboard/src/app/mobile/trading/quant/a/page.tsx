import {
  getQuantPortfolio,
  getQuantNav,
  getQuantTrades,
  getFactorIC,
} from "@/lib/db";
import { QuantDetailClient } from "./QuantDetailClient";

export const dynamic = "force-dynamic";
export const revalidate = 15;

export default function QuantADetailPage() {
  const positions = getQuantPortfolio("a", 20);
  const nav = getQuantNav("a", 60);
  const trades = getQuantTrades("a", 30);
  const factorIC = getFactorIC(30);

  return (
    <QuantDetailClient
      market="A股"
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
