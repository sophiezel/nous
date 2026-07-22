"use client";

import { useState } from "react";
import Link from "next/link";
import { cn, formatDate, typeLabel, typeColor } from "@/lib/utils";
import { ChevronRight, ChevronDown } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface ExpandableReportProps {
  id: number;
  type: string;
  title: string;
  content: string;
  created_at: string;
}

export function ExpandableReport({
  id,
  type,
  title,
  content,
  created_at,
}: ExpandableReportProps) {
  const [expanded, setExpanded] = useState(false);

  const preview = content.substring(0, 100).replace(/[#*`\n]/g, " ");

  return (
    <div
      className={cn(
        "rounded-xl border transition-all duration-200",
        expanded
          ? "border-zinc-700 bg-zinc-900/80"
          : "border-zinc-800/50 bg-zinc-900/30 active:bg-zinc-800/50"
      )}
    >
      {/* Header — always visible, tap to expand */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 p-3 text-left"
      >
        <div className="shrink-0">
          <span
            className={cn(
              "flex items-center justify-center w-9 h-9 rounded-xl text-[10px] font-bold border",
              typeColor(type)
            )}
          >
            {typeLabel(type).charAt(0)}
          </span>
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span
              className={cn(
                "text-[9px] font-medium px-1.5 py-0.5 rounded",
                typeColor(type)
              )}
            >
              {typeLabel(type)}
            </span>
            <span className="text-[10px] text-zinc-600 tabular-nums">
              {formatDate(created_at).split(" ")[0]}
            </span>
          </div>
          <p className="text-xs text-zinc-300 truncate">
            {expanded ? title : (preview || title)}
          </p>
        </div>

        <span className="shrink-0 text-zinc-600">
          {expanded ? (
            <ChevronDown className="w-4 h-4" />
          ) : (
            <ChevronRight className="w-4 h-4" />
          )}
        </span>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-zinc-800/50 pt-3 animate-in fade-in slide-in-from-top-1 duration-200">
          <div
            className="prose prose-invert prose-sm max-w-none
              prose-headings:text-zinc-100 prose-headings:text-sm
              prose-p:text-xs prose-p:text-zinc-300 prose-p:leading-relaxed
              prose-strong:text-zinc-200
              prose-code:text-emerald-400 prose-code:bg-zinc-800 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-[11px]
              prose-pre:bg-zinc-800 prose-pre:border prose-pre:border-zinc-700 prose-pre:text-[11px]
              prose-a:text-emerald-400
              prose-li:text-xs prose-li:text-zinc-300
              [&_table]:w-full [&_table]:text-[11px]
              [&_th]:border [&_th]:border-zinc-700 [&_th]:px-2 [&_th]:py-1.5
              [&_td]:border [&_td]:border-zinc-700 [&_td]:px-2 [&_td]:py-1.5
            "
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {content}
            </ReactMarkdown>
          </div>

          {/* Deep link */}
          <Link
            href={`/mobile/reports/${id}`}
            className="inline-flex items-center gap-1 mt-2 text-[10px] text-emerald-400/70 hover:text-emerald-400"
          >
            在新页面打开
            <ChevronRight className="w-3 h-3" />
          </Link>
        </div>
      )}
    </div>
  );
}
