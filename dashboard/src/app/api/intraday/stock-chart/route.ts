import { NextRequest, NextResponse } from "next/server";
import { fetchAPI } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const symbol = req.nextUrl.searchParams.get("symbol");
  if (!symbol) return NextResponse.json({ error: "missing symbol" }, { status: 400 });

  try {
    const [daily, fundamental, fundFlow] = await Promise.all([
      fetchAPI<any>(`/v1/stock/daily?symbol=${symbol}&limit=60`),
      fetchAPI<any>(`/v1/stock/fundamental?symbol=${symbol}`),
      fetchAPI<any>(`/v1/stock/fund-flow?symbol=${symbol}&limit=5`),
    ]);

    return NextResponse.json({
      daily: Array.isArray(daily) ? daily : [],
      fundamental: fundamental || null,
      fundFlow: Array.isArray(fundFlow) ? fundFlow : [],
    });
  } catch {
    return NextResponse.json({ daily: [], fundamental: null, fundFlow: [] });
  }
}
