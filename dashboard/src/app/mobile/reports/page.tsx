import { getRecentReports, getReportTypes } from "@/lib/db";
import { ExpandableReport } from "@/components/mobile/expandable-report";
import { typeLabel, cn } from "@/lib/utils";
import { FileText } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 60;

export default async function MobileReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const params = await searchParams;
  const filterType = params.type || undefined;
  const reports = getRecentReports(filterType, 30);
  const types = getReportTypes();

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-bold">报告列表</h1>

      {types.length > 0 && (
        <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1 scrollbar-none">
          <a
            href={`/mobile/reports`}
            className={cn(
              "shrink-0 text-xs px-3 py-1.5 rounded-full border transition-colors",
              !filterType
                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                : "border-zinc-800 text-zinc-400 active:bg-zinc-800"
            )}
          >
            全部
          </a>
          {types.map((t) => (
            <a
              key={t}
              href={`/mobile/reports?type=${t}`}
              className={cn(
                "shrink-0 text-xs px-3 py-1.5 rounded-full border transition-colors",
                filterType === t
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : "border-zinc-800 text-zinc-400 active:bg-zinc-800"
              )}
            >
              {typeLabel(t)}
            </a>
          ))}
        </div>
      )}

      {reports.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <FileText className="w-10 h-10 text-zinc-700 mb-3" />
          <p className="text-sm text-zinc-500">暂无报告</p>
        </div>
      ) : (
        <div className="space-y-2">
          {reports.map((r) => (
            <ExpandableReport
              key={r.id}
              id={r.id}
              type={r.type}
              title={r.title || r.type}
              content={r.content}
              created_at={r.created_at}
            />
          ))}
        </div>
      )}
    </div>
  );
}
