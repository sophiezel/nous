import { NextResponse } from "next/server";
import { getScreenerDb } from "@/lib/db";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  try {
    const db = getScreenerDb();

    // 今日推荐
    const picks = db.prepare(`
      SELECT symbol, name, market, cycle, score, pe, rsi, volume_ratio,
             position_suggested, expected_return, rec_date
      FROM recommendation_pool 
      WHERE rec_date = (SELECT MAX(rec_date) FROM recommendation_pool)
      ORDER BY market, cycle, score DESC
    `).all();

    // Pipeline最新心跳
    const heartbeat = db.prepare(`
      SELECT check_time, status, detail 
      FROM pipeline_heartbeat 
      WHERE check_type='pipeline_recommend' 
      ORDER BY check_time DESC LIMIT 1
    `).get();

    // 按池分组统计
    const poolStats = db.prepare(`
      SELECT market, cycle, COUNT(*) as cnt
      FROM recommendation_pool 
      WHERE rec_date = (SELECT MAX(rec_date) FROM recommendation_pool)
      GROUP BY market, cycle
    `).all();

    // ml_scores最新预测
    const mlScores = db.prepare(`
      SELECT symbol, pool_type, model_score_norm, trade_date
      FROM ml_scores 
      WHERE trade_date = (SELECT MAX(trade_date) FROM ml_scores)
      ORDER BY model_score_norm DESC LIMIT 20
    `).all();

    return NextResponse.json({
      ok: true,
      date: (picks as any[])[0]?.rec_date || null,
      totalPicks: (picks as any[]).length,
      poolStats,
      picks,
      mlScores,
      heartbeat: heartbeat ? {
        time: (heartbeat as any).check_time,
        status: (heartbeat as any).status,
        detail: (heartbeat as any).detail,
      } : null,
    });
  } catch (e: any) {
    return NextResponse.json({
      ok: false,
      error: e.message || "Unknown error",
    }, { status: 500 });
  }
}
