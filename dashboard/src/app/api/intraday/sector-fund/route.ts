import { NextResponse } from "next/server";
import { fetchAPI } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  try {
    const [themeData, screen] = await Promise.all([
      fetchAPI<any>("/v1/theme/stocks"),
      fetchAPI<any>("/v1/screen/latest?limit=50"),
    ]);

    const themeAgg: Record<string, { theme: string; count: number; avgChange: number; stocks: any[] }> = {};

    if (Array.isArray(themeData)) {
      for (const t of themeData) {
        for (const s of (t.stocks || [])) {
          if (!themeAgg[s.theme]) themeAgg[s.theme] = { theme: s.theme, count: 0, avgChange: 0, stocks: [] };
          themeAgg[s.theme].count++;
          themeAgg[s.theme].avgChange += s.change_pct || 0;
          themeAgg[s.theme].stocks.push(s);
        }
      }
    }

    return NextResponse.json({
      sectors: Object.values(themeAgg).map(g => ({
        ...g, avgChange: +(g.avgChange / g.count).toFixed(2),
      })),
    });
  } catch {
    return NextResponse.json({ sectors: [] });
  }
}
