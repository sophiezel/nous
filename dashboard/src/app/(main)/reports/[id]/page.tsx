import { getReportById } from "@/lib/db";
import { typeLabel, typeColor, formatDate } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { notFound } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, FileText } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export const dynamic = "force-dynamic";

export default async function ReportDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const report = getReportById(parseInt(id));

  if (!report) {
    notFound();
  }

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      {/* Back */}
      <Link
        href="/reports"
        className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
      >
        <ArrowLeft className="w-3 h-3" />
        返回列表
      </Link>

      {/* Header */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <div className="flex items-center gap-3 mb-3">
          <span
            className={cn(
              "text-xs font-medium px-2.5 py-1 rounded border",
              typeColor(report.type)
            )}
          >
            {typeLabel(report.type)}
          </span>
          <span className="text-xs text-zinc-500">
            {formatDate(report.created_at)}
          </span>
        </div>
        <h1 className="text-lg font-bold">
          {report.title || typeLabel(report.type)}
        </h1>
      </div>

      {/* Content */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        {report.content ? (
          <div className="prose prose-invert prose-sm max-w-none
            prose-headings:text-zinc-100
            prose-p:text-zinc-300
            prose-strong:text-zinc-200
            prose-code:text-emerald-400 prose-code:bg-zinc-800 prose-code:px-1 prose-code:py-0.5 prose-code:rounded
            prose-pre:bg-zinc-800 prose-pre:border prose-pre:border-zinc-700
            prose-a:text-emerald-400
            prose-li:text-zinc-300
            prose-table:border-zinc-700
            prose-th:text-zinc-300 prose-th:bg-zinc-800
            prose-td:text-zinc-400 prose-td:border-zinc-700
            [&_table]:w-full [&_table]:border-collapse
            [&_th]:border [&_th]:border-zinc-700 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left
            [&_td]:border [&_td]:border-zinc-700 [&_td]:px-3 [&_td]:py-2
          ">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {report.content}
            </ReactMarkdown>
          </div>
        ) : (
          <p className="text-zinc-500 text-sm">(空内容)</p>
        )}
      </div>
    </div>
  );
}
