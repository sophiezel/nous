import { getRecentReports, getReportTypes } from "@/lib/db";
import { ReportCard } from "@/components/report-card";
import { typeLabel } from "@/lib/utils";
import { FileText } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 60;

export default async function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const params = await searchParams;
  const filterType = params.type || undefined;
  const reports = (getRecentReports(filterType, 40)).map((r) => ({
    id: r.id,
    type: r.type,
    title: r.title,
    preview: r.content.substring(0, 200).replace(/[#*`\n]/g, " ").trim(),
    created_at: r.created_at,
  }));
  const types = getReportTypes();

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight">报告列表</h1>
        <p className="text-sm text-zinc-500 mt-1">
          所有投研推送报告 · {reports.length} 条
        </p>
      </div>

      {/* Type Filter */}
      {types.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <a
            href="/reports"
            className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
              !filterType
                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                : "border-zinc-800 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
            }`}
          >
            全部
          </a>
          {types.map((t) => (
            <a
              key={t}
              href={`/reports?type=${t}`}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                filterType === t
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : "border-zinc-800 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
              }`}
            >
              {typeLabel(t)}
            </a>
          ))}
        </div>
      )}

      {/* Reports */}
      {reports.length === 0 ? (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-12 text-center">
          <FileText className="w-10 h-10 text-zinc-600 mx-auto mb-3" />
          <p className="text-sm text-zinc-500">暂无报告</p>
          <p className="text-xs text-zinc-600 mt-1">
            {filterType
              ? `没有类型为 "${typeLabel(filterType)}" 的报告`
              : "等待 cron 任务推送数据..."}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {reports.map((r) => (
            <ReportCard key={r.id} {...r} />
          ))}
        </div>
      )}
    </div>
  );
}
