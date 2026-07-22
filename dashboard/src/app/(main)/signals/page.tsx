import { getRecentReports } from "@/lib/db";
import { ReportCard } from "@/components/report-card";
import { Zap } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 60;

const SIGNAL_TYPES = [
  "daily_picks",
  "buy_signals",
  "ai_signals",
  "power_signals",
  "risk_alert",
];

export default function SignalsPage() {
  // Get latest report for each signal type
  const allReports = getRecentReports(undefined, 60);
  const signalReports = allReports
    .filter((r) => SIGNAL_TYPES.includes(r.type))
    .slice(0, 20)
    .map((r) => ({
      id: r.id,
      type: r.type,
      title: r.title,
      preview: r.content.substring(0, 200).replace(/[#*`\n]/g, " ").trim(),
      created_at: r.created_at,
    }));

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">交易信号</h1>
        <p className="text-sm text-zinc-500 mt-1">
          荐股 · 买入信号 · 风控告警 · {signalReports.length} 条
        </p>
      </div>

      {signalReports.length === 0 ? (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-12 text-center">
          <Zap className="w-10 h-10 text-zinc-600 mx-auto mb-3" />
          <p className="text-sm text-zinc-500">暂无交易信号</p>
          <p className="text-xs text-zinc-600 mt-1">
            等待荐股/买入 cron 推送数据...
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {signalReports.map((r) => (
            <ReportCard key={r.id} {...r} />
          ))}
        </div>
      )}
    </div>
  );
}
