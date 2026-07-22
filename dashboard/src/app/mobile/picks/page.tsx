import { getActiveRecommendations, getRecommendationHistory, getStrategyPerformance } from "@/lib/db";
import { headers } from "next/headers";
import { isSuperAdmin } from "@/lib/auth";
import { PicksClient } from "./PicksClient";
import type { Recommendation, StrategyPerf } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 300;

function safeNumber(v: any): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function safeRec(recs: any[]): Recommendation[] {
  return recs.map((r) => ({
    ...r,
    entry_avg_price: safeNumber(r.entry_avg_price),
    pnl: safeNumber(r.pnl),
    pnl_pct: safeNumber(r.pnl_pct),
    score: safeNumber(r.score),
    total_shares: safeNumber(r.total_shares),
    total_amount: safeNumber(r.total_amount),
  }));
}

function safePerf(rows: any[]): StrategyPerf[] {
  return rows.map((r) => ({
    market: String(r.market || ""),
    strategy_type: String(r.strategy_type || ""),
    total_trades: Number(r.total_trades) || 0,
    wins: Number(r.wins) || 0,
    avg_return: Number(r.avg_return) || 0,
    avg_win: Number(r.avg_win) || 0,
    avg_loss: Number(r.avg_loss) || 0,
  }));
}

export default async function PicksPage() {
  const h = await headers();
  const role = h.get("X-User-Role") || "guest";
  if (!isSuperAdmin(role)) {
    return <div className="p-8 text-center text-zinc-500">无权访问</div>;
  }

  let aLong: Recommendation[] = [];
  let aShort: Recommendation[] = [];
  let hkLong: Recommendation[] = [];
  let hkShort: Recommendation[] = [];
  let history: Recommendation[] = [];
  let perf: StrategyPerf[] = [];

  try { aLong = safeRec(getActiveRecommendations("A", "long_term")); } catch {}
  try { aShort = safeRec(getActiveRecommendations("A", "short_term")); } catch {}
  try { hkLong = safeRec(getActiveRecommendations("HK", "long_term")); } catch {}
  try { hkShort = safeRec(getActiveRecommendations("HK", "short_term")); } catch {}
  try { history = safeRec(getRecommendationHistory(undefined, undefined, 50)); } catch {}
  try { perf = safePerf(getStrategyPerformance()); } catch {}

  return (
    <PicksClient
      aLong={aLong}
      aShort={aShort}
      hkLong={hkLong}
      hkShort={hkShort}
      history={history}
      perf={perf}
    />
  );
}
