import { getMacroScores, getLatestMacroScore } from "@/lib/db";
import { StatCard } from "@/components/stat-card";
import { safeJsonParse } from "@/lib/utils";
import { TrendingUp, Activity, DollarSign, BarChart3 } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 60;

// Simple inline SVG chart (no Recharts needed for server component)
function Sparkline({
  data,
  color = "#10b981",
  height = 60,
}: {
  data: number[];
  color?: string;
  height?: number;
}) {
  if (data.length < 2) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const width = 200;
  const points = data
    .map(
      (v, i) =>
        `${(i / (data.length - 1)) * width},${height - ((v - min) / range) * (height - 4) - 2}`
    )
    .join(" ");
  return (
    <svg width={width} height={height} className="w-full">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function MacroPage() {
  const scores = getMacroScores(60);
  const latest = getLatestMacroScore();

  // Reverse for chart display (oldest first)
  const chartData = [...scores].reverse();

  const scoreValues = chartData.map((s) => s.score);
  const positionValues = chartData.map((s) => s.position * 100);

  const latestIndicators = safeJsonParse(latest?.indicators ?? null);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">宏观评分</h1>
        <p className="text-sm text-zinc-500 mt-1">
          综合评分 & 仓位建议 · {scores.length} 条记录
        </p>
      </div>

      {latest && (
        <>
          {/* Current */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <StatCard
              title="综合评分"
              value={latest.score.toFixed(1)}
              subtitle="满分100"
              icon={<TrendingUp className="w-4 h-4" />}
              trend={latest.score >= 70 ? "up" : latest.score >= 50 ? "neutral" : "down"}
            />
            <StatCard
              title="推荐仓位"
              value={`${(latest.position * 100).toFixed(0)}%`}
              subtitle="0=空仓 100=满仓"
              icon={<DollarSign className="w-4 h-4" />}
              trend={latest.position >= 0.7 ? "up" : latest.position >= 0.4 ? "neutral" : "down"}
            />
            <StatCard
              title="数据日期"
              value={latest.date}
              icon={<Activity className="w-4 h-4" />}
            />
            <StatCard
              title="记录条数"
              value={scores.length}
              subtitle="历史总计"
              icon={<BarChart3 className="w-4 h-4" />}
            />
          </div>

          {/* Score Trend */}
          {scoreValues.length >= 2 && (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
              <h2 className="text-sm font-semibold text-zinc-300 mb-4">
                评分趋势
              </h2>
              <div className="h-16">
                <Sparkline data={scoreValues} color="#10b981" height={64} />
              </div>
              <div className="flex justify-between text-[10px] text-zinc-600 mt-1">
                <span>{chartData[0]?.date}</span>
                <span>{chartData[chartData.length - 1]?.date}</span>
              </div>
            </div>
          )}

          {/* Position Trend */}
          {positionValues.length >= 2 && (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
              <h2 className="text-sm font-semibold text-zinc-300 mb-4">
                仓位建议趋势
              </h2>
              <div className="h-16">
                <Sparkline data={positionValues} color="#f59e0b" height={64} />
              </div>
              <div className="flex justify-between text-[10px] text-zinc-600 mt-1">
                <span>{chartData[0]?.date}</span>
                <span>{chartData[chartData.length - 1]?.date}</span>
              </div>
            </div>
          )}

          {/* Indicators Detail */}
          {latestIndicators && (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
              <h2 className="text-sm font-semibold text-zinc-300 mb-3">
                分项指标
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {Object.entries(latestIndicators).map(([key, value]) => (
                  <div
                    key={key}
                    className="rounded-lg border border-zinc-800 bg-zinc-900/80 p-3"
                  >
                    <span className="text-[10px] text-zinc-500 uppercase">
                      {key}
                    </span>
                    <p className="text-lg font-bold tabular-nums mt-0.5">
                      {typeof value === "number" ? value.toFixed(2) : String(value)}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* History Table */}
      {scores.length > 0 && (
        <div className="rounded-xl border border-zinc-800 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/50">
                <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400">
                  日期
                </th>
                <th className="text-right px-4 py-3 text-xs font-medium text-zinc-400">
                  评分
                </th>
                <th className="text-right px-4 py-3 text-xs font-medium text-zinc-400">
                  仓位
                </th>
              </tr>
            </thead>
            <tbody>
              {scores.slice(0, 30).map((s, i) => (
                <tr
                  key={s.date}
                  className={`border-b border-zinc-800/50 ${
                    i % 2 === 0 ? "bg-transparent" : "bg-zinc-900/30"
                  }`}
                >
                  <td className="px-4 py-2.5 text-zinc-300 tabular-nums">
                    {s.date}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    <span
                      className={
                        s.score >= 70
                          ? "text-emerald-400"
                          : s.score >= 50
                          ? "text-amber-400"
                          : "text-red-400"
                      }
                    >
                      {s.score.toFixed(1)}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right text-zinc-400 tabular-nums">
                    {(s.position * 100).toFixed(0)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {scores.length === 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-12 text-center">
          <TrendingUp className="w-10 h-10 text-zinc-600 mx-auto mb-3" />
          <p className="text-sm text-zinc-500">暂无宏观数据</p>
          <p className="text-xs text-zinc-600 mt-1">
            等待 macro-score cron 推送数据...
          </p>
        </div>
      )}
    </div>
  );
}
