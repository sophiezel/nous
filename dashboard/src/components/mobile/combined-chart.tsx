"use client";

import { useState, useRef, useEffect, useCallback } from "react";

interface Metric {
  key: string;
  label: string;
  color: string;
}

interface ChartDataPoint {
  date: string;
  values: Record<string, number>;
}

interface CombinedChartProps {
  data: ChartDataPoint[];
  metrics: Metric[];
  height?: number;
  windowDays: number;
  customStart?: string;
  customEnd?: string;
  onLoadMore?: () => void;
  onWindowChange?: (days: number) => void;
  onVisibleRangeChange?: (firstIdx: number, lastIdx: number, total: number) => void;
  formatValue?: (v: number, metricKey: string) => string;
}

const WINDOWS = [
  { label: "1周", days: 5 },
  { label: "2周", days: 10 },
  { label: "1月", days: 22 },
  { label: "3月", days: 66 },
  { label: "全部", days: 0 },
];

/** Compute bar width so bars fill the viewport for small datasets, shrink + scroll for large */
function calcBarWidth(dataCount: number, metricCount: number, viewW: number): number {
  if (viewW <= 0) viewW = 343; // default phone width
  const groupGap = 4; // gap between date groups
  const metricGap = 2; // gap between bars of different metrics in same group
  const groupsCanFit = viewW / 50; // target ~50px per group minimum
  if (dataCount <= groupsCanFit) {
    // Few data points — make bars wider to fill viewport
    const groupWidth = viewW / dataCount - groupGap;
    return Math.max(4, Math.min(24, (groupWidth - metricGap * (metricCount - 1)) / metricCount));
  }
  // Many data points — compact bars, enable horizontal scroll
  const baseWidth = Math.max(3, (viewW / dataCount - groupGap - metricGap * (metricCount - 1)) / metricCount);
  return Math.max(3, Math.min(14, baseWidth));
}

export function CombinedChart({
  data,
  metrics,
  height = 100,
  windowDays,
  customStart,
  customEnd,
  onLoadMore,
  onWindowChange,
  onVisibleRangeChange,
  formatValue,
}: CombinedChartProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerW, setContainerW] = useState(343);
  const [hasScrolledNearEnd, setHasScrolledNearEnd] = useState(false);

  // Measure container width
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setContainerW(entry.contentRect.width);
    });
    obs.observe(el);
    setContainerW(el.clientWidth);
    return () => obs.disconnect();
  }, []);

  // Filter data by window or custom range
  let visibleData: ChartDataPoint[];
  if (customStart && customEnd) {
    visibleData = data.filter((d) => d.date >= customStart && d.date <= customEnd);
  } else if (windowDays > 0) {
    visibleData = data.slice(-windowDays);
  } else {
    visibleData = data;
  }

  const firstDate = visibleData[0]?.date ?? "";
  const lastDate = visibleData[visibleData.length - 1]?.date ?? "";
  // Year prefix: show when visible window spans multiple years or isn't current year
  const currentYear = new Date().getFullYear().toString();
  const visibleYears = new Set(visibleData.map((d) => d.date.slice(0, 4)));
  const needsYear = !visibleYears.has(currentYear) || visibleYears.size > 1;

  const fmtDate = (d: string) => {
    if (!d || d.length < 10) return d;
    const parts = d.split("-");
    if (parts.length < 3) return d;
    return needsYear ? `${parts[0].slice(2)}-${parts[1]}-${parts[2]}` : `${parts[1]}-${parts[2]}`;
  };

  // Dynamic bar width
  const barW = calcBarWidth(visibleData.length, metrics.length, containerW);
  const groupGap = 4;
  const metricGap = 2;
  const groupWidth = barW * metrics.length + metricGap * (metrics.length - 1);

  // Compute bar X positions accounting for date gaps (holidays/weekends)
  const barPositions: { x: number; gapDays: number }[] = [];
  let cumX = 8;
  for (let i = 0; i < visibleData.length; i++) {
    let gapDays = 0;
    if (i > 0) {
      const prev = new Date(visibleData[i - 1].date);
      const curr = new Date(visibleData[i].date);
      gapDays = Math.round((curr.getTime() - prev.getTime()) / 86400000) - 1;
    }
    // Extra padding for gaps
    // 0-2 days: normal weekend, no marker
    // 3-15 days: holiday, show "N天休"
    // >15 days: data gap, show "数据缺失"
    const extraGap = gapDays > 2 ? Math.min(gapDays * 0.8, 30) : 0;
    cumX += extraGap;
    barPositions.push({ x: cumX, gapDays });
    cumX += groupWidth + groupGap;
  }
  const chartW = Math.max(cumX, containerW);
  const barAreaH = height - 18;

  const allValues = visibleData.flatMap((d) => metrics.map((m) => d.values[m.key] ?? 0));
  const dataMax = Math.max(...allValues, 1);

  // X-axis label interval: one label per ~60px of chart width, min 7 labels
  const desiredLabels = Math.max(7, Math.round(chartW / 60));
  const xLabelInterval = Math.max(1, Math.ceil(visibleData.length / desiredLabels));

  // Pre-compute which indices get labels
  const labelIndices = new Set<number>();
  for (let i = 0; i < visibleData.length; i += xLabelInterval) {
    labelIndices.add(i);
  }
  // Always include last
  if (visibleData.length > 1) labelIndices.add(visibleData.length - 1);

  // Notify parent of visible range on scroll
  const reportVisible = useCallback(() => {
    if (!scrollRef.current || !onVisibleRangeChange) return;
    const { scrollLeft, clientWidth } = scrollRef.current;
    const totalW = visibleData.length * (groupWidth + groupGap);
    if (totalW <= 0) return;
    const firstIdx = Math.max(0, Math.floor((scrollLeft / totalW) * visibleData.length));
    const lastIdx = Math.min(
      visibleData.length - 1,
      Math.floor(((scrollLeft + clientWidth) / totalW) * visibleData.length)
    );
    onVisibleRangeChange(firstIdx, lastIdx, visibleData.length);
  }, [visibleData, groupWidth, groupGap, onVisibleRangeChange]);

  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return;
    reportVisible();
    const { scrollLeft, scrollWidth, clientWidth } = scrollRef.current;
    if (scrollWidth - scrollLeft - clientWidth < 150 && !hasScrolledNearEnd) {
      setHasScrolledNearEnd(true);
      onLoadMore?.();
      setTimeout(() => setHasScrolledNearEnd(false), 1000);
    }
  }, [hasScrolledNearEnd, onLoadMore, reportVisible]);

  // Initial visible range
  useEffect(() => {
    reportVisible();
  }, [reportVisible]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollLeft = scrollRef.current.scrollWidth;
    }
  }, [windowDays, data.length]);

  return (
    <div className="space-y-1.5">
      {/* Legend */}
      <div className="flex items-center gap-3">
        {metrics.map((m) => (
          <div key={m.key} className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: m.color }} />
            <span className="text-[9px] text-zinc-400">{m.label}</span>
          </div>
        ))}
      </div>

      {/* Chart */}
      <div
        ref={containerRef}
        className="overflow-x-auto scrollbar-hide"
        style={{ WebkitOverflowScrolling: "touch" }}
        onScroll={handleScroll}
      >
        <svg
          width={chartW}
          height={height}
          viewBox={`0 0 ${chartW} ${height}`}
          style={{ display: "block" }}
        >
          {/* Grid lines */}
          {[0, 0.33, 0.66, 1].map((ratio) => {
            const y = barAreaH * (1 - ratio);
            const val = dataMax * ratio;
            return (
              <g key={ratio}>
                <line x1={0} y1={y} x2={chartW} y2={y} stroke="#27272a" strokeWidth="0.5" />
                <text x={chartW - 4} y={y + 3} textAnchor="end" className="text-[7px]" fill="#52525b">
                  {val >= 100 ? val.toFixed(0) : val.toFixed(1)}
                </text>
              </g>
            );
          })}

          {/* Bars */}
          {visibleData.map((d, i) => {
            const gx = barPositions[i].x;
            const gapDays = barPositions[i].gapDays;

            return (
              <g key={i}>
                {/* Gap marker */}
                {gapDays > 2 && (
                  <>
                    <line x1={gx - 8} y1={4} x2={gx - 2} y2={4}
                      stroke={gapDays > 15 ? "#ef4444" : "#f59e0b"} strokeWidth="1.5" strokeDasharray="3 2" />
                    <text x={gx - 5} y={10} textAnchor="middle"
                      className="text-[6px]" fill={gapDays > 15 ? "#ef4444" : "#f59e0b"}>
                      {gapDays > 15 ? "缺" : gapDays + "天休"}
                    </text>
                  </>
                )}
                {metrics.map((m, mi) => {
                  const v = d.values[m.key] ?? 0;
                  const h = Math.max(1, (v / dataMax) * barAreaH);
                  const bx = gx + mi * (barW + metricGap);
                  const by = barAreaH - h;
                  return (
                    <g key={m.key}>
                      <rect x={bx} y={by} width={barW} height={h} fill={m.color} rx={1} opacity={0.85} />
                      {/* Value label — show when bars are wide enough */}
                      {barW >= 6 && h > 8 && (
                        <text
                          x={bx + barW / 2}
                          y={by + h / 2 + 3}
                          textAnchor="middle"
                          className="text-[7px] font-bold"
                          fill="white"
                          style={{ textShadow: "0 1px 2px rgba(0,0,0,0.8)" }}
                        >
                          {formatValue ? formatValue(v, m.key) : v >= 100 ? v.toFixed(0) : v.toFixed(1)}
                        </text>
                      )}
                      {/* Top label when bar is short */}
                      {barW >= 6 && h <= 8 && v > 0 && (
                        <text x={bx + barW / 2} y={by - 2} textAnchor="middle" className="text-[7px]" fill={m.color}>
                          {formatValue ? formatValue(v, m.key) : v.toFixed(0)}
                        </text>
                      )}
                    </g>
                  );
                })}

                {/* X-axis label — anchor start for first, end for last to prevent clip */}
                {labelIndices.has(i) && (
                  <text
                    x={i === 0 ? gx : i === visibleData.length - 1 ? gx + groupWidth : gx + groupWidth / 2}
                    y={height - 2}
                    textAnchor={i === 0 ? "start" : i === visibleData.length - 1 ? "end" : "middle"}
                    className="text-[7px]"
                    fill="#71717a"
                  >
                    {fmtDate(d.date)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

/** Quick single-metric sparkline for compact display */
export function MiniSparkline({
  data,
  color = "#10b981",
  width = 150,
  height = 32,
  label,
  value,
}: {
  data: number[];
  color?: string;
  width?: number;
  height?: number;
  label: string;
  value: string;
}) {
  if (data.length < 2) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const pad = 2;

  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * (width - pad * 2) + pad;
      const y = height - pad - ((v - min) / range) * (height - pad * 2);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <div className="flex items-center gap-2 py-1">
      <div className="shrink-0 w-12">
        <span className="text-[9px] text-zinc-500 block leading-tight">{label}</span>
        <span className="text-xs font-bold tabular-nums text-zinc-200">{value}</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="flex-1 h-8">
        <polyline points={points} fill="none" stroke={color} strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}
