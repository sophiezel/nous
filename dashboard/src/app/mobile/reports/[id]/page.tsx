import { getReportById } from "@/lib/db";
import { typeLabel, typeColor, formatDate } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { notFound } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export const dynamic = "force-dynamic";

export default async function MobileReportDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const report = getReportById(parseInt(id));

  if (!report) notFound();

  return (
    <div className="space-y-3 pb-4">
      {/* Back */}
      <Link
        href="/mobile/reports"
        className="inline-flex items-center gap-1 text-xs text-zinc-500 active:text-zinc-300 py-1"
      >
        <ArrowLeft className="w-3.5 h-3.5" />
        返回
      </Link>

      {/* Header */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 mb-2">
          <span className={cn("text-[10px] font-medium px-2 py-0.5 rounded border", typeColor(report.type))}>
            {typeLabel(report.type)}
          </span>
          <span className="text-[10px] text-zinc-500">{formatDate(report.created_at)}</span>
        </div>
        <h1 className="text-base font-bold">{report.title || typeLabel(report.type)}</h1>
      </div>

      {/* Content */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="prose prose-invert prose-sm max-w-none
          prose-headings:text-zinc-100 prose-headings:text-sm
          prose-p:text-xs prose-p:text-zinc-300 prose-p:leading-relaxed
          prose-strong:text-zinc-200
          prose-code:text-emerald-400 prose-code:bg-zinc-800 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-[11px]
          prose-pre:bg-zinc-800 prose-pre:border prose-pre:border-zinc-700 prose-pre:text-[11px]
          prose-a:text-emerald-400
          prose-li:text-xs prose-li:text-zinc-300
          [&_table]:w-full [&_table]:text-[11px]
          [&_th]:border [&_th]:border-zinc-700 [&_th]:px-2 [&_th]:py-1.5 [&_th]:text-left
          [&_td]:border [&_td]:border-zinc-700 [&_td]:px-2 [&_td]:py-1.5
        ">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {report.content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
