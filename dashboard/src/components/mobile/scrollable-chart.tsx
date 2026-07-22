"use client";

import { useState } from "react";

/**
 * ScrollableChart — 基于 CombinedChart 统一引擎的单指标+时间范围选择器
 *
 * v1.1: 重构为 CombinedChart 包装器，消除重复渲染逻辑（原193行独立实现已废弃）
 */
import { CombinedChart } from "./combined-chart";

interface TimeRange {
  label: string;
  days: number;
}

const RANGES: TimeRange[] = [
  { label: "1周", days: 5 },
  { label: "2周", days: 10 },
  { label: "1月", days: 22 },
  { label: "3月", days: 66 },
  { label: "全部", days: 0 },
];

interface ChartDataPoint {
  date: string;
  label?: string;
  values: Record<string, number>;
}

interface ScrollableChartProps {
  data: ChartDataPoint[];
  metrics: { key: string; label: string; color: string }[];
  height?: number;
  barWidth?: number; // unused, kept for backward compat
}

export function ScrollableChart({
  data,
  metrics,
  height = 120,
}: ScrollableChartProps) {
  const [selectedMetric, setSelectedMetric] = useState(metrics[0]?.key ?? "");
  const [selectedRange, setSelectedRange] = useState(1); // default 2W

  const currentMetric = metrics.find((m) => m.key === selectedMetric) ?? metrics[0];
  const windowDays = RANGES[selectedRange].days;

  return (
    <div className="space-y-2">
      {/* Metric selector */}
      <div className="flex gap-1 overflow-x-auto pb-1 -mx-1 px-1">
        {metrics.map((m) => (
          <button
            key={m.key}
            onClick={() => setSelectedMetric(m.key)}
            className={`shrink-0 px-2.5 py-1 rounded-full text-[10px] font-medium transition-colors ${
              selectedMetric === m.key
                ? "text-white"
                : "text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800"
            }`}
            style={
              selectedMetric === m.key
                ? { backgroundColor: m.color + "30", color: m.color }
                : {}
            }
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* Time range selector */}
      <div className="flex justify-center gap-1">
        {RANGES.map((r, i) => (
          <button
            key={r.label}
            onClick={() => setSelectedRange(i)}
            className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
              selectedRange === i
                ? "bg-zinc-700 text-zinc-200"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
          >
            {r.label}
          </button>
        ))}
      </div>

      {/* Delegate to CombinedChart — it handles its own scroll */}
      <CombinedChart
        data={data}
        metrics={[currentMetric]}
        height={height}
        windowDays={windowDays}
        formatValue={(v) =>
          v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)
        }
      />
    </div>
  );
}
