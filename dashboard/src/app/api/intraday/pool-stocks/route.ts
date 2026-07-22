import { NextResponse } from "next/server";
import { fetchAPI } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  try {
    const [themes, screen] = await Promise.all([
      fetchAPI<any>("/v1/theme/stocks"),
      fetchAPI<any>("/v1/screen/latest?limit=20"),
    ]);

    const themeMap = new Map<string, any[]>();
    if (Array.isArray(themes)) {
      for (const t of themes) {
        for (const s of (t.stocks || [])) {
          if (!themeMap.has(t.theme)) themeMap.set(t.theme, []);
          themeMap.get(t.theme)!.push(s);
        }
      }
    }

    return NextResponse.json({
      themes: Array.from(themeMap.entries()).map(([theme, stocks]) => ({ theme, stocks })),
      quantStocks: Array.isArray(screen) ? screen : [],
    });
  } catch {
    return NextResponse.json({ themes: [], quantStocks: [] });
  }
}
