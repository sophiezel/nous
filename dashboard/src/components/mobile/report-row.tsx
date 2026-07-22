import Link from "next/link";
import { cn, formatDate, typeLabel, typeColor } from "@/lib/utils";
import { ChevronRight } from "lucide-react";

interface ReportRowProps {
  id: number;
  type: string;
  title: string;
  preview: string;
  created_at: string;
}

export function ReportRow({ id, type, title, preview, created_at }: ReportRowProps) {
  return (
    <Link
      href={`/mobile/reports/${id}`}
      className="flex items-center gap-3 p-3 rounded-xl bg-zinc-900/50 border border-zinc-800/50 active:bg-zinc-800/50 transition-colors"
    >
      {/* Type indicator */}
      <div className="shrink-0">
        <span
          className={cn(
            "block w-8 h-8 rounded-lg flex items-center justify-center text-[9px] font-bold border",
            typeColor(type)
          )}
        >
          {typeLabel(type).charAt(0)}
        </span>
      </div>

      {/* Content */}
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
        <p className="text-xs text-zinc-300 truncate">{preview || title}</p>
      </div>

      {/* Arrow */}
      <ChevronRight className="w-4 h-4 text-zinc-600 shrink-0" />
    </Link>
  );
}
