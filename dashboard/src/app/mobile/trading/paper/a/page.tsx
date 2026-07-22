import {
  getPaperPortfolio,
  getPaperNav,
  getPaperTrades,
} from "@/lib/db";
import { PaperDetailClient } from "./PaperDetailClient";

export const dynamic = "force-dynamic";
export const revalidate = 15;

export default function PaperADetailPage() {
  const positions = getPaperPortfolio("a") as any[];
  const nav = getPaperNav("a", 60);
  const trades = getPaperTrades("a", 30);

  const totalPnl = positions.reduce((s, p) => s + (p.pnl || 0), 0);
  const totalVal = positions.reduce((s, p) => s + (p.market_value || 0), 0);
  const totalPnlPct = totalVal > 0 ? (totalPnl / (totalVal - totalPnl)) * 100 : 0;

  return (
    <PaperDetailClient
      market="A股"
      positions={positions.map(p => ({
        symbol: p.symbol,
        name: p.name,
        weight_pct: p.weight_pct,
        pnl_pct: p.pnl_pct,
        market_value: p.market_value,
        shares: p.shares,
        cost_price: p.cost_price,
        current_price: p.current_price,
        pnl: p.pnl,
      }))}
      navData={nav.map(n => ({
        trade_date: n.trade_date,
        nav: n.nav,
        daily_return: n.daily_return,
        total_pnl: n.total_pnl,
        total_invested: n.total_invested,
      }))}
      trades={trades.map(t => ({
        trade_date: t.trade_date,
        symbol: t.symbol,
        name: t.name,
        direction: t.direction,
        price: t.price,
        shares: t.shares,
        amount: t.amount,
        pnl: t.pnl,
      }))}
      totalPnl={totalPnl}
      totalPnlPct={totalPnlPct}
    />
  );
}
