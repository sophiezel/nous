"use client";

import { cn } from "@/lib/utils";

interface FactorICPoint {
  date: string;
  factor_name: string;
  ic: number;
  rank_ic: number;
  significance: string;
}

interface FactorHeatmapProps {
  data: FactorICPoint[];
  className?: string;
}

function icColor(ic: number): string {
  if (ic > 0.1) return "bg-emerald-500/70 text-emerald-50";
  if (ic > 0.05) return "bg-emerald-500/40 text-emerald-200";
  if (ic > 0) return "bg-emerald-500/15 text-emerald-300/70";
  if (ic > -0.05) return "bg-rose-500/15 text-rose-300/70";
  if (ic > -0.1) return "bg-rose-500/40 text-rose-200";
  return "bg-rose-500/70 text-rose-50";
}

export function FactorHeatmap({ data, className }: FactorHeatmapProps) {
  if (!data || data.length === 0) {
    return (
      <div className={cn("text-[10px] text-zinc-500 text-center py-4", className)}>
        暂无因子IC数据
      </div>
    );
  }

  // Group by factor_name, sort by date descending
  const factorMap = new Map<string, FactorICPoint[]>();
  for (const d of data) {
    const arr = factorMap.get(d.factor_name) || [];
    arr.push(d);
    factorMap.set(d.factor_name, arr);
  }

  // Get unique dates (sorted desc) and factor names
  const dateSet = new Set<string>();
  for (const d of data) dateSet.add(d.date);
  const dates = Array.from(dateSet).sort().reverse().slice(0, 10);
  const factors = Array.from(factorMap.keys()).slice(0, 8);

  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full text-[10px]">
        <thead>
          <tr>
            <th className="text-left text-zinc-500 font-medium pb-1 pr-2 sticky left-0 bg-zinc-950">因子</th>
            {dates.map((date) => (
              <th key={date} className="text-center text-zinc-500 font-medium pb-1 px-1 tabular-nums" style={{ minWidth: 28 }}>
                {date.slice(5)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {factors.map((factor) => {
            const factorData = factorMap.get(factor) || [];
            const dateMap = new Map(factorData.map((d) => [d.date, d]));
            return (
              <tr key={factor}>
                <td className="text-zinc-300 font-medium pr-2 py-1 sticky left-0 bg-zinc-950 truncate max-w-[80px]">
                  {factor}
                </td>
                {dates.map((date) => {
                  const point = dateMap.get(date);
                  const ic = point?.ic ?? null;
                  return (
                    <td
                      key={date}
                      className={cn(
                        "text-center px-1 py-1 rounded tabular-nums",
                        ic != null ? icColor(ic) : "text-zinc-700"
                      )}
                      title={point ? `${factor} ${date} IC: ${ic?.toFixed(4)}` : ""}
                    >
                      {ic != null ? ic.toFixed(2) : "--"}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {data.length > 0 && (
        <p className="text-[9px] text-zinc-600 mt-1 text-center">
          颜色: 绿色正IC / 红色负IC · 饱和度表示强度
        </p>
      )}
    </div>
  );
}
