"use client";

import { ModuleCard } from "@/components/mobile/module-card";
import { cn, formatDate } from "@/lib/utils";
import { Bell, Mail, MailOpen, AlertTriangle, TrendingUp, Info } from "lucide-react";
import Link from "next/link";
import type { Message } from "@/lib/types";

interface Props {
  messages: Message[];
}

function typeIcon(type: string) {
  switch (type) {
    case "alert": return <AlertTriangle className="w-3 h-3 text-rose-400" />;
    case "signal": return <TrendingUp className="w-3 h-3 text-emerald-400" />;
    default: return <Info className="w-3 h-3 text-blue-400" />;
  }
}

function typeColor(type: string): string {
  switch (type) {
    case "alert": return "border-rose-500/20 bg-rose-500/5";
    case "signal": return "border-emerald-500/20 bg-emerald-500/5";
    default: return "border-blue-500/20 bg-blue-500/5";
  }
}

export function MessagesListClient({ messages }: Props) {
  const unreadCount = messages.filter(m => !m.is_read).length;

  return (
    <div className="space-y-3 pb-2">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <Bell className="w-4 h-4 text-emerald-400" />
          <h1 className="text-sm font-bold text-zinc-100">消息</h1>
        </div>
        {unreadCount > 0 && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400">
            {unreadCount} 条未读
          </span>
        )}
      </div>

      {messages.length === 0 ? (
        <div className="rounded-2xl border border-zinc-800/80 p-6 text-center">
          <Bell className="w-8 h-8 text-zinc-700 mx-auto mb-2" />
          <p className="text-[10px] text-zinc-500">暂无消息</p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {messages.map((msg) => (
            <Link
              key={msg.id}
              href={`/mobile/messages/${msg.id}`}
              className={cn(
                "block rounded-xl border p-3 transition-all hover:bg-zinc-800/30",
                msg.is_read
                  ? "border-zinc-800/50 bg-zinc-900/20"
                  : "border-emerald-500/15 bg-emerald-500/[0.02]",
                typeColor(msg.type)
              )}
            >
              <div className="flex items-start gap-2.5">
                <div className="mt-0.5">{typeIcon(msg.type)}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={cn(
                      "text-xs font-medium",
                      msg.is_read ? "text-zinc-300" : "text-zinc-100"
                    )}>
                      {msg.title}
                    </span>
                    {!msg.is_read && (
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
                    )}
                  </div>
                  <p className="text-[10px] text-zinc-500 mt-1 line-clamp-2">
                    {msg.content?.substring(0, 120)}
                  </p>
                  <p className="text-[9px] text-zinc-600 mt-1 tabular-nums">
                    {formatDate(msg.created_at)}
                  </p>
                </div>
                {msg.is_read ? (
                  <MailOpen className="w-3 h-3 text-zinc-600 mt-1 shrink-0" />
                ) : (
                  <Mail className="w-3 h-3 text-emerald-400/70 mt-1 shrink-0" />
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
