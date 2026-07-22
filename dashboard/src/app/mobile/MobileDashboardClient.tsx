"use client";

import { ModuleCard } from "@/components/mobile/module-card";
import { SparklineChart } from "@/components/mobile/sparkline";
import { BarChart } from "@/components/mobile/bar-chart";
import {
  TrendingUp, Activity, Globe, DollarSign,
  Ship, Waves, Target, ArrowUpDown, Info, Brain, BarChart3,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { ThemePool } from "@/lib/types";

interface ReportSummary {
  id: number; type: string; title: string; preview: string; created_at: string;
}

interface IndexSnapshot { label: string; pct: number | null; }
interface LhbStock { symbol: string; name: string; pct_change: number; net_amount: number; }
interface FuturesSnapshot { label: string; pct: number | null; close: number | null; }
interface NorthStock { symbol: string; rank: number; net_inflow: number; change_pct: number; }

interface Decision {
  regime: string; regimeEmoji: string;
  positionPct: number;
  reason: string;
  focusThemes: string[];
  subSignals: string[];
}

interface Props {
  // Decision
  decision: Decision;
  quantBias: string | null;
  // M1
  macroScore: number | null;
  macroPosition: number | null;
  macroDate: string | null;
  macroIndicatorKeys: string[];
  macroIndicatorValues: number[];
  macroChartData: number[];
  // M2
  sentimentScore: number | null;
  sentimentLimitUp: number;
  sentimentLimitUpRate: number;
  sentimentAlert: string | null;
  advDeclRatio: number | null;
  bustRate: number | null;
  sentimentChartData: number[];
  vixClose: number | null;
  // M2 enhancements
  sentDeltaWeek: number | null;
  sentDeltaMonth: number | null;
  sentPercentile: number | null;
  marketTurnover: number | null;
  // M3
  domesticIndices: IndexSnapshot[];
  globalIndices: IndexSnapshot[];
  // M4: 资金全景
  marginBalance: number | null;
  marginChartData: number[];
  shortBalance: number | null;
  marginTrend: number | null;
  southNetBuy: number | null;
  southChartData: number[];
  lhbTop5: LhbStock[];
  latestMarginBuy: number | null;
  leverageRate: number | null;
  blockTrades: { symbol: string; name: string; price: number; volume: number; amount: number }[];
  // M5: 北向资金 (restored)
  northStocks: NorthStock[];
  northTotalInflow: number;
  northDeltaWeek: number | null;
  // South stocks
  southStocks: NorthStock[];
  southTotalInflow: number;
  southDeltaWeek: number | null;
  // Sector aggregation
  northSectors: { sector: string; total_net_buy: number; buy_count: number; sell_count: number }[];
  // ETF flows
  etfFlows: { symbol: string; name: string; etf_type: string; pct_change: number }[];
  // M6: 外围市场
  kwebPct: number | null;
  futures: FuturesSnapshot[];
  // M7/M8: 主力/潜力主线
  themePool: ThemePool[];
  themeFlowMap: Record<string, number>;
  // Reports
  latestPicks: ReportSummary | null;
  latestBuy: ReportSummary | null;
  latestReview: ReportSummary | null;
}

function formatBillions(n: number | null): string {
  if (n == null) return "--";
  const b = n / 1e8;
  return `${b >= 0 ? "+" : ""}${b.toFixed(1)}亿`;
}

function pctText(n: number | null): string {
  if (n == null) return "--";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}
export function MobileDashboardClient(props: Props) {
  const { decision, quantBias,
    macroScore, macroPosition,
    macroIndicatorKeys, macroIndicatorValues, macroChartData,
    sentimentScore, sentimentLimitUp, sentimentLimitUpRate,
    sentimentAlert, advDeclRatio, bustRate,
    sentimentChartData, vixClose,
    sentDeltaWeek, sentDeltaMonth, sentPercentile, marketTurnover,
    domesticIndices, globalIndices,
    marginBalance, marginChartData,
    shortBalance, marginTrend,
    southNetBuy, southChartData, lhbTop5,
    latestMarginBuy, leverageRate, blockTrades,
    northStocks, northTotalInflow, northDeltaWeek,
    southStocks, southTotalInflow, southDeltaWeek,
    northSectors, etfFlows,
    kwebPct, futures,
    themePool,
    themeFlowMap,
    latestPicks, latestBuy, latestReview,
  } = props;

  const sentimentColor =
    sentimentScore !== null && sentimentScore >= 60 ? "#10b981"
    : sentimentScore !== null && sentimentScore >= 40 ? "#f59e0b"
    : "#ef4444";

  const macroMetricSub = macroPosition !== null
    ? `/100 · 仓位${(macroPosition * 100).toFixed(0)}%`
    : "/100";

  const macroKeysUpper = macroIndicatorKeys.map(k => k.toUpperCase());

  const decisionBg = decision.regime === "进攻" ? "bg-emerald-500/10 border-emerald-500/30"
    : decision.regime === "防御" ? "bg-amber-500/10 border-amber-500/30"
    : "bg-rose-500/10 border-rose-500/30";
  const decisionText = decision.regime === "进攻" ? "text-emerald-400"
    : decision.regime === "防御" ? "text-amber-400"
    : "text-rose-400";

  return (
    <div className="space-y-2 pb-2">
      {/* ════════ Decision Banner ════════ */}
      <div className={cn("rounded-xl border p-3", decisionBg)}>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-lg">{decision.regimeEmoji}</span>
          <span className={cn("text-sm font-bold", decisionText)}>
            {decision.regime} · 仓位 {decision.positionPct}%
            <span className="relative group/tip inline">
              <span tabIndex={0} className="inline-flex items-center ml-1 cursor-help"><Info className="w-3 h-3 text-zinc-500 hover:text-zinc-300" /></span>
              <div className="absolute left-1/2 -translate-x-1/2 top-5 w-72 max-w-[calc(100vw-2rem)] p-3 rounded-xl bg-zinc-900 border border-zinc-700 shadow-xl opacity-0 invisible group-hover/tip:opacity-100 group-hover/tip:visible transition-all duration-200 z-50">
                <p className="text-[10px] font-semibold text-zinc-300 mb-2">决策引擎 · 因子加权链路</p>
                <div className="text-[9px] leading-relaxed text-zinc-500 space-y-1.5">
                  <div>
                    <span className="text-zinc-400 font-medium">核心层 (加权求和 80%)</span><br/>
                    <span className="text-zinc-600">宏观25% + 情绪25% + VIX15% + 融资15%</span>
                  </div>
                  <div>
                    <span className="text-zinc-400 font-medium">增强层 (乘法调整 20%)</span><br/>
                    <span className="text-zinc-600">北向流×0.6~1.2 + 情绪动量×0.75~1.15 + 杠杆×0.6~1.0 + 板块广度×0.8~1.1 + 主题资金×0.75~1.1</span>
                  </div>
                  <div className="pt-1 border-t border-zinc-800">
                    <span className="text-zinc-400">输出:</span> composite ≥0.70→🟢进攻 / ≥0.45→🟡防御 / &lt;0.45→🔴观望
                  </div>
                  <div className="pt-1 border-t border-zinc-800">
                    <span className="text-zinc-500">底线: 仓位不超过宏观建议上限</span>
                  </div>
                  <div className="pt-1 border-t border-zinc-800">
                    <span className="text-zinc-400 font-medium">量化智能体</span><br/>
                    <span className="text-zinc-600">每日09:05由6个GBDT/DL模型输出方向/IC/买卖信号。方向=看多/看空，IC&gt;0.05有效、&gt;0.10优秀，买卖=score&gt;5/&lt;2筛选机会广度。与决策条互补——规则给方向，模型给置信度。</span>
                  </div>
                </div>
              </div>
            </span>
          </span>
        </div>
        <p className="text-[10px] text-zinc-400 leading-relaxed">{decision.reason}</p>
        {decision.focusThemes.length > 0 && (
          <p className="text-[10px] text-zinc-500 mt-1">
            关注: {decision.focusThemes.join(" · ")}
          </p>
        )}
        {decision.subSignals.length > 0 && (
          <p className="text-[9px] text-zinc-500 mt-0.5">
            {decision.subSignals.map((s, i) => (
              <span key={i} className={s.includes("⚠") ? "text-rose-400" : "text-zinc-500"}>{s}{i < decision.subSignals.length - 1 ? " · " : ""}</span>
            ))}
          </p>
        )}
        {quantBias && (
          <p className="text-[9px] text-violet-400/80 mt-0.5 font-medium inline-flex items-center gap-1">
            <Brain className="w-3 h-3" />{quantBias}
          </p>
        )}
      </div>

      {/* ──── M1: 宏观评分 ──── */}
      <ModuleCard label="M1" title="宏观评分" accent="emerald"
        icon={<TrendingUp className="w-3.5 h-3.5 text-emerald-400" />}
        metric={macroScore?.toFixed(0) ?? "--"} metricSub={macroMetricSub}
        subMetrics={
          macroKeysUpper.length > 0
            ? macroKeysUpper.slice(0, 4).map((k, i) => ({
                label: k, value: macroIndicatorValues[i]?.toFixed(1) ?? "--", color: "emerald" as const,
              }))
            : undefined
        }
        chart={macroChartData.length >= 2 ? <SparklineChart data={macroChartData.slice(-30)} color="#10b981" height={28} className="w-full h-7" /> : undefined}
        href="/mobile/macro"
      />

      {/* ──── M2: 市场情绪 ──── */}
      <ModuleCard label="M2" title="市场情绪"
        accent={sentimentScore && sentimentScore >= 60 ? "emerald" : sentimentScore && sentimentScore >= 40 ? "amber" : "rose"}
        icon={<Activity className="w-3.5 h-3.5" />}
        metric={sentimentScore ?? "--"}
        metricSub={
          <>
            {sentimentScore !== null ? (sentimentScore >= 60 ? "乐观" : sentimentScore >= 40 ? "中性" : "悲观") : ""}
            {sentDeltaWeek != null && (
              <span className={sentDeltaWeek >= 3 ? "text-emerald-400" : sentDeltaWeek <= -3 ? "text-rose-400" : "text-zinc-500"}>
                {" "}{sentDeltaWeek >= 0 ? "↑" : "↓"}{Math.abs(sentDeltaWeek)}(周)
              </span>
            )}
          </>
        }
        badge={sentimentAlert || undefined}
        subMetrics={[
          { label: "涨停", value: `${sentimentLimitUp}家`, color: "emerald" as const },
          { label: "涨停率", value: `${(sentimentLimitUpRate * 100).toFixed(1)}%`, color: sentimentLimitUpRate > 0.04 ? "rose" as const : "amber" as const },
          { label: "涨跌比", value: advDeclRatio != null ? advDeclRatio.toFixed(2) : "--", color: advDeclRatio != null && advDeclRatio > 2 ? "emerald" as const : advDeclRatio != null && advDeclRatio < 0.5 ? "rose" as const : "amber" as const },
          { label: "炸板率", value: bustRate != null ? `${(bustRate * 100).toFixed(0)}%` : "--", color: bustRate != null && bustRate > 0.3 ? "rose" as const : "emerald" as const },
          { label: "VIX", value: vixClose?.toFixed(1) ?? "--", color: vixClose && vixClose > 25 ? "rose" as const : "emerald" as const },
          ...(marketTurnover != null ? [{ label: "成交", value: `${(marketTurnover / 1e12).toFixed(2)}万亿`, color: "blue" as const }] : []),
          ...(sentPercentile != null ? [{ label: "分位", value: `>${sentPercentile}%`, color: sentPercentile > 80 ? "rose" as const : sentPercentile < 20 ? "emerald" as const : "amber" as const }] : []),
        ]}
        chart={sentimentChartData.length >= 2 ? <SparklineChart data={sentimentChartData.slice(-30)} color={sentimentColor} height={28} className="w-full h-7" /> : undefined}
        href="/mobile/sentiment"
      />

      {/* ──── M3: 指数快览 ──── */}
      <ModuleCard label="M3" title="指数快览" accent="blue"
        icon={<Globe className="w-3.5 h-3.5 text-blue-400" />}
        body={
          <div className="grid grid-cols-3 gap-x-3 gap-y-1.5 text-center">
            {domesticIndices.slice(0, 3).map(di => (
              <div key={di.label} className="text-xs">
                <span className="text-zinc-500 block text-[10px]">{di.label}</span>
                <span className={cn("font-semibold tabular-nums", di.pct != null && di.pct >= 0 ? "text-emerald-400" : "text-rose-400")}>{pctText(di.pct)}</span>
              </div>
            ))}
            {domesticIndices.slice(3, 5).map(di => (
              <div key={di.label} className="text-xs">
                <span className="text-zinc-500 block text-[10px]">{di.label}</span>
                <span className={cn("font-semibold tabular-nums", di.pct != null && di.pct >= 0 ? "text-emerald-400" : "text-rose-400")}>{pctText(di.pct)}</span>
              </div>
            ))}
            {globalIndices.slice(0, 1).map(gi => (
              <div key={gi.label} className="text-xs">
                <span className="text-zinc-500 block text-[10px]">{gi.label}</span>
                <span className={cn("font-semibold tabular-nums", gi.pct != null && gi.pct >= 0 ? "text-emerald-400" : "text-rose-400")}>{pctText(gi.pct)}</span>
              </div>
            ))}
          </div>
        }
        href="/mobile/index"
      />

      {/* ──── M4: 两融资金 ──── */}
      <ModuleCard label="M4" title="两融资金" accent="amber"
        icon={<DollarSign className="w-3.5 h-3.5 text-amber-400" />}
        metric={marginBalance != null ? `${(marginBalance / 1e8).toFixed(0)}亿` : "--"}
        metricSub={
          <span className={marginTrend != null && marginTrend > 0 ? "text-emerald-400" : "text-rose-400"}>
            {marginTrend != null ? `${marginTrend >= 0 ? "↑" : "↓"}${Math.abs(marginTrend).toFixed(1)}%(周)` : ""}
          </span>
        }
        subMetrics={[
          { label: "买入", value: latestMarginBuy != null ? `${(latestMarginBuy / 1e8).toFixed(0)}亿` : "--", color: "amber" as const },
          { label: "融券", value: shortBalance != null ? `${(shortBalance / 1e8).toFixed(0)}亿` : "--", color: "rose" as const },
          { label: "杠杆率", value: leverageRate != null ? `${leverageRate.toFixed(1)}%` : "--", color: leverageRate != null && leverageRate > 12 ? "rose" as const : leverageRate != null && leverageRate > 8 ? "amber" as const : "emerald" as const },
          ...(lhbTop5.length > 0 ? [{ label: "龙虎榜", value: `${lhbTop5[0].name} ${lhbTop5[0].net_amount >= 0 ? "+" : ""}${(lhbTop5[0].net_amount / 1e8).toFixed(1)}亿`, color: lhbTop5[0].net_amount >= 0 ? "emerald" as const : "rose" as const }] : []),
        ]}
        chart={marginChartData.length >= 2 ? <SparklineChart data={marginChartData.slice(-60)} color="#f59e0b" height={28} className="w-full h-7" /> : undefined}
        href="/mobile/flow"
      />

      {/* ──── M5: 北向/南向资金 ──── */}
      <ModuleCard label="M5" title="资金流向" accent="blue"
        icon={<ArrowUpDown className="w-3.5 h-3.5 text-blue-400" />}
        body={
          <div className="space-y-1.5">
            {/* 北向板块 */}
            {northSectors.length > 0 && (
              <div className="flex items-center gap-1 flex-wrap text-[10px]">
                <span className="text-zinc-500 text-[9px]">北向板块</span>
                {northSectors.slice(0, 3).map(s => (
                  <span key={s.sector} className={cn("tabular-nums", s.total_net_buy >= 0 ? "text-emerald-400" : "text-rose-400")}>
                    {s.sector}{s.total_net_buy >= 0 ? "+" : ""}{(s.total_net_buy/1e8).toFixed(1)}亿
                  </span>
                ))}
              </div>
            )}
            {/* 北向个股 */}
            <div className="flex items-center gap-1 flex-wrap text-[10px]">
              <span className="text-zinc-500 text-[9px]">北向</span>
              <span className={cn("tabular-nums font-semibold", northTotalInflow >= 0 ? "text-blue-400" : "text-rose-400")}>{formatBillions(northTotalInflow)}</span>
              {northDeltaWeek != null && (
                <span className={cn("tabular-nums text-[9px]", northDeltaWeek >= 0 ? "text-emerald-400" : "text-rose-400")}>
                  {northDeltaWeek >= 0 ? "↑" : "↓"}{Math.abs(northDeltaWeek).toFixed(0)}%(周)
                </span>
              )}
              {northStocks.slice(0, 3).map(s => (
                <span key={s.symbol} className="tabular-nums text-zinc-400">
                  {s.symbol} {formatBillions(s.net_inflow)}
                </span>
              ))}
            </div>
            {/* 南向个股 */}
            <div className="flex items-center gap-1 flex-wrap text-[10px]">
              <span className="text-zinc-500 text-[9px]">南向</span>
              <span className={cn("tabular-nums font-semibold", southTotalInflow >= 0 ? "text-violet-400" : "text-rose-400")}>{formatBillions(southTotalInflow)}</span>
              {southDeltaWeek != null && (
                <span className={cn("tabular-nums text-[9px]", southDeltaWeek >= 0 ? "text-emerald-400" : "text-rose-400")}>
                  {southDeltaWeek >= 0 ? "↑" : "↓"}{Math.abs(southDeltaWeek).toFixed(0)}%(周)
                </span>
              )}
              {southStocks.slice(0, 3).map(s => (
                <span key={s.symbol} className="tabular-nums text-zinc-400">
                  {s.symbol} {formatBillions(s.net_inflow)}
                </span>
              ))}
            </div>
            {/* ETF */}
            {etfFlows.length > 0 && (
              <div className="flex items-center gap-1 flex-wrap text-[10px] border-t border-zinc-800/50 pt-1.5">
                <span className="text-zinc-500 text-[9px]">ETF</span>
                {etfFlows.slice(0, 4).map(e => (
                  <span key={e.symbol} className={cn("tabular-nums", e.pct_change >= 0 ? "text-emerald-400" : "text-rose-400")}>
                    {e.name.length > 4 ? e.name.slice(0, 4) : e.name}{pctText(e.pct_change)}
                  </span>
                ))}
              </div>
            )}
          </div>
        }
        href="/mobile/hsgt"
      />

      {/* ──── M6: 外围市场 ──── */}
      <ModuleCard label="M6" title="外围市场" accent="cyan"
        icon={<Waves className="w-3.5 h-3.5 text-cyan-400" />}
        body={
          <div className="grid grid-cols-3 gap-x-3 text-center">
            <div className="text-xs">
              <span className="text-zinc-500 block text-[10px]">中概互联</span>
              <span className={cn("font-semibold tabular-nums", kwebPct != null && kwebPct >= 0 ? "text-emerald-400" : "text-rose-400")}>{pctText(kwebPct)}</span>
            </div>
            {futures.map(f => (
              <div key={f.label} className="text-xs">
                <span className="text-zinc-500 block text-[10px]">{f.label}</span>
                <span className={cn("font-semibold tabular-nums", f.pct != null && f.pct >= 0 ? "text-emerald-400" : "text-rose-400")}>{pctText(f.pct)}</span>
              </div>
            ))}
          </div>
        }
      />

      {/* ──── M7: 板块地图 ──── */}
      <ModuleCard label="M7" title="板块地图" accent="emerald"
        icon={<Target className="w-3.5 h-3.5 text-emerald-400" />}
        body={
          themePool.length > 0 ? (
            <div className="space-y-1.5 text-[10px]">
              <div className="text-[9px] text-zinc-500 mb-0.5">主线</div>
              {themePool.slice(0, 3).map(t => {
                const best = [...t.stocks].sort((a, b) => b.change_pct - a.change_pct)[0];
                const flow = themeFlowMap[t.theme];
                return (
                  <div key={t.theme} className="flex items-center gap-1 flex-wrap">
                    <span className="text-zinc-400">{t.theme}</span>
                    <span className={cn("tabular-nums", best?.change_pct >= 0 ? "text-emerald-400" : "text-rose-400")}>
                      {best ? `${best.change_pct >= 0 ? "+" : ""}${best.change_pct.toFixed(1)}%` : "--"}
                    </span>
                    {flow != null && (
                      <span className={cn("tabular-nums text-[9px]", flow >= 0 ? "text-emerald-400/70" : "text-rose-400/70")}>
                        {flow >= 0 ? "+" : ""}{(flow/1e8).toFixed(1)}亿
                      </span>
                    )}
                  </div>
                );
              })}
              {themePool.length > 3 && (
                <>
                  <div className="text-[9px] text-zinc-500 mt-1 mb-0.5">潜力</div>
                  {themePool.slice(3, 6).map(t => {
                    const best = [...t.stocks].sort((a, b) => b.change_pct - a.change_pct)[0];
                    const flow = themeFlowMap[t.theme];
                    return (
                      <div key={t.theme} className="flex items-center gap-1 flex-wrap">
                        <span className="text-zinc-500">{t.theme}</span>
                        <span className={cn("tabular-nums", best?.change_pct >= 0 ? "text-emerald-400/70" : "text-rose-400/70")}>
                          {best ? `${best.change_pct >= 0 ? "+" : ""}${best.change_pct.toFixed(1)}%` : "--"}
                        </span>
                        {flow != null && (
                          <span className={cn("tabular-nums text-[9px]", flow >= 0 ? "text-emerald-400/50" : "text-rose-400/50")}>
                            {flow >= 0 ? "+" : ""}{(flow/1e8).toFixed(1)}亿
                          </span>
                        )}
                      </div>
                    );
                  })}
                </>
              )}
            </div>
          ) : undefined
        }
        badge={themePool.length === 0 ? "数据接入中" : undefined}
        href="/mobile/sectors"
      />

      {/* ──── 最新信号 ──── */}
      {(latestPicks || latestBuy) && (
        <div className="rounded-2xl border border-emerald-500/10 bg-emerald-500/[0.02] p-3">
          <p className="text-[10px] font-semibold text-emerald-400/80 mb-2 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            最新交易信号
          </p>
          <div className="space-y-1.5">
            {latestPicks && (
              <a href={`/mobile/reports/${latestPicks.id}`} className="block text-zinc-300 hover:text-emerald-400">
                <span className="text-[11px] font-medium">📌 今日荐股</span>
                <span className="text-[10px] text-zinc-500 ml-2 truncate">{latestPicks.title}</span>
              </a>
            )}
            {latestBuy && (
              <a href={`/mobile/reports/${latestBuy.id}`} className="block text-zinc-300 hover:text-emerald-400">
                <span className="text-[11px] font-medium">📈 买入信号</span>
                <span className="text-[10px] text-zinc-500 ml-2 truncate">{latestBuy.title}</span>
              </a>
            )}
          </div>
        </div>
      )}

      {/* ──── 持仓复盘 ──── */}
      {latestReview && (
        <div className="rounded-2xl border border-violet-500/10 bg-violet-500/[0.02] p-3">
          <p className="text-[10px] font-semibold text-violet-400/80 mb-2">持仓复盘</p>
          <a href={`/mobile/reports/${latestReview.id}`} className="block text-xs text-zinc-300 hover:text-violet-400 truncate">
            <BarChart3 className="w-3.5 h-3.5 inline-block mr-0.5" /> {latestReview.title}
          </a>
        </div>
      )}
    </div>
  );
}
