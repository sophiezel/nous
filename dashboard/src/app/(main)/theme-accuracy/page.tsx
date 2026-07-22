import {
  getThemeAccuracyTrend,
  getThemeScoreDistribution,
  getThemeTopStocks,
  getThemeMarketDistribution,
} from "@/lib/db";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { cn } from "@/lib/utils";
import {
  Target,
  TrendingUp,
  PieChart,
  GitCompareArrows,
} from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 300;

function StatBadge({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-zinc-800/30 border border-zinc-700/30">
      <span className="text-[10px] text-zinc-500">{label}</span>
      <span className={cn("text-xs font-bold tabular-nums", color || "text-zinc-200")}>{value}</span>
    </div>
  );
}

export default function ThemeAccuracyPage() {
  const accuracyTrend = getThemeAccuracyTrend(60);
  const scoreDistribution = getThemeScoreDistribution();
  const topStocks = getThemeTopStocks(undefined, 10);
  const marketDistribution = getThemeMarketDistribution();

  // Prepare sparkline data for avg_score trend
  const trendScores = accuracyTrend.map((r) => Number(r.avg_score));
  const trendLabels = accuracyTrend.map((r) => r.screen_date?.slice(5) || "");

  const totalScored = scoreDistribution.reduce((s, b) => s + b.count, 0);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold tracking-tight">主线预测准确率</h1>
        <p className="text-sm text-zinc-500 mt-1">
          每日评分趋势 · 分布统计 · 命中率分析
        </p>
      </div>

      {/* Daily Score Trend */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-emerald-400" />
          每日评分趋势
        </h2>
        {accuracyTrend.length === 0 ? (
          <div className="p-8 text-center text-zinc-500 text-sm">暂无评分数据</div>
        ) : (
          <>
            <ZoomableSparkline
              data={trendScores}
              labels={trendLabels}
              color="#10b981"
              height={60}
              className="w-full"
            />
            <div className="flex justify-between text-[9px] text-zinc-600 mt-1">
              <span>{trendLabels[0] || ""}</span>
              <span>{trendLabels[trendLabels.length - 1] || ""}</span>
            </div>
            <div className="grid grid-cols-3 gap-2 mt-4">
              <StatBadge
                label="最新日均分"
                value={trendScores.length > 0 ? trendScores[trendScores.length - 1].toFixed(2) : "—"}
                color="text-emerald-400"
              />
              <StatBadge
                label="趋势最高"
                value={trendScores.length > 0 ? Math.max(...trendScores).toFixed(2) : "—"}
                color="text-amber-400"
              />
              <StatBadge
                label="趋势最低"
                value={trendScores.length > 0 ? Math.min(...trendScores).toFixed(2) : "—"}
                color="text-red-400"
              />
            </div>
          </>
        )}
      </div>

      {/* Distribution + Hit Rate */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Score Distribution */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
          <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
            <PieChart className="w-4 h-4 text-violet-400" />
            评分分布 · 已确认 / 潜在 / 观察
          </h2>
          {scoreDistribution.length === 0 ? (
            <div className="p-8 text-center text-zinc-500 text-sm">暂无分布数据</div>
          ) : (
            <div className="space-y-2">
              {scoreDistribution.map((b) => {
                const pct = totalScored > 0 ? ((b.count / totalScored) * 100).toFixed(1) : "0";
                const colors: Record<string, string> = {
                  "高评分(8-10)": "bg-emerald-500",
                  "中高评分(6-8)": "bg-blue-500",
                  "中等评分(4-6)": "bg-amber-500",
                  "中低评分(2-4)": "bg-orange-500",
                  "低评分(0-2)": "bg-red-500",
                };
                return (
                  <div key={b.bin} className="flex items-center gap-2">
                    <span className={cn("w-2 h-2 rounded-full shrink-0", colors[b.bin] || "bg-zinc-500")} />
                    <span className="text-[10px] text-zinc-400 w-28">{b.bin}</span>
                    <div className="flex-1 h-2.5 bg-zinc-800 rounded-full overflow-hidden">
                      <div
                        className={cn("h-full rounded-full transition-all", colors[b.bin] || "bg-zinc-500")}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="text-[10px] text-zinc-500 w-12 text-right tabular-nums">
                      {b.count} ({pct}%)
                    </span>
                  </div>
                );
              })}
              <p className="text-[9px] text-zinc-600 mt-2 text-center">总样本: {totalScored}</p>
            </div>
          )}
        </div>

        {/* Top-3 Hit Rate */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
          <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
            <Target className="w-4 h-4 text-rose-400" />
            Top-3 命中率
          </h2>
          {topStocks.length === 0 ? (
            <div className="p-8 text-center text-zinc-500 text-sm">暂无评分数据</div>
          ) : (
            <div className="space-y-1.5">
              {topStocks.slice(0, 3).map((s, i) => (
                <div
                  key={s.symbol}
                  className="flex items-center justify-between py-1.5 px-3 rounded-lg bg-zinc-800/30 border border-zinc-700/30"
                >
                  <div className="flex items-center gap-2">
                    <span className={cn(
                      "w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold",
                      i === 0 ? "bg-amber-500/20 text-amber-400" :
                      i === 1 ? "bg-zinc-500/20 text-zinc-400" :
                      "bg-orange-500/20 text-orange-400"
                    )}>
                      {i + 1}
                    </span>
                    <span className="text-[10px] font-medium text-zinc-200">{s.name || s.symbol}</span>
                    <span className="text-[9px] text-zinc-500 font-mono">{s.symbol}</span>
                  </div>
                  <span className="text-[10px] font-bold tabular-nums text-emerald-400">
                    {Number(s.score).toFixed(1)}
                  </span>
                </div>
              ))}
              <div className="grid grid-cols-3 gap-2 mt-3">
                <StatBadge label="PE" value={topStocks[0]?.pe != null ? Number(topStocks[0].pe).toFixed(1) : "—"} />
                <StatBadge label="PB" value={topStocks[0]?.pb != null ? Number(topStocks[0].pb).toFixed(2) : "—"} />
                <StatBadge label="ROE" value={topStocks[0]?.roe != null ? Number(topStocks[0].roe).toFixed(1) : "—"} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Lead-Lag Analysis */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
          <GitCompareArrows className="w-4 h-4 text-cyan-400" />
          领先-滞后分析 (按市场)
        </h2>
        {marketDistribution.length === 0 ? (
          <div className="p-8 text-center text-zinc-500 text-sm">暂无市场分布数据</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-zinc-500 border-b border-zinc-800">
                  <th className="text-left py-2 pr-3 font-normal">市场</th>
                  <th className="text-right py-2 px-3 font-normal">股票数</th>
                  <th className="text-right py-2 px-3 font-normal">平均评分</th>
                  <th className="text-right py-2 pl-3 font-normal">占比</th>
                </tr>
              </thead>
              <tbody>
                {marketDistribution.map((m) => {
                  const total = marketDistribution.reduce((s, x) => s + x.count, 0);
                  const pct = total > 0 ? ((m.count / total) * 100).toFixed(1) : "0";
                  const marketLabel = m.market === "a" ? "A股" : m.market === "hk" ? "港股" : m.market || "未知";
                  return (
                    <tr key={m.market} className="border-b border-zinc-800/30 hover:bg-zinc-800/20">
                      <td className="py-2 pr-3 text-zinc-200 font-medium">{marketLabel}</td>
                      <td className="py-2 px-3 text-right tabular-nums text-zinc-400">{m.count}</td>
                      <td className="py-2 px-3 text-right tabular-nums text-zinc-300">
                        <span className={m.avg_score >= 6 ? "text-emerald-400" : m.avg_score >= 4 ? "text-amber-400" : "text-red-400"}>
                          {m.avg_score.toFixed(2)}
                        </span>
                      </td>
                      <td className="py-2 pl-3 text-right tabular-nums text-zinc-500">{pct}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
