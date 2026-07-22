"use client";

import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

interface Props {
  data: { trade_date: string; model: string; ic: number }[];
}

export function ICTrendChart({ data }: Props) {
  // Group by model, create series
  const modelMap = new Map<string, Map<string, number>>();
  const allDates = new Set<string>();

  for (const point of data) {
    allDates.add(point.trade_date);
    if (!modelMap.has(point.model)) modelMap.set(point.model, new Map());
    modelMap.get(point.model)!.set(point.trade_date, point.ic * 100);
  }

  const sortedDates = Array.from(allDates).sort();
  const chartData = sortedDates.map((date) => {
    const row: Record<string, string | number> = { date: date.slice(5) };
    for (const [model, dateMap] of modelMap) {
      row[model] = dateMap.get(date) ?? null;
    }
    return row;
  });

  const modelColors: Record<string, string> = {
    catboost: "#f59e0b",
    xgboost: "#3b82f6",
    lightgbm: "#10b981",
    mlp: "#8b5cf6",
    ridge: "#ef4444",
    voting: "#ec4899",
  };

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
        <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
        <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#71717a" }} axisLine={{ stroke: "#3f3f46" }} tickLine={false} />
        <YAxis tick={{ fontSize: 9, fill: "#71717a" }} axisLine={{ stroke: "#3f3f46" }} tickLine={false}
          domain={["auto", "auto"]} tickFormatter={(v: number) => v.toFixed(0)} />
        <Tooltip
          contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: "8px", fontSize: "11px", color: "#e4e4e7" }}
          formatter={(value: any, name: string) => [typeof value === "number" ? value.toFixed(2) : "—", name]}
        />
        {Array.from(modelMap.keys()).map((model) => (
          <Line key={model} type="monotone" dataKey={model} stroke={modelColors[model] || "#71717a"}
            strokeWidth={1.2} dot={false} activeDot={{ r: 3 }} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
