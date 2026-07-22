import Link from "next/link";
import { cn, formatDate, typeLabel, typeColor } from "@/lib/utils";

interface ReportCardProps {
  id: number;
  type: string;
  title: string | null;
  preview: string;
  created_at: string;
}

export function ReportCard({
  id,
  type,
  title,
  preview,
  created_at,
}: ReportCardProps) {
  return (
    <Link
      href={`/reports/${id}`}
      className="block rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 hover:border-zinc-700 hover:bg-zinc-900/80 transition-colors"
    >
      <div className="flex items-center gap-2 mb-2">
        <span
          className={cn(
            "text-[10px] font-medium px-2 py-0.5 rounded border",
            typeColor(type)
          )}
        >
          {typeLabel(type)}
        </span>
        <span className="text-xs text-zinc-500">{formatDate(created_at)}</span>
      </div>
      <h3 className="text-sm font-medium text-zinc-200 mb-1">
        {title || typeLabel(type)}
      </h3>
      <p className="text-xs text-zinc-500 line-clamp-2">{preview}</p>
    </Link>
  );
}
