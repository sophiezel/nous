import { getSentimentHistory, getLatestSentiment } from "@/lib/db";
import { StatCard } from "@/components/stat-card";
import { safeJsonParse } from "@/lib/utils";
import { Activity, TrendingUp, Zap, Flame } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 60;

export default function SentimentPage() {
  const history = getSentimentHistory(60);
  const latest = getLatestSentiment();

  const chartData = [...history].reverse();
  const scoreValues = chartData.map((s) => s.score);
  const latestDetails = safeJsonParse(latest?.details ?? null);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">市场情绪</h1>
        <p className="text-sm text-zinc-500 mt-1">
          涨停梯队 · 炸板率 · 连板高度 · {history.length} 条记录
        </p>
      </div>

      {latest && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <StatCard
              title="情绪分"
              value={latest.score}
              subtitle="满分100"
              icon={<Activity className="w-4 h-4" />}
              trend={latest.score >= 60 ? "up" : latest.score >= 40 ? "neutral" : "down"}
            />
            <StatCard
              title="涨停家数"
              value={latest.limit_up_count}
              icon={<TrendingUp className="w-4 h-4" />}
            />
            <StatCard
              title="涨停率"
              value={`${(latest.limit_up_rate * 100).toFixed(1)}%`}
              icon={<Zap className="w-4 h-4" />}
            />
            <StatCard
              title="数据日期"
              value={latest.date}
              icon={<Flame className="w-4 h-4" />}
            />
          </div>

          {/* Details */}
          {latestDetails && (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
              <h2 className="text-sm font-semibold text-zinc-300 mb-3">
                情绪详情
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {Object.entries(latestDetails).map(([key, value]) => (
                  <div
                    key={key}
                    className="rounded-lg border border-zinc-800 bg-zinc-900/80 p-3"
                  >
                    <span className="text-[10px] text-zinc-500">{key}</span>
                    <p className="text-lg font-bold tabular-nums mt-0.5">
                      {typeof value === "number"
                        ? key.includes("率") || key.includes("%")
                          ? `${(value * 100).toFixed(1)}%`
                          : value.toFixed(2)
                        : String(value)}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* History Table */}
      {history.length > 0 && (
        <div className="rounded-xl border border-zinc-800 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/50">
                <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400">
                  日期
                </th>
                <th className="text-right px-4 py-3 text-xs font-medium text-zinc-400">
                  情绪分
                </th>
                <th className="text-right px-4 py-3 text-xs font-medium text-zinc-400">
                  涨停数
                </th>
                <th className="text-right px-4 py-3 text-xs font-medium text-zinc-400">
                  涨停率
                </th>
              </tr>
            </thead>
            <tbody>
              {history.slice(0, 30).map((s, i) => (
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
                        s.score >= 60
                          ? "text-emerald-400"
                          : s.score >= 40
                          ? "text-amber-400"
                          : "text-red-400"
                      }
                    >
                      {s.score}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right text-zinc-400 tabular-nums">
                    {s.limit_up_count}
                  </td>
                  <td className="px-4 py-2.5 text-right text-zinc-400 tabular-nums">
                    {(s.limit_up_rate * 100).toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {history.length === 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-12 text-center">
          <Activity className="w-10 h-10 text-zinc-600 mx-auto mb-3" />
          <p className="text-sm text-zinc-500">暂无情绪数据</p>
          <p className="text-xs text-zinc-600 mt-1">
            等待 sentiment-collect cron 推送数据...
          </p>
        </div>
      )}
    </div>
  );
}
