// /api/recommendations/dual-track/route.ts — 双轨对比数据API
import { NextResponse } from "next/server";

const DATA_SERVICE = process.env.DATA_SERVICE_URL || "http://127.0.0.1:3099";
const AUTH_HEADER = {
  "x-api-key": process.env.DATA_SERVICE_KEY || "local-dev",
  "Content-Type": "application/json",
};

export async function GET() {
  try {
    const query = `
      -- F3海鹰: 最新推荐
      SELECT 'F3' as engine, rec_date as date, symbol, name, cycle as pool, score, NULL as theme
      FROM recommendation_pool 
      WHERE rec_date = (SELECT MAX(rec_date) FROM recommendation_pool)
      ORDER BY score DESC
    `;
    
    const query2 = `
      -- TRL龙脉: 最新推荐
      SELECT 'TRL' as engine, recommend_date as date, symbol, theme_name as theme, pool, tier, score, theme_name as name
      FROM leader_history
      WHERE recommend_date = (SELECT MAX(recommend_date) FROM leader_history)
      ORDER BY score DESC
    `;

    const [f3Res, trlRes] = await Promise.all([
      fetch(`${DATA_SERVICE}/query`, { method: "POST", headers: AUTH_HEADER, body: JSON.stringify({ sql: query }) }),
      fetch(`${DATA_SERVICE}/query`, { method: "POST", headers: AUTH_HEADER, body: JSON.stringify({ sql: query2 }) }),
    ]);

    const f3Data = f3Res.ok ? await f3Res.json() : { rows: [] };
    const trlData = trlRes.ok ? await trlRes.json() : { rows: [] };

    const f3Rows = f3Data.rows || f3Data.data || [];
    const trlRows = trlData.rows || trlData.data || [];

    // 共识检测
    const f3Symbols = new Set((f3Rows as any[]).map(r => r.symbol));
    const consensus = (trlRows as any[]).filter(r => f3Symbols.has(r.symbol)).map(r => r.symbol);

    // 分池统计
    const pools: Record<string, { f3: number; trl: number }> = {};
    (f3Rows as any[]).forEach(r => { 
      const key = r.pool || "unknown"; 
      pools[key] = pools[key] || { f3: 0, trl: 0 }; 
      pools[key].f3++; 
    });
    (trlRows as any[]).forEach(r => { 
      const key = r.pool || "unknown"; 
      pools[key] = pools[key] || { f3: 0, trl: 0 }; 
      pools[key].trl++; 
    });

    return NextResponse.json({
      date: new Date().toISOString().split("T")[0],
      f3: { total: f3Rows.length, rows: f3Rows },
      trl: { total: trlRows.length, rows: trlRows },
      consensus: { count: consensus.length, symbols: consensus },
      pools,
    });
  } catch (e: any) {
    return NextResponse.json({ error: e.message || "Data Service unreachable" }, { status: 503 });
  }
}
