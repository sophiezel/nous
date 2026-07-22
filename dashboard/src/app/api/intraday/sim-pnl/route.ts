import { NextRequest, NextResponse } from "next/server";
import { fetchAPI } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const symbol = req.nextUrl.searchParams.get("symbol");
  if (!symbol) return NextResponse.json({ error: "missing symbol" }, { status: 400 });

  try {
    const [recHistory, daily] = await Promise.all([
      fetchAPI<any>(`/v1/rec/performance?symbol=${symbol}`),
      fetchAPI<any>(`/v1/stock/daily?symbol=${symbol}&limit=5`),
    ]);

    const periods = (Array.isArray(daily) ? daily.slice(0, 3) : []).map((d: any) => ({
      date: d.trade_date,
      open: d.open, high: d.high, low: d.low, close: d.close,
      rangePct: d.high && d.low ? ((d.high - d.low) / d.low) * 100 : 0,
      openPnl: d.open && d.close ? ((d.close - d.open) / d.open) * 100 : 0,
    }));

    return NextResponse.json({
      recHistory: Array.isArray(recHistory) ? recHistory : [],
      periods,
    });
  } catch {
    return NextResponse.json({ recHistory: [], periods: [] });
  }
}
