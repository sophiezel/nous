"use client";

import { useState } from "react";
import { SparklineChart } from "./sparkline";
import { cn } from "@/lib/utils";

interface Props {
  data: number[];
  labels?: string[];
  color?: string;
  height?: number;
  className?: string;
}

const PRESETS = [
  { label: "3M", months: 3 },
  { label: "6M", months: 6 },
  { label: "1Y", months: 12 },
  { label: "2Y", months: 24 },
  { label: "全部", months: Infinity },
];

export function ZoomableSparkline({ data, labels, color = "#10b981", height = 36, className }: Props) {
  const total = data.length;
  const [selected, setSelected] = useState(2); // default: 1Y

  if (total < 2) return null;

  const months = PRESETS[selected]?.months ?? 12;
  const windowSize = Math.min(total, months === Infinity ? total : months);
  const startIdx = total - windowSize;
  const sliced = data.slice(startIdx);

  return (
    <div className={className}>
      <SparklineChart data={sliced} color={color} height={height} className="w-full" />

      {/* Preset period buttons */}
      <div className="flex justify-center gap-1 mt-1.5">
        {PRESETS.map((p, i) => (
          <button
            key={p.label}
            onClick={() => setSelected(i)}
            className={cn(
              "text-[9px] px-2 py-0.5 rounded-full transition-colors",
              i === selected
                ? "bg-zinc-700 text-zinc-200"
                : "text-zinc-500 hover:text-zinc-400"
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Date range */}
      {labels && labels.length >= total && (
        <p className="text-center text-[9px] text-zinc-600 mt-0.5">
          {labels[startIdx]?.slice(0, 7)} ~ {labels[total - 1]?.slice(0, 7)}
        </p>
      )}
    </div>
  );
}
