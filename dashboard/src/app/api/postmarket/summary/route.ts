import { NextResponse } from "next/server";
import { getScreenerDb } from "@/lib/db";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const db = getScreenerDb();

  // 推荐股整体统计
  const recSummary = db.prepare(
    "SELECT COUNT(*) as total, AVG(last_return) as avgReturn, SUM(CASE WHEN last_return > 0 THEN 1 ELSE 0 END) as wins, SUM(CASE WHEN last_return < 0 THEN 1 ELSE 0 END) as losses FROM rec_performance WHERE last_return IS NOT NULL"
  ).get() as any;

  // 组合净值走势
  const nav = db.prepare(
    "SELECT trade_date, nav, daily_return, pnl_pct FROM portfolio_nav ORDER BY trade_date DESC LIMIT 30"
  ).all() as any[];

  // 最新宏观评分
  const macro = db.prepare(
    "SELECT date, score FROM macro_scores ORDER BY date DESC LIMIT 1"
  ).get() as any;

  // 北向南向资金汇总
  const hsgtLatest = db.prepare(
    "SELECT trade_date, direction, SUM(net_buy) as total_net FROM hsgt_daily WHERE trade_date = (SELECT MAX(trade_date) FROM hsgt_daily) GROUP BY direction"
  ).all() as any[];

  // 两融汇总
  const marginLatest = db.prepare(
    "SELECT trade_date, margin_balance, short_balance, total_balance FROM margin_short_daily ORDER BY trade_date DESC LIMIT 1"
  ).get() as any;

  // 龙虎榜汇总
  const lhbCount = db.prepare(
    "SELECT COUNT(*) as count, SUM(net_amount) as totalNet FROM lhb_daily WHERE trade_date = (SELECT MAX(trade_date) FROM lhb_daily)"
  ).get() as any;

  // 最新报告 (AI总结用)
  const latestReport = db.prepare(
    "SELECT id, type, title, created_at FROM reports ORDER BY created_at DESC LIMIT 1"
  ).get() as any;

  return NextResponse.json({
    recSummary: {
      totalRecs: recSummary?.total || 0,
      avgReturn: recSummary?.avgReturn || 0,
      winRate: recSummary?.total > 0 ? +((recSummary.wins / recSummary.total) * 100).toFixed(1) : 0,
      wins: recSummary?.wins || 0,
      losses: recSummary?.losses || 0,
    },
    nav: nav.reverse(),
    macroScore: macro || null,
    hsgt: hsgtLatest,
    marginShort: marginLatest || null,
    lhb: lhbCount || null,
    latestReport: latestReport || null,
  });
}
