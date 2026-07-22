import { NextResponse } from "next/server";
import { getScreenerDb } from "@/lib/db";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const db = getScreenerDb();

  // 推荐股盈亏对比
  const recs = db.prepare(
    "SELECT symbol, name, rec_score, return_1d, return_5d, return_20d, last_return FROM rec_performance WHERE last_return IS NOT NULL ORDER BY rec_date DESC LIMIT 20"
  ).all() as any[];

  // 推荐池整体统计
  const poolStats = db.prepare(
    "SELECT theme, COUNT(*) as count, AVG(change_pct) as avg_change, MAX(change_pct) as best, MIN(change_pct) as worst FROM theme_pool_stocks GROUP BY theme"
  ).all() as any[];

  // 剔除池 (这里用screen_results低分作为模拟的"剔除池")
  const eliminated = db.prepare(
    "SELECT symbol, name, score, pe, rsi FROM screen_results WHERE screen_date = (SELECT MAX(screen_date) FROM screen_results) AND score < 6 ORDER BY score ASC LIMIT 10"
  ).all() as any[];

  // 量化盘交易日志 (从 reports 表取 trader_daily 类型)
  const tradeLogs = db.prepare(
    "SELECT id, type, title, created_at, substr(content, 1, 50) as preview FROM reports WHERE type IN ('trader_daily', 'daily_picks') ORDER BY created_at DESC LIMIT 10"
  ).all() as any[];

  return NextResponse.json({
    recs,
    poolStats,
    eliminated,
    tradeLogs,
  });
}
