import { NextResponse } from "next/server";
import { getScreenerDb } from "@/lib/db";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const db = getScreenerDb();

  // 两融做空 — latest 2 rows for daily change calc
  const marginShort = db.prepare(
    "SELECT trade_date, margin_balance, margin_buy, short_balance, short_volume, total_balance FROM margin_short_daily ORDER BY trade_date DESC LIMIT 2"
  ).all() as any[];

  // 北向 & 南向
  const hsgt = db.prepare(
    "SELECT trade_date, direction, net_buy, buy_amount, sell_amount FROM hsgt_daily ORDER BY trade_date DESC LIMIT 30"
  ).all() as any[];

  // 龙虎榜最新
  const lhbLatest = db.prepare(
    "SELECT * FROM lhb_daily WHERE trade_date = (SELECT MAX(trade_date) FROM lhb_daily) ORDER BY net_amount DESC LIMIT 10"
  ).all() as any[];

  // KWEB / CWEB 中概
  const kw = db.prepare(
    "SELECT trade_date, close FROM index_global_daily WHERE symbol = 'KWEB' ORDER BY trade_date DESC LIMIT 30"
  ).all() as any[];

  const cw = db.prepare(
    "SELECT trade_date, close FROM index_global_daily WHERE symbol = 'CWEB' ORDER BY trade_date DESC LIMIT 30"
  ).all() as any[];

  // 最新交易日
  const lastTradeDate = db.prepare(
    "SELECT MAX(trade_date) as d FROM stock_daily_all"
  ).get() as any;

  return NextResponse.json({
    tradeDate: lastTradeDate?.d || null,
    marginShort,
    hsgt,
    lhb: lhbLatest,
    kweb: kw,
    cweb: cw,
  });
}
