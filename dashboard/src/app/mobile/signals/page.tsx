import { getRecentReports } from "@/lib/db";
import { ReportRow } from "@/components/mobile/report-row";
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

export default function MobileSignalsPage() {
  const allReports = getRecentReports(undefined, 60);
  const signalReports = allReports
    .filter((r) => SIGNAL_TYPES.includes(r.type))
    .slice(0, 20);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-bold">交易信号</h1>

      {signalReports.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Zap className="w-10 h-10 text-zinc-700 mb-3" />
          <p className="text-sm text-zinc-500">暂无交易信号</p>
          <p className="text-xs text-zinc-600 mt-1">等待荐股/买入cron推送数据</p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {signalReports.map((r) => (
            <ReportRow
              key={r.id}
              id={r.id}
              type={r.type}
              title={r.title || r.type}
              preview={r.content.substring(0, 80).replace(/[#*`\n]/g, " ")}
              created_at={r.created_at}
            />
          ))}
        </div>
      )}
    </div>
  );
}
