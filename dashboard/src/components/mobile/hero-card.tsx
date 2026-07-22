"use client";

import { MacroRing } from "./macro-ring";
import { cn } from "@/lib/utils";

interface HeroCardProps {
  macroScore: number | null;
  macroPosition: number | null;
  sentimentScore: number | null;
  sentimentLabel?: string;
  date?: string;
}

export function HeroCard({
  macroScore,
  macroPosition,
  sentimentScore,
  sentimentLabel,
  date,
}: HeroCardProps) {
  const sentimentColor =
    sentimentScore !== null && sentimentScore >= 60
      ? "#10b981"
      : sentimentScore !== null && sentimentScore >= 40
        ? "#f59e0b"
        : "#ef4444";

  return (
    <div className="relative overflow-hidden rounded-[20px] bg-gradient-to-br from-emerald-500/15 via-zinc-900 to-zinc-950 border border-zinc-800/80">
      {/* Glow effects */}
      <div className="absolute -top-20 -right-20 w-48 h-48 bg-emerald-500/10 rounded-full blur-3xl" />
      <div className="absolute -bottom-10 -left-10 w-32 h-32 bg-amber-500/5 rounded-full blur-2xl" />

      <div className="relative p-5">
        {/* Header row */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <h1 className="text-lg font-bold tracking-tight">天工</h1>
            <p className="text-[11px] text-zinc-500 mt-0.5">
              {date || new Date().toLocaleDateString("zh-CN", {
                year: "numeric",
                month: "long",
                day: "numeric",
                weekday: "short",
              })}
            </p>
          </div>
          <div className="flex items-center gap-1 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-[10px] font-medium text-emerald-400">实时</span>
          </div>
        </div>

        {/* Ring gauges row */}
        <div className="flex items-center justify-center gap-8 mb-5">
          {/* Macro ring */}
          {macroScore !== null && (
            <div className="flex flex-col items-center gap-2">
              <div className="relative">
                <MacroRing score={macroScore} size={96} strokeWidth={7} />
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className="text-2xl font-bold tabular-nums text-emerald-400">
                    {macroScore.toFixed(0)}
                  </span>
                </div>
              </div>
              <span className="text-[10px] text-zinc-500 font-medium tracking-wider uppercase">
                宏观评分
              </span>
            </div>
          )}

          {/* Divider */}
          {macroScore !== null && sentimentScore !== null && (
            <div className="w-px h-20 bg-zinc-800" />
          )}

          {/* Sentiment ring */}
          {sentimentScore !== null && (
            <div className="flex flex-col items-center gap-2">
              <div className="relative">
                <MacroRing
                  score={sentimentScore}
                  size={96}
                  strokeWidth={7}
                  color={sentimentColor}
                />
                <div className="absolute inset-0 flex items-center justify-center">
                  <span
                    className="text-2xl font-bold tabular-nums"
                    style={{ color: sentimentColor }}
                  >
                    {sentimentScore}
                  </span>
                </div>
              </div>
              <span className="text-[10px] text-zinc-500 font-medium tracking-wider uppercase">
                市场情绪
              </span>
            </div>
          )}
        </div>

        {/* Position bar */}
        {macroPosition !== null && (
          <div className="space-y-1.5">
            <div className="flex justify-between text-[10px] text-zinc-600">
              <span>空仓 0%</span>
              <span>半仓 50%</span>
              <span>满仓 100%</span>
            </div>
            <div className="relative h-3 bg-zinc-800/80 rounded-full overflow-hidden">
              {/* Track */}
              <div className="absolute inset-0 flex">
                <div className="flex-1 border-r border-zinc-700/50" />
                <div className="flex-1" />
              </div>
              {/* Fill */}
              <div
                className="h-full bg-gradient-to-r from-emerald-500 via-emerald-400 to-amber-400 rounded-full transition-all duration-700 ease-out"
                style={{ width: `${macroPosition * 100}%` }}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-zinc-500">
                建议仓位
              </span>
              <span className="text-xs font-bold tabular-nums text-amber-400">
                {(macroPosition * 100).toFixed(0)}%
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
