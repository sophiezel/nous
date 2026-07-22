"use client";

import { cn, formatDate } from "@/lib/utils";
import { Bell, ArrowLeft, AlertTriangle, TrendingUp, Info } from "lucide-react";
import Link from "next/link";
import type { Message } from "@/lib/types";

interface Props {
  message: Message;
}

function typeIcon(type: string) {
  switch (type) {
    case "alert": return <AlertTriangle className="w-4 h-4 text-rose-400" />;
    case "signal": return <TrendingUp className="w-4 h-4 text-emerald-400" />;
    default: return <Info className="w-4 h-4 text-blue-400" />;
  }
}

function typeLabel(type: string): string {
  switch (type) {
    case "alert": return "告警";
    case "signal": return "交易信号";
    case "system": return "系统消息";
    default: return type;
  }
}

export function MessageDetailClient({ message }: Props) {
  return (
    <div className="space-y-3 pb-2">
      {/* Back button */}
      <Link
        href="/mobile/messages"
        className="inline-flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-300 transition-colors"
      >
        <ArrowLeft className="w-3.5 h-3.5" />
        返回消息列表
      </Link>

      {/* Header */}
      <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/30 p-4">
        <div className="flex items-start gap-3">
          <div className="mt-0.5">{typeIcon(message.type)}</div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <h1 className="text-sm font-bold text-zinc-100">{message.title}</h1>
              <span className={cn(
                "text-[9px] px-1.5 py-0.5 rounded font-medium",
                message.type === "alert" ? "bg-rose-500/10 text-rose-400" :
                message.type === "signal" ? "bg-emerald-500/10 text-emerald-400" :
                "bg-blue-500/10 text-blue-400"
              )}>
                {typeLabel(message.type)}
              </span>
            </div>
            <p className="text-[10px] text-zinc-500 tabular-nums">
              {formatDate(message.created_at)}
            </p>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/30 p-4">
        <div className="prose prose-invert prose-xs max-w-none text-[11px] text-zinc-300 leading-relaxed whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    </div>
  );
}
