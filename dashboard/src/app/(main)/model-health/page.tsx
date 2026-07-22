import {
  getModelHealthRankIC,
  getModelHealthPSI,
  getModelHealthHalfLife,
  getModelHealthRetrainLog,
} from "@/lib/db";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { cn } from "@/lib/utils";
import {
  Activity,
  Gauge,
  Clock,
  ScrollText,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 300;

function PSIGauge({ label, value, status }: { label: string; value: number; status: string }) {
  const pct = Math.min(100, Math.abs(value) * 100);
  const colors: Record<string, string> = {
    good: "text-emerald-400 border-emerald-500/20 bg-emerald-500/10",
    warning: "text-amber-400 border-amber-500/20 bg-amber-500/10",
    danger: "text-red-400 border-red-500/20 bg-red-500/10",
  };
  const barColors: Record<string, string> = {
    good: "bg-emerald-500",
    warning: "bg-amber-500",
    danger: "bg-red-500",
  };
  return (
    <div className="flex flex-col items-center p-3 rounded-xl border border-zinc-700/30 bg-zinc-800/30">
      <span className="text-[9px] text-zinc-500 uppercase tracking-wider mb-2">{label}</span>
      {/* Mini gauge bar */}
      <div className="w-full h-2 bg-zinc-700 rounded-full overflow-hidden mb-2">
        <div
          className={cn("h-full rounded-full transition-all", barColors[status] || "bg-zinc-500")}
          style={{ width: `${Math.min(100, pct)}%` }}
        />
      </div>
      <span className={cn("text-xs font-bold tabular-nums", colors[status]?.split(" ")[0] || "text-zinc-400")}>
        {(value * 100).toFixed(1)}%
      </span>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    good: "bg-emerald-500",
    warning: "bg-amber-500",
    danger: "bg-red-500",
  };
  return <span className={cn("w-1.5 h-1.5 rounded-full inline-block", colors[status] || "bg-zinc-500")} />;
}

export default function ModelHealthPage() {
  const rankICData = getModelHealthRankIC(60);
  const psiMetrics = getModelHealthPSI();
  const halfLifeData = getModelHealthHalfLife(30);
  const retrainLogs = getModelHealthRetrainLog(50);

  // Prepare Rank IC sparkline data (group by date, avg across models)
  const rankICByDate = new Map<string, number[]>();
  for (const r of rankICData) {
    const arr = rankICByDate.get(r.trade_date) || [];
    arr.push(Number(r.rank_ic));
    rankICByDate.set(r.trade_date, arr);
  }
  const rankICDates = Array.from(rankICByDate.keys()).sort();
  const rankICAvg = rankICDates.map((d) => {
    const vals = rankICByDate.get(d)!;
    return vals.reduce((s, v) => s + v, 0) / vals.length * 100;
  });

  // Half-life sparkline
  const halfLifeDates = halfLifeData.map((r) => r.trade_date?.slice(5) || "");
  const halfLifeValues = halfLifeData.map((r) => r.half_life_days);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold tracking-tight">模型健康监控</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Rank IC · PSI · 半衰期 · 重训练日志
        </p>
      </div>

      {/* Rank IC Rolling Mean */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
          <Activity className="w-4 h-4 text-sky-400" />
          Rank IC 滚动均值曲线
        </h2>
        {rankICAvg.length < 2 ? (
          <div className="p-8 text-center text-zinc-500 text-sm">暂无 Rank IC 数据</div>
        ) : (
          <>
            <ZoomableSparkline
              data={rankICAvg}
              labels={rankICDates}
              color="#38bdf8"
              height={64}
              className="w-full"
            />
            <div className="flex justify-between text-[9px] text-zinc-600 mt-1">
              <span>{rankICDates[0]?.slice(5) || ""}</span>
              <span>{rankICDates[rankICDates.length - 1]?.slice(5) || ""}</span>
            </div>
            <div className="grid grid-cols-3 gap-2 mt-4">
              <div className="flex flex-col items-center p-2 rounded-lg bg-zinc-800/40">
                <span className="text-[9px] text-zinc-500">最新均值</span>
                <span className={cn(
                  "text-sm font-bold tabular-nums mt-0.5",
                  rankICAvg[rankICAvg.length - 1] > 0 ? "text-emerald-400" : "text-red-400"
                )}>
                  {rankICAvg[rankICAvg.length - 1]?.toFixed(2) || "—"}
                </span>
              </div>
              <div className="flex flex-col items-center p-2 rounded-lg bg-zinc-800/40">
                <span className="text-[9px] text-zinc-500">最大值</span>
                <span className="text-sm font-bold tabular-nums mt-0.5 text-emerald-400">
                  {Math.max(...rankICAvg).toFixed(2)}
                </span>
              </div>
              <div className="flex flex-col items-center p-2 rounded-lg bg-zinc-800/40">
                <span className="text-[9px] text-zinc-500">模型数</span>
                <span className="text-sm font-bold tabular-nums mt-0.5 text-zinc-200">
                  {new Set(rankICData.map((r) => r.model)).size}
                </span>
              </div>
            </div>
          </>
        )}
      </div>

      {/* PSI Dashboard + Half-Life */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* PSI Dashboard */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
          <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
            <Gauge className="w-4 h-4 text-amber-400" />
            PSI 仪表盘
          </h2>
          {psiMetrics.length === 0 ? (
            <div className="p-8 text-center text-zinc-500 text-sm">暂无 PSI 数据</div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {psiMetrics.map((m) => (
                <PSIGauge
                  key={m.metric}
                  label={m.metric}
                  value={Number(m.value)}
                  status={m.status || "good"}
                />
              ))}
            </div>
          )}
        </div>

        {/* Half-Life Trend */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
          <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
            <Clock className="w-4 h-4 text-purple-400" />
            半衰期趋势
          </h2>
          {halfLifeValues.length < 2 ? (
            <div className="p-8 text-center text-zinc-500 text-sm">暂无半衰期数据</div>
          ) : (
            <>
              <ZoomableSparkline
                data={halfLifeValues}
                labels={halfLifeDates}
                color="#a855f7"
                height={48}
                className="w-full"
              />
              <div className="flex justify-between text-[9px] text-zinc-600 mt-1">
                <span>{halfLifeDates[0] || ""}</span>
                <span>{halfLifeDates[halfLifeDates.length - 1] || ""}</span>
              </div>
              <div className="mt-3 flex items-center justify-center gap-2 text-[10px] text-zinc-500">
                <span>当前半衰期:</span>
                <span className="font-bold text-purple-400">
                  {halfLifeValues[halfLifeValues.length - 1]?.toFixed(0) || "—"} 天
                </span>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Retrain Trigger Log */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
          <ScrollText className="w-4 h-4 text-zinc-400" />
          重训练触发日志
        </h2>
        {retrainLogs.length === 0 ? (
          <div className="p-8 text-center text-zinc-500 text-sm">暂无训练日志</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-zinc-500 border-b border-zinc-800">
                  <th className="text-left py-2 pr-3 font-normal">日期</th>
                  <th className="text-left py-2 px-3 font-normal">模型</th>
                  <th className="text-center py-2 px-3 font-normal">触发</th>
                  <th className="text-left py-2 pl-3 font-normal">原因</th>
                </tr>
              </thead>
              <tbody>
                {retrainLogs.map((log) => (
                  <tr key={log.id} className="border-b border-zinc-800/30 hover:bg-zinc-800/20">
                    <td className="py-2 pr-3 text-zinc-400 font-mono">{log.check_date}</td>
                    <td className="py-2 px-3 text-zinc-200 font-medium">{log.model_id}</td>
                    <td className="py-2 px-3 text-center">
                      {log.triggered ? (
                        <span className="inline-flex items-center gap-1 text-amber-400">
                          <AlertTriangle className="w-3 h-3" /> 是
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-zinc-500">
                          <CheckCircle2 className="w-3 h-3" /> 否
                        </span>
                      )}
                    </td>
                    <td className="py-2 pl-3 text-zinc-500 max-w-[200px] truncate">
                      {log.trigger_reason || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-[9px] text-zinc-600 mt-2 text-center">{retrainLogs.length} 条记录</p>
          </div>
        )}
      </div>
    </div>
  );
}
