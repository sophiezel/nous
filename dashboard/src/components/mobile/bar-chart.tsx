"use client";

interface BarChartProps {
  data: number[];
  labels?: string[];        // optional: labels below each bar
  width?: number;
  height?: number;
  color?: string;
  showZero?: boolean;        // show zero baseline
  className?: string;
}

export function BarChart({
  data,
  labels,
  width = 200,
  height = 60,
  color = "#10b981",
  showZero = true,
  className,
}: BarChartProps) {
  if (!data || data.length < 2) return null;

  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const labelH = labels ? 14 : 0;           // space for labels
  const chartH = height - labelH;
  const barWidth = Math.max(2, (width - data.length * 2) / data.length);
  const gap = (width - barWidth * data.length) / (data.length + 1);
  const zeroY = showZero && min < 0
    ? chartH - (Math.abs(min) / (max - min || 1)) * (chartH - 2)
    : chartH;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={className}
    >
      {/* Zero baseline */}
      {showZero && min < 0 && (
        <line
          x1={0} y1={zeroY} x2={width} y2={zeroY}
          stroke="currentColor" strokeOpacity={0.15} strokeWidth={0.5}
        />
      )}
      {data.map((v, i) => {
        const barH = Math.max(1, (Math.abs(v) / (max - min || 1)) * (chartH - 2));
        const x = gap + i * (barWidth + gap);
        const y = v >= 0 ? zeroY - barH : zeroY;
        return (
          <g key={i}>
            <rect
              x={x} y={y}
              width={barWidth} height={barH}
              fill={color}
              rx={Math.min(1, barWidth / 4)}
              opacity={0.85}
            />
            {labels && labels[i] && (
              <text
                x={x + barWidth / 2} y={height - 2}
                textAnchor="middle"
                fill="currentColor"
                fontSize="9"
                fillOpacity={0.5}
              >
                {labels[i]}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
