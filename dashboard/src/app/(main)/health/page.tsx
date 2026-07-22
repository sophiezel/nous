import { getRecentReports, getWeixinChannelHealth } from "@/lib/db";
import { formatDate, typeLabel, typeColor } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { Heart, CheckCircle, AlertTriangle, XCircle, MessageCircle } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 60;

function HealthStatus({ report }: { report: { content: string; created_at: string } | null }) {
  if (!report) {
    return (
      <div className="flex items-center gap-2 text-amber-400">
        <AlertTriangle className="w-5 h-5" />
        <span className="text-sm font-medium">等待首次数据健康检查...</span>
      </div>
    );
  }

  const isOk = report.content.includes("✅") || report.content.toLowerCase().includes("ok");
  const hasWarn = report.content.includes("⚠️") || report.content.includes("告警");

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {isOk && !hasWarn ? (
          <CheckCircle className="w-5 h-5 text-emerald-400" />
        ) : hasWarn ? (
          <AlertTriangle className="w-5 h-5 text-amber-400" />
        ) : (
          <XCircle className="w-5 h-5 text-red-400" />
        )}
        <span className="text-sm font-medium">
          {isOk && !hasWarn
            ? "系统正常"
            : hasWarn
            ? "有告警"
            : "异常"}
        </span>
        <span className="text-xs text-zinc-500">
          {formatDate(report.created_at)}
        </span>
      </div>
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/80 p-4">
        <pre className="text-xs text-zinc-400 whitespace-pre-wrap font-mono">
          {report.content.substring(0, 2000)}
        </pre>
      </div>
    </div>
  );
}

export default function HealthPage() {
  const healthReports = getRecentReports("health_check", 5);
  const latestHealth = healthReports.length > 0 ? healthReports[0] : null;
  const weixin = getWeixinChannelHealth();

  // Get all report types and their latest timestamps
  const allRecent = getRecentReports(undefined, 100);
  const latestByType = new Map<string, string>();
  for (const r of allRecent) {
    if (!latestByType.has(r.type) || r.created_at > latestByType.get(r.type)!) {
      latestByType.set(r.type, r.created_at);
    }
  }

  const now = new Date();
  const typeEntries = Array.from(latestByType.entries())
    .filter(([t]) => t !== "health_check" && t !== "test")
    .sort((a, b) => b[1].localeCompare(a[1]));

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">系统健康</h1>
        <p className="text-sm text-zinc-500 mt-1">
          数据新鲜度 · Cron 执行状态
        </p>
      </div>

      {/* WeChat Channel Status */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
          <MessageCircle className="w-4 h-4 text-green-400" />
          微信推送通道
        </h2>
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            {weixin.state === "healthy" ? (
              <CheckCircle className="w-5 h-5 text-emerald-400" />
            ) : weixin.state === "degraded" ? (
              <XCircle className="w-5 h-5 text-red-400" />
            ) : weixin.state === "partial" ? (
              <AlertTriangle className="w-5 h-5 text-amber-400" />
            ) : (
              <AlertTriangle className="w-5 h-5 text-zinc-500" />
            )}
            <span className="text-sm font-medium">
              {weixin.state === "healthy" && "正常"}
              {weixin.state === "degraded" && "中断"}
              {weixin.state === "partial" && "部分失败"}
              {weixin.state === "idle" && "待首次消息"}
              {weixin.state === "error" && "查询失败"}
              {weixin.state === "unknown" && "未知"}
            </span>
          </div>
          <div className="grid grid-cols-3 gap-3 text-center">
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/80 p-2">
              <div className="text-lg font-bold text-zinc-200">{weixin.today_total}</div>
              <div className="text-[10px] text-zinc-500">今日消息</div>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/80 p-2">
              <div className="text-lg font-bold text-emerald-400">{weixin.today_pushed}</div>
              <div className="text-[10px] text-zinc-500">已推送</div>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/80 p-2">
              <div className={cn(
                "text-lg font-bold",
                weixin.today_failed > 0 ? "text-red-400" : "text-zinc-200"
              )}>{weixin.today_failed}</div>
              <div className="text-[10px] text-zinc-500">未推送</div>
            </div>
          </div>
          {weixin.state === "degraded" && weixin.last_fail && (
            <p className="text-xs text-red-400/80">
              自 {formatDate(weixin.last_fail)} 起所有消息未推送 — Dashboard可正常查看
            </p>
          )}
          {weixin.state === "partial" && (
            <p className="text-xs text-amber-400/80">
              {weixin.today_total - weixin.today_pushed}条消息未推送 — 可能触发iLink限流
            </p>
          )}
        </div>
      </div>

      {/* Main Status */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
          <Heart className="w-4 h-4 text-rose-400" />
          综合状态
        </h2>
        <HealthStatus
          report={
            latestHealth
              ? { content: latestHealth.content, created_at: latestHealth.created_at }
              : null
          }
        />
      </div>

      {/* Cron Status */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4">
          数据通道状态
        </h2>
        {typeEntries.length === 0 ? (
          <p className="text-sm text-zinc-500">暂无数据通道记录</p>
        ) : (
          <div className="space-y-1.5">
            {typeEntries.map(([type, latestTime]) => {
              const age =
                (now.getTime() - new Date(latestTime).getTime()) / (1000 * 60 * 60);
              const isFresh = age < 24;
              const isRecent = age < 48;
              return (
                <div
                  key={type}
                  className="flex items-center justify-between py-2 px-3 rounded-lg border border-zinc-800"
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "text-[10px] font-medium px-2 py-0.5 rounded border",
                        typeColor(type)
                      )}
                    >
                      {typeLabel(type)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-zinc-500">{formatDate(latestTime)}</span>
                    <span
                      className={cn(
                        "w-2 h-2 rounded-full",
                        isFresh
                          ? "bg-emerald-500"
                          : isRecent
                          ? "bg-amber-500"
                          : "bg-red-500"
                      )}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Recent Health Checks */}
      {healthReports.length > 1 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
          <h2 className="text-sm font-semibold text-zinc-300 mb-4">
            历史健康检查
          </h2>
          <div className="space-y-2">
            {healthReports.slice(1).map((r) => (
              <details
                key={r.id}
                className="rounded-lg border border-zinc-800 bg-zinc-900/80"
              >
                <summary className="px-4 py-2.5 text-sm text-zinc-400 cursor-pointer hover:text-zinc-200">
                  {formatDate(r.created_at)}
                </summary>
                <pre className="px-4 pb-3 text-xs text-zinc-500 whitespace-pre-wrap font-mono">
                  {r.content.substring(0, 1500)}
                </pre>
              </details>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
