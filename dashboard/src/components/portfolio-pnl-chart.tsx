"use client";

import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { NavPoint } from "@/lib/db";

export function PortfolioChart({ data }: { data: NavPoint[] }) {
  const chartData = data.map((d) => ({
    date: d.trade_date.slice(5), // MM-DD
    nav: d.nav,
    pnl: d.pnl_pct,
  }));

  // Colors based on overall P&L
  const isPositive = (chartData[chartData.length - 1]?.pnl || 0) >= 0;
  const lineColor = isPositive ? "#10b981" : "#ef4444";

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
        <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 9, fill: "#71717a" }}
          axisLine={{ stroke: "#3f3f46" }}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fontSize: 9, fill: "#71717a" }}
          axisLine={{ stroke: "#3f3f46" }}
          tickLine={false}
          domain={["auto", "auto"]}
          tickFormatter={(v: number) => v.toFixed(3)}
        />
        <Tooltip
          contentStyle={{
            background: "#18181b",
            border: "1px solid #3f3f46",
            borderRadius: "8px",
            fontSize: "11px",
            color: "#e4e4e7",
          }}
          formatter={(value: any, name: string) => [
            typeof value === "number" ? value.toFixed(4) : value,
            name === "nav" ? "净值" : "盈亏%",
          ]}
          labelFormatter={(label: string) => label}
        />
        <Line
          type="monotone"
          dataKey="nav"
          stroke={lineColor}
          strokeWidth={1.5}
          dot={false}
          activeDot={{ r: 3, fill: lineColor }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
