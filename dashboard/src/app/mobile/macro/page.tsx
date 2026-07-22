import { fetchAPI } from "@/lib/api";
import { MacroRing } from "@/components/mobile/macro-ring";
import { SparklineChart } from "@/components/mobile/sparkline";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { TrendingUp, DollarSign, Info } from "lucide-react";
import { safeJsonParse } from "@/lib/utils";
import { PagePoller } from "@/components/PagePoller";

export const dynamic = "force-dynamic";
export const revalidate = 60;

interface IndicatorDef {
  key: string;
  label: string;
  unit: string;
  desc: string;
  impact: string;
  color: string;
  history: { trade_date: string; value: number }[];
}

export default async function MobileMacroPage() {
  const [scoresRes, latestRes, indicatorsRes] = await Promise.all([
    fetchAPI("/v1/macro/scores?limit=60"),
    fetchAPI("/v1/macro/score"),
    fetchAPI("/v1/macro/indicators"),
  ]);

  const scores: any[] = scoresRes ?? [];
  const latest: any = latestRes ?? null;
  const latestIndicators = safeJsonParse(latest?.indicators ?? null);
  const latestDate = latest?.date?.slice(0, 10) ?? null;

  // Parse indicators response into per-indicator histories
  const indicatorMap: Record<string, { trade_date: string; value: number }[]> = {};
  if (Array.isArray(indicatorsRes)) {
    for (const row of indicatorsRes) {
      const key = row.indicator || row.key;
      if (!indicatorMap[key]) indicatorMap[key] = [];
      indicatorMap[key].push({ trade_date: row.trade_date, value: row.value });
    }
  }

  const chartData = [...scores].reverse();
  const scoreValues = chartData.map(s => s.score);

  // ── Build indicator defs ──
  const indicators: IndicatorDef[] = [
    {
      key: "CPI", label: "CPI 同比", unit: "%",
      desc: "居民消费价格指数。衡量一篮子消费品价格变动，反映终端通胀水平。数值越高=通胀越严重。",
      impact: "CPI>3%→央行收紧货币→利空股市；1-3%=温和中性；<0%=通缩→经济衰退信号。",
      color: "#ef4444",
      history: [...(indicatorMap["CPI"] || [])].reverse(),
    },
    {
      key: "PPI", label: "PPI 同比", unit: "%",
      desc: "工业生产者出厂价格指数。衡量工业品出厂价变动，领先CPI约3-6个月，是通胀的先行指标。",
      impact: "PPI上行→企业利润改善→周期股利好；PPI下行→通缩压力→消费股利好。",
      color: "#f59e0b",
      history: [...(indicatorMap["PPI"] || [])].reverse(),
    },
    {
      key: "PMI", label: "制造业 PMI", unit: "",
      desc: "采购经理人指数。通过对采购经理的月度调查编制，>50=经济扩张，<50=经济收缩。",
      impact: "PMI>50且连续上升→经济上行→股市利好；PMI<50连续3个月→衰退预警→减仓信号。",
      color: "#8b5cf6",
      history: [...(indicatorMap["PMI"] || [])].reverse(),
    },
    {
      key: "M2", label: "M2 同比", unit: "%",
      desc: "广义货币供应量同比增速。反映市场整体流动性充裕程度。M2=现金+活期+定期+储蓄存款。",
      impact: "M2增速上升→流动性充裕→利好股市估值；>12%→可能过热；<8%→流动性偏紧。",
      color: "#3b82f6",
      history: [...(indicatorMap["M2"] || [])].reverse(),
    },
    {
      key: "SHIBOR", label: "SHIBOR 隔夜", unit: "%",
      desc: "上海银行间同业拆放利率（隔夜）。反映银行间短期资金成本，是市场流动性的即时温度计。",
      impact: "SHIBOR上升→资金面收紧→利空；下降→宽松→利好。急升>50bp→流动性危机预警。",
      color: "#06b6d4",
      history: [...(indicatorMap["SHIBOR"] || [])].reverse(),
    },
  ];

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-bold">宏观评分</h1>

      {latest ? (
        <>
          {/* Hero gauge */}
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5">
            <div className="flex items-center gap-4">
              <MacroRing score={latest.score} size={80} strokeWidth={6} />
              <div className="flex-1">
                <div className="flex items-baseline gap-1">
                  <span className="text-3xl font-bold tabular-nums text-emerald-400">{latest.score.toFixed(0)}</span>
                  <span className="text-sm text-zinc-500">/100</span>
                </div>
                <div className="flex items-center gap-2 mt-1">
                  <DollarSign className="w-3.5 h-3.5 text-amber-400" />
                  <span className="text-sm font-medium text-zinc-300">仓位 {(latest.position * 100).toFixed(0)}%</span>
                </div>
                <p className="text-[11px] text-zinc-500 mt-1">{latestDate}</p>
              </div>
            </div>
            <div className="mt-4">
              <div className="flex justify-between text-[10px] text-zinc-500 mb-1">
                <span>空仓</span><span>半仓</span><span>满仓</span>
              </div>
              <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                <div className="h-full bg-gradient-to-r from-emerald-500 to-amber-500 rounded-full" style={{ width: `${latest.position * 100}%` }} />
              </div>
            </div>
          </div>

          {/* Score trend */}
          {scoreValues.length >= 2 && (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
              <div className="flex items-center gap-2 mb-2">
                <TrendingUp className="w-4 h-4 text-emerald-400" />
                <span className="text-xs font-medium text-zinc-400">评分趋势</span>
              </div>
              <SparklineChart data={scoreValues} color="#10b981" height={50} className="w-full h-12" />
            </div>
          )}

          {/* ── Indicator cards ── */}
          {indicators.map(ind => {
            const latestVal = ind.history[ind.history.length - 1]?.value;
            const hasData = ind.history.length >= 2;
            return (
              <details key={ind.key} className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden group">
                <summary className="p-3 list-none cursor-pointer">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: ind.color }} />
                    <span className="text-xs font-medium text-zinc-300">{ind.label}</span>
                    <span className="tabular-nums text-sm font-bold ml-auto" style={{ color: ind.color }}>
                      {latestVal != null ? latestVal.toFixed(2) + ind.unit : "--"}
                    </span>
                  </div>
                  {/* Trend always visible */}
                  {hasData && (
                    <ZoomableSparkline
                      data={ind.history.map(r => r.value)}
                      labels={ind.history.map(r => r.trade_date)}
                      color={ind.color}
                      height={36}
                      className="w-full"
                    />
                  )}
                </summary>
                <div className="px-3 pb-3 space-y-2">
                  {/* Tooltip — only on expand */}
                  <div className="bg-zinc-800/50 rounded-lg p-2.5 text-[10px] leading-relaxed space-y-1.5">
                    <div className="flex items-start gap-1.5">
                      <Info className="w-3 h-3 text-zinc-500 mt-0.5 shrink-0" />
                      <span className="text-zinc-400">{ind.desc}</span>
                    </div>
                    <div className="flex items-start gap-1.5">
                      <TrendingUp className="w-3 h-3 text-zinc-500 mt-0.5 shrink-0" />
                      <span className="text-zinc-500">对股市: {ind.impact}</span>
                    </div>
                  </div>
                  <div className="flex gap-3 text-[10px] text-zinc-500">
                    <span>{hasData ? `${ind.history[0]?.trade_date?.slice(0, 7)} ~ ${ind.history[ind.history.length - 1]?.trade_date?.slice(0, 7)}` : ""}</span>
                    <span>{ind.history.length} 条记录</span>
                  </div>
                </div>
              </details>
            );
          })}
        </>
      ) : (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <TrendingUp className="w-10 h-10 text-zinc-700 mb-3" />
          <p className="text-sm text-zinc-500">暂无宏观数据</p>
        </div>
      )}
      <Poller pollKey="macro" />
    </div>
  );
}
