import { NextResponse } from "next/server";
import { fetchAPI } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const indices = ["IDX_000001", "IDX_399001", "IDX_000688", "IDX_HSI"] as const;
  const labels: Record<string, string> = {
    IDX_000001: "上证指数", IDX_399001: "深证成指",
    IDX_000688: "科创50", IDX_HSI: "恒生指数",
  };

  const result: any[] = [];
  for (const code of indices) {
    try {
      const data = await fetchAPI<any>(`/v1/index/daily?codes=${code}&days=30`);
      const rows = data?.[code] || [];
      if (rows.length > 0) {
        const latest = rows[0];
        const prev = rows[1] || latest;
        const changePct = prev.close ? ((latest.close - prev.close) / prev.close) * 100 : 0;
        result.push({ symbol: code, label: labels[code] || code, ...latest, changePct });
      }
    } catch {}
  }
  return NextResponse.json(result);
}
