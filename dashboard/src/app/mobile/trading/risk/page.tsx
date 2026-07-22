import { getRiskOverview, getRiskEvents } from "@/lib/db";
import { RiskClient } from "./RiskClient";
import type { RiskOverview, RiskEvent } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 30;

/** Parse DB row to safe RiskOverview — all numeric fields guarded */
function safeOverview(raw: RiskOverview | null): RiskOverview | null {
  if (!raw) return null;
  return {
    total_drawdown_pct: Number(raw.total_drawdown_pct) || 0,
    current_drawdown_pct: Number(raw.current_drawdown_pct) || 0,
    var_95: Number(raw.var_95) || 0,
    volatility: Number(raw.volatility) || 0,
    sharpe: Number(raw.sharpe) || 0,
    max_consecutive_losses: Number(raw.max_consecutive_losses) || 0,
    update_time: String(raw.update_time || ""),
  };
}

function safeEvents(raw: RiskEvent[]): RiskEvent[] {
  if (!raw || !Array.isArray(raw)) return [];
  return raw.map(evt => {
    const safe: RiskEvent = {
      id: evt.id ?? 0,
      date: String(evt.date ?? ""),
      type: String(evt.type ?? ""),
      symbol: String(evt.symbol ?? ""),
      message: String(evt.message ?? ""),
      severity: String(evt.severity ?? "info"),
      metric_value: Number(evt.metric_value) || 0,
      threshold: Number(evt.threshold) || 0,
      acknowledged: Number(evt.acknowledged ?? 0),
    };
    return safe;
  });
}

export default function RiskPage() {
  let overview: RiskOverview | null = null;
  let events: RiskEvent[] = [];

  try {
    overview = safeOverview(getRiskOverview());
  } catch (e) {
    console.error("[risk] getRiskOverview failed:", (e as Error).message);
  }

  try {
    events = safeEvents(getRiskEvents(20));
  } catch (e) {
    console.error("[risk] getRiskEvents failed:", (e as Error).message);
  }

  return (
    <RiskClient overview={overview} events={events} />
  );
}
