import {
  getLivePortfolio,
  getLiveDiagnosis,
  getBenchmarkComparison,
  getPaperNav,
} from "@/lib/db";
import { PortfolioClient } from "./PortfolioClient";

export const dynamic = "force-dynamic";
export const revalidate = 30;

export default function PortfolioPage() {
  const livePositions = getLivePortfolio();
  const diagnosis = getLiveDiagnosis();
  const benchmark = getBenchmarkComparison();
  // Use portfolio_nav from screener.db for live NAV history
  const liveNav = getPaperNav("a", 60);

  return (
    <PortfolioClient
      positions={livePositions}
      diagnosis={diagnosis}
      benchmark={benchmark}
      navData={liveNav.map(n => ({
        trade_date: n.trade_date,
        nav: n.nav,
        daily_return: n.daily_return,
      }))}
    />
  );
}
