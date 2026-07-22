import { fetchAPI } from "@/lib/api";
import { PagePoller } from "@/components/PagePoller";
import { MobileDashboardClient } from "./MobileDashboardClient";
import { safeJsonParse } from "@/lib/utils";
import type { ThemePool } from "@/lib/types";
import * as crypto from "crypto";

// ── Quant signal decryption (pure, no better-sqlite3) ──
const ENC_KEY_B64 = process.env.TIANGONG_QUANT_ENC_KEY || "";
const ENC_KEY = ENC_KEY_B64 ? Buffer.from(ENC_KEY_B64, "base64") : null;

function decryptQuantSignal(row: any): any | null {
  if (!row) return null;
  const r = { ...row };
  for (const k of Object.keys(r)) {
    if (k.endsWith("_enc") && typeof r[k] === "string" && ENC_KEY) {
      const plainKey = k.replace("_enc", "");
      try {
        const raw = Buffer.from(r[k], "base64");
        const nonce = raw.subarray(0, 12);
        const ct = raw.subarray(12);
        const decipher = crypto.createDecipheriv("aes-256-gcm", ENC_KEY, nonce);
        decipher.setAuthTag(ct.subarray(ct.length - 16));
        const pt = Buffer.concat([decipher.update(ct.subarray(0, ct.length - 16)), decipher.final()]);
        const s = pt.toString("utf8");
        if (["ic", "confidence", "strength", "importance", "total_stocks", "buy_signals", "sell_signals"].some(t => plainKey.endsWith(t) || plainKey === t)) {
          r[plainKey] = parseFloat(s) || 0;
        } else {
          r[plainKey] = s;
        }
      } catch { r[plainKey] = ""; }
      delete r[k];
    }
  }
  return r;
}

export const dynamic = "force-dynamic";
export const revalidate = 60;

function jp<T = any>(raw: unknown): T | null {
  if (typeof raw !== "string") return null;
  return safeJsonParse(raw) as T | null;
}

async function indexPct(code: string, isGlobal: boolean): Promise<{ pct: number | null }> {
  let raw: Record<string, { trade_date: string; close: number }[]>;
  if (isGlobal) {
    raw = await fetchAPI(`/v1/global/index?symbols=${encodeURIComponent(code)}`);
  } else {
    raw = await fetchAPI(`/v1/index/daily?codes=${encodeURIComponent(code)}&days=2`);
  }
  const hist = raw[code] || [];
  const latest = hist[0];
  const prev = hist[1];
  const pct = prev?.close && latest?.close
    ? ((latest.close - prev.close) / prev.close * 100)
    : null;
  return { pct };
}

// ── Decision engine ──────────────────────────────

interface Decision {
  regime: "进攻" | "防御" | "观望";
  regimeEmoji: string;
  positionPct: number;
  reason: string;
  focusThemes: string[];
  subSignals: string[]; // compact signal summary line
}

function computeDecision(
  macroScore: number | null,
  macroPosition: number | null,
  sentimentScore: number | null,
  sentimentAlert: string | null,
  sentDeltaWeek: number | null,
  sentPercentile: number | null,
  vix: number | null,
  marginTrend: number | null,
  leverageRate: number | null,
  northDeltaWeek: number | null,
  southDeltaWeek: number | null,
  themePool: ThemePool[],
  themeFlowMap: Record<string, number>,
): Decision {
  const fallback: Decision = {
    regime: "观望", regimeEmoji: "⚪",
    positionPct: 30, reason: "数据不足，保持观望",
    focusThemes: [], subSignals: [],
  };
  if (macroScore == null && sentimentScore == null) return fallback;

  // ── Core factors (0-1) ──
  const macroFactor = macroScore != null ? Math.min(1, macroScore / 100) : 0.5;
  const sentFactor = sentimentScore != null ? Math.min(1, sentimentScore / 100) : 0.5;
  const vixFactor = vix != null
    ? vix > 30 ? 0.1 : vix > 25 ? 0.4 : vix > 20 ? 0.7 : 1.0 : 0.5;
  const marginFactor = marginTrend != null
    ? marginTrend > 3 ? 1.2 : marginTrend > 0 ? 1.0 : marginTrend > -3 ? 0.7 : 0.4 : 0.8;

  const coreComposite = macroFactor * 0.25 + sentFactor * 0.25 + vixFactor * 0.15 + marginFactor * 0.15;

  // ── New: Fund flow factor ──
  let flowFactor = 1.0;
  if (northDeltaWeek != null) {
    if (northDeltaWeek > 10) flowFactor = 1.2;        // 北向加速流入 → bullish
    else if (northDeltaWeek > 0) flowFactor = 1.05;
    else if (northDeltaWeek < -10) flowFactor = 0.6;   // 北向加速流出 → bearish
    else if (northDeltaWeek < 0) flowFactor = 0.85;
  }

  // ── New: Sentiment momentum factor ──
  let momentumFactor = 1.0;
  if (sentDeltaWeek != null) {
    if (sentDeltaWeek > 8) momentumFactor = 1.15;       // sentiment improving fast
    else if (sentDeltaWeek < -8) momentumFactor = 0.75;  // sentiment collapsing
  }

  // ── New: Leverage risk penalty ──
  let leveragePenalty = 1.0;
  if (leverageRate != null) {
    if (leverageRate > 15) leveragePenalty = 0.6;   // extreme leverage → forced risk reduction
    else if (leverageRate > 12) leveragePenalty = 0.8;
    else if (leverageRate < 6) leveragePenalty = 0.9; // too cold
  }

  // ── New: Theme breadth factor ──
  let breadthFactor = 1.0;
  if (themePool.length > 0) {
    const upThemes = themePool.filter(t => {
      const avg = t.stocks.reduce((s,x) => s + x.change_pct, 0) / t.stocks.length;
      return avg > 0;
    }).length;
    const broadPct = upThemes / themePool.length;
    if (broadPct >= 0.8) breadthFactor = 1.1;      // broad rally
    else if (broadPct < 0.3) breadthFactor = 0.8;   // narrow divergence
  }

  // ── New: Theme fund flow bias ──
  const themeFlowValues = Object.values(themeFlowMap);
  let themeFlowBias = 1.0;
  if (themeFlowValues.length >= 3) {
    const positiveThemes = themeFlowValues.filter(v => v > 0).length;
    if (positiveThemes === 0) themeFlowBias = 0.75;   // all themes losing money
    else if (positiveThemes === themeFlowValues.length) themeFlowBias = 1.1;
  }

  // ── Composite ──
  const composite = coreComposite * (0.35 + flowFactor * 0.2 + momentumFactor * 0.15 + leveragePenalty * 0.1 + breadthFactor * 0.1 + themeFlowBias * 0.1);

  // ── Position ──
  let regime: "进攻" | "防御" | "观望";
  let regimeEmoji: string;
  let positionPct: number;

  if (composite >= 0.7) {
    regime = "进攻"; regimeEmoji = "🟢";
    positionPct = Math.round(60 + (composite - 0.7) / 0.3 * 30);
  } else if (composite >= 0.45) {
    regime = "防御"; regimeEmoji = "🟡";
    positionPct = Math.round(30 + (composite - 0.45) / 0.25 * 30);
  } else {
    regime = "观望"; regimeEmoji = "🔴";
    positionPct = Math.max(0, Math.round(composite * 50));
  }

  if (macroPosition != null) positionPct = Math.min(positionPct, Math.round(macroPosition * 100));

  // ── Reason string ──
  const parts: string[] = [];
  if (macroScore != null) parts.push(macroScore >= 70 ? "宏观扩张" : macroScore >= 50 ? "宏观中性" : "宏观收缩");
  if (sentimentScore != null) parts.push(sentimentScore >= 60 ? "情绪乐观" : sentimentScore >= 40 ? "情绪中性" : "情绪悲观");
  if (vix != null) parts.push(vix > 25 ? `VIX高` : vix > 20 ? `VIX偏高` : "");
  if (northDeltaWeek != null && northDeltaWeek > 5) parts.push("北向流入");
  if (northDeltaWeek != null && northDeltaWeek < -5) parts.push("北向流出");
  if (sentDeltaWeek != null && Math.abs(sentDeltaWeek) >= 5) parts.push(sentDeltaWeek > 0 ? "情绪↑" : "情绪↓");

  // ── Sub-signals (compact warnings) ──
  const subSignals: string[] = [];
  if (sentimentAlert) subSignals.push(sentimentAlert);
  if (leverageRate != null && leverageRate > 12) subSignals.push(`杠杆${leverageRate.toFixed(0)}%⚠️`);
  if (marginTrend != null && marginTrend < -2) subSignals.push("融资收缩");
  if (northDeltaWeek != null && northDeltaWeek < 0 && southDeltaWeek != null && southDeltaWeek > 0) subSignals.push("北出南进");

  const focusThemes = themePool.length > 0
    ? themePool.slice(0, 2).map(t => t.theme.length > 6 ? t.theme.slice(0, 6) : t.theme)
    : [];

  return { regime, regimeEmoji, positionPct, reason: parts.join(" · ") || "信号混合", focusThemes, subSignals };
}

// ── Main page ────────────────────────────────────

export default async function MobileDashboardPage() {
  // ── Fetch all data in parallel ──
  const [
    macroRaw,
    macroScoresRaw,
    sentimentRaw,
    sentimentHistoryRaw,
    vixRaw,
    marginResp,
    lhbRaw,
    southFlowRaw,
    blockTradesRaw,
    northStocksRaw,
    southStocksRaw,
    northSectorsRaw,
    etfFlowsRaw,
    futuresResp,
    themePoolRaw,
  ] = await Promise.all([
    fetchAPI<Record<string, any>>("/v1/macro/score").catch(() => ({})),
    fetchAPI<any[]>("/v1/macro/scores?limit=60").catch(() => []),
    fetchAPI<Record<string, any>>("/v1/sentiment/latest").catch(() => ({})),
    fetchAPI<any[]>("/v1/sentiment/history?days=60").catch(() => []),
    fetchAPI<Record<string, any>>("/v1/global/latest?symbol=VIX").catch(() => ({})),
    fetchAPI<{ margin_daily: any[]; margin_short: any[] }>("/v1/flow/margin?days=30").catch(() => ({ margin_daily: [], margin_short: [] })),
    fetchAPI<any[]>("/v1/flow/lhb?limit=10").catch(() => []),
    fetchAPI<any[]>("/v1/flow/hsgt?dir=south&days=30").catch(() => []),
    fetchAPI<any[]>("/v1/flow/block?limit=10").catch(() => []),
    fetchAPI<any[]>("/v1/flow/hsgt/stocks?dir=%E5%8C%97%E5%90%91&limit=10").catch(() => []),
    fetchAPI<any[]>("/v1/flow/hsgt/stocks?dir=%E5%8D%97%E5%90%91&limit=10").catch(() => []),
    fetchAPI<any[]>("/v1/flow/hsgt/sectors?dir=%E5%8C%97%E5%90%91&limit=5").catch(() => []),
    fetchAPI<any[]>("/v1/flow/etf?limit=6").catch(() => []),
    fetchAPI<Record<string, any>>("/v1/futures?codes=CU0,AU0,SC0").catch(() => ({} as Record<string, any>)),
    fetchAPI<any[]>("/v1/theme/stocks").catch(() => []),
  ]);

  // ── Hsgt total trend for delta calculation ──
  const northTrendRaw = await fetchAPI<any[]>("/v1/flow/hsgt?dir=north&days=10").catch(() => []);
  const southTrendExtra = await fetchAPI<any[]>("/v1/flow/hsgt?dir=south&days=10").catch(() => []);

  // Map northTrend/SouthTrend to { trade_date, total } shape
  const northTrend = northTrendRaw.map((r: any) => ({ trade_date: r.trade_date, total: r.net_buy }));
  const southTrend = southTrendExtra.map((r: any) => ({ trade_date: r.trade_date, total: r.net_buy }));

  // ── Theme fund flow aggregation ──
  const themePool = (themePoolRaw || []) as ThemePool[];
  const themeFlowMap = new Map<string, number>();
  if (themePool.length > 0) {
    const themeFlowPromises = themePool.map(theme =>
      fetchAPI<{ total: number | null }>(`/v1/theme/fund-flow?symbols=${theme.stocks.map(s => s.symbol).join(",")}`)
        .then(r => r.total)
        .catch(() => null)
    );
    const themeFlows = await Promise.all(themeFlowPromises);
    themePool.forEach((theme, i) => {
      if (themeFlows[i] != null) themeFlowMap.set(theme.theme, themeFlows[i]!);
    });
  }

  // ── Macro ──
  const macro: any = macroRaw && Object.keys(macroRaw).length > 0 ? macroRaw : null;
  const macroScores: any[] = macroScoresRaw;

  // ── Sentiment ──
  const sentiment: any = sentimentRaw && Object.keys(sentimentRaw).length > 0 ? sentimentRaw : null;
  const sentimentHistory: any[] = sentimentHistoryRaw;

  // VIX
  const vix: any = vixRaw && Object.keys(vixRaw).length > 0 ? vixRaw : null;

  // M2 details: 涨跌比 + 炸板率
  const sentDetails = sentiment?.details ? jp<Record<string, number>>(sentiment.details) : null;
  const advDeclRatio = sentDetails?.["涨跌比"] ?? null;
  const bustRate = sentDetails?.["炸板率"] ?? null;
  const limitUpRate = sentiment?.limit_up_rate ?? 0;
  const sentimentAlert = limitUpRate > 0.05 ? "过热反转预警"
    : limitUpRate < 0.002 && limitUpRate > 0 ? "冰点反转信号"
    : null;

  // ── M2 enhancements: delta + percentile + turnover ──
  const historyScores = [...sentimentHistory].reverse().map(s => s.score);
  const sentCurrent = sentiment?.score ?? null;
  const sentWeekAgo = historyScores.length > 5 ? historyScores[historyScores.length - 6] : null;
  const sentMonthAgo = historyScores.length > 22 ? historyScores[historyScores.length - 23] : null;
  const sentDeltaWeek = sentCurrent != null && sentWeekAgo != null ? sentCurrent - sentWeekAgo : null;
  const sentDeltaMonth = sentCurrent != null && sentMonthAgo != null ? sentCurrent - sentMonthAgo : null;
  const sentPercentile = sentCurrent != null && historyScores.length > 1
    ? Math.round(historyScores.filter(s => s < sentCurrent).length / historyScores.length * 100)
    : null;

  // ── Turnover (via Data Service) ──
  const turnoverRes = await fetchAPI<{total: number | null}>("/v1/market/turnover").catch(() => ({total: null}));
  const marketTurnover = turnoverRes?.total ?? null;

  // ── M3: Index PCT ──
  const [idx000001, idx399006, kwebIdx, idx000300, idx000905] = await Promise.all([
    indexPct("IDX_000001", false),
    indexPct("IDX_399006", false),
    indexPct("KWEB", true),
    indexPct("IDX_000300", false),
    indexPct("IDX_000905", false),
  ]);
  const domesticIndices = [
    { label: "上证指数", ...idx000001 },
    { label: "创业板", ...idx399006 },
    { label: "恒生科技", ...kwebIdx },
    { label: "沪深300", ...idx000300 },
    { label: "中证500", ...idx000905 },
  ];
  const globalIndices = [
    { label: "中概互联", ...kwebIdx },
  ];

  // ── M4 (merged: margin + south + LHB) ──
  const margin = marginResp.margin_daily || [];
  const marginShort = marginResp.margin_short || [];
  const lhb = lhbRaw || [];
  const southFlow = southFlowRaw || [];

  // Margin 7-day trend
  const marginTrend: number | null = (() => {
    if (margin.length < 7) return null;
    const recent = [...margin].reverse().slice(-7);
    const first = recent[0]?.margin_balance;
    const last = recent[recent.length - 1]?.margin_balance;
    return first && last ? ((last - first) / first * 100) : null;
  })();

  // Margin buy + leverage (skip NULL rows from incomplete trading day)
  const marginBuyHist = marginResp.margin_daily || [];
  const latestMarginBuy = marginBuyHist.find(r => r.margin_buy != null)?.margin_buy ?? null;
  const leverageRate = latestMarginBuy != null && marketTurnover != null
    ? (latestMarginBuy / marketTurnover * 100)
    : null;

  const blockTrades = blockTradesRaw || [];

  // North-bound stock TOP10
  const northStocks = northStocksRaw || [];
  const northTotalInflow = northStocks.reduce((sum: number, s: any) => sum + (s.net_inflow || 0), 0);
  // South-bound stock TOP10
  const southStocks = southStocksRaw || [];
  const southTotalInflow = southStocks.reduce((sum: number, s: any) => sum + (s.net_inflow || 0), 0);

  // ── stock display name (use DB name, fallback to code) ──
  const stockName = (code: string, name?: string | null) => name || code;

  // Sector aggregation
  const northSectors = northSectorsRaw || [];

  // ── North/South weekly delta ──
  const northDeltaWeek = northTrend.length > 5
    ? (northTrend[0].total - northTrend[5].total) / Math.abs(northTrend[5].total || 1) * 100 : null;
  const southDeltaWeek = southTrend.length > 5
    ? (southTrend[0].total - southTrend[5].total) / Math.abs(southTrend[5].total || 1) * 100 : null;

  // ETF flows
  const etfFlows = etfFlowsRaw || [];

  // ── M7: KWEB + futures ──
  const kwebPct = kwebIdx.pct;
  const fpct = (sym: string) => {
    const f = futuresResp?.[sym];
    return f?.prev?.close ? ((f.latest.close - f.prev.close) / f.prev.close * 100) : null;
  };
  const futures = [
    { label: "铜", pct: fpct("CU0"), close: null },
    { label: "金", pct: fpct("AU0"), close: null },
    { label: "原油", pct: fpct("SC0"), close: null },
  ];

  // ── Decision ──
  const quantRow = await fetchAPI<any>("/v1/quant/signals/latest").catch(() => null);
  const quantSignal = quantRow ? decryptQuantSignal(quantRow) : null;
  const quantParts: string[] = [];
  if (quantSignal) {
    if (quantSignal.prediction_bias) quantParts.push(`${quantSignal.prediction_bias}`);
    if (quantSignal.ensemble_ic) quantParts.push(`IC:${quantSignal.ensemble_ic.toFixed(2)}`);
    if (quantSignal.buy_signals || quantSignal.sell_signals) quantParts.push(`买${quantSignal.buy_signals}卖${quantSignal.sell_signals}`);
    if (quantSignal.top_factor) quantParts.push(`因子:${quantSignal.top_factor}`);
  }
  const quantBias = quantParts.length > 0 ? quantParts.join(" · ") : null;
  const decision = computeDecision(
    macro?.score ?? null,
    macro?.position ?? null,
    sentiment?.score ?? null,
    sentimentAlert,
    sentDeltaWeek,
    sentPercentile,
    vix?.close ?? null,
    marginTrend,
    leverageRate,
    northDeltaWeek,
    southDeltaWeek,
    themePool,
    Object.fromEntries(themeFlowMap),
  );

  // Reports (via Data Service API)
  const [latestPicks, latestBuy, latestReview] = await Promise.all([
    fetchAPI<any>("/v1/reports/latest?type=daily_picks").catch(() => null),
    fetchAPI<any>("/v1/reports/latest?type=buy_signals").catch(() => null),
    fetchAPI<any>("/v1/reports/latest?type=portfolio_review").catch(() => null),
  ]);

  // Serialize
  const macroParsed = macro ? jp<Record<string, number>>(macro.indicators) : null;
  const sentParsed = sentiment ? jp<Record<string, any>>(sentiment.details) : null;

  return (<>
    <MobileDashboardClient
      // Decision
      decision={decision}
      quantBias={quantBias}
      // M1
      macroScore={macro?.score ?? null}
      macroPosition={macro?.position ?? null}
      macroDate={macro?.date ?? null}
      macroIndicatorKeys={macroParsed ? Object.keys(macroParsed) : []}
      macroIndicatorValues={macroParsed ? Object.values(macroParsed).map(v => typeof v === "number" ? v : 0) : []}
      macroChartData={[...macroScores].reverse().map(s => s.score)}
      // M2
      sentimentScore={sentiment?.score ?? null}
      sentimentLimitUp={sentiment?.limit_up_count ?? 0}
      sentimentLimitUpRate={limitUpRate}
      sentimentAlert={sentimentAlert}
      advDeclRatio={advDeclRatio}
      bustRate={bustRate}
      sentimentChartData={[...sentimentHistory].reverse().map(s => s.score)}
      vixClose={vix?.close ?? null}
      sentDeltaWeek={sentDeltaWeek}
      sentDeltaMonth={sentDeltaMonth}
      sentPercentile={sentPercentile}
      marketTurnover={marketTurnover}
      // M3
      domesticIndices={domesticIndices}
      globalIndices={globalIndices}
      // M4 (merged: margin + south + LHB)
      marginBalance={margin.length > 0 ? margin[0].margin_balance : null}
      marginChartData={[...margin].reverse().map(r => r.margin_balance / 1e8)}
      shortBalance={marginShort.length > 0 ? marginShort[0].short_balance : null}
      marginTrend={marginTrend}
      southNetBuy={southFlow.length > 0 ? southFlow[0].net_buy : null}
      southChartData={[...southFlow].reverse().map(r => r.net_buy ?? 0)}
      lhbTop5={lhb.slice(0, 5).map(r => ({
        symbol: r.symbol, name: r.name,
        pct_change: r.pct_change, net_amount: r.net_amount,
        l_buy: r.l_buy, l_sell: r.l_sell, reason: r.reason,
      }))}
      latestMarginBuy={latestMarginBuy}
      leverageRate={leverageRate}
      blockTrades={blockTrades.map((b: any) => ({
        symbol: b.symbol, name: b.name,
        price: b.price, volume: b.volume, amount: b.amount, close: b.close,
      }))}
      // M5: 北向
      northStocks={northStocks.map((s: any) => ({
        symbol: stockName(s.symbol, s.name), rank: s.rank,
        net_inflow: s.net_inflow, change_pct: s.change_pct,
      }))}
      northTotalInflow={northTotalInflow}
      northDeltaWeek={northDeltaWeek}
      southStocks={southStocks.map((s: any) => ({
        symbol: stockName(s.symbol, s.name), rank: s.rank,
        net_inflow: s.net_inflow, change_pct: s.change_pct,
      }))}
      southTotalInflow={southTotalInflow}
      southDeltaWeek={southDeltaWeek}
      northSectors={northSectors.map((s: any) => ({
        sector: s.sector, total_net_buy: s.total_net_buy,
        buy_count: s.buy_count, sell_count: s.sell_count,
      }))}
      etfFlows={etfFlows.map((e: any) => ({
        symbol: e.symbol, name: e.name,
        etf_type: e.etf_type, pct_change: e.pct_change,
      }))}
      // M6
      kwebPct={kwebPct}
      futures={futures}
      // M8/M9
      themePool={themePool}
      themeFlowMap={Object.fromEntries(themeFlowMap)}
      // Reports
      latestPicks={latestPicks?.content ? {
        id: latestPicks.id, type: latestPicks.type,
        title: latestPicks.title || latestPicks.type,
        preview: latestPicks.content?.substring(0, 80)?.replace(/[#*`\n]/g, " ") || "",
        created_at: latestPicks.created_at,
      } : null}
      latestBuy={latestBuy?.content ? {
        id: latestBuy.id, type: latestBuy.type,
        title: latestBuy.title || latestBuy.type,
        preview: latestBuy.content?.substring(0, 80)?.replace(/[#*`\n]/g, " ") || "",
        created_at: latestBuy.created_at,
      } : null}
      latestReview={latestReview?.content ? {
        id: latestReview.id, type: latestReview.type,
        title: latestReview.title || latestReview.type,
        preview: latestReview.content?.substring(0, 80)?.replace(/[#*`\n]/g, " ") || "",
        created_at: latestReview.created_at,
      } : null}
    />
    <PagePoller pollKey="sentiment" />
  </>);
}
