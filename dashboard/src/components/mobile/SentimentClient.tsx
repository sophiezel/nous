"use client";

import { useState } from "react";
import { fetchAPI } from "@/lib/api";
import { useScheduledPoll } from "@/hooks/useScheduledPoll";
import { safeJsonParse } from "@/lib/utils";

interface SentimentClientProps {
  chartData: any[];
  latest: { date: string; score: number; limit_up_count: number; limit_up_rate: number } | null;
  latestDetails: Record<string, unknown> | null;
}

function parseHistoryToChart(history: any[]) {
  return [...history].reverse().map((row: any) => {
    const details = safeJsonParse(row.details);
    return {
      date: row.date?.slice(0, 10),
      values: {
        涨停家数: row.limit_up_count,
        涨停率: +(row.limit_up_rate * 100).toFixed(1),
        情绪评分: row.score,
        跌停家数: (details?.跌停家数 as number) ?? 0,
        炸板数: (details?.炸板数 as number) ?? 0,
        炸板率: (details?.炸板率 as number) ?? 0,
        上涨家数: (details?.上涨家数 as number) ?? 0,
        下跌家数: (details?.下跌家数 as number) ?? 0,
        涨跌比: (details?.涨跌比 as number) ?? 0,
      },
    };
  });
}

export function SentimentClient(props: SentimentClientProps) {
  const [latest, setLatest] = useState(props.latest);
  const [chartData, setChartData] = useState(props.chartData);
  const [latestDetails, setLatestDetails] = useState(props.latestDetails);
  const [loading, setLoading] = useState(false);

  // 智能轮询: 16:30-16:50 每2min, 其他 30min
  const fetchLatest = async () => {
    setLoading(true);
    try {
      const [newLatest, newHistory] = await Promise.all([
        fetchAPI<any>("/v1/sentiment/latest"),
        fetchAPI<any[]>("/v1/sentiment/history?days=366"),
      ]);
      if (newLatest && Object.keys(newLatest).length > 0) {
        setLatest({
          date: newLatest.date,
          score: newLatest.score,
          limit_up_count: newLatest.limit_up_count,
          limit_up_rate: newLatest.limit_up_rate,
        });
        setLatestDetails(safeJsonParse(newLatest.details));
      }
      if (newHistory?.length) {
        setChartData(parseHistoryToChart(newHistory));
      }
    } catch {} finally {
      setLoading(false);
    }
  };

  useScheduledPoll("sentiment", fetchLatest, props.latest);

  const details = latestDetails as Record<string, number> | null;

  return (
    <div className="p-4 space-y-4">
      {loading && (
        <div className="text-zinc-500 text-xs text-right">刷新中...</div>
      )}
      {latest && (
        <div className="grid grid-cols-3 gap-2">
          <div className="bg-zinc-900/70 rounded-xl p-3 text-center">
            <div className="text-xs text-zinc-500">情绪评分</div>
            <div className={`text-2xl font-bold ${latest.score >= 50 ? "text-emerald-400" : "text-rose-400"}`}>
              {latest.score}
            </div>
          </div>
          <div className="bg-zinc-900/70 rounded-xl p-3 text-center">
            <div className="text-xs text-zinc-500">涨停家数</div>
            <div className="text-2xl font-bold text-zinc-100">{latest.limit_up_count}</div>
          </div>
          <div className="bg-zinc-900/70 rounded-xl p-3 text-center">
            <div className="text-xs text-zinc-500">涨停率</div>
            <div className="text-2xl font-bold text-zinc-100">{latest.limit_up_rate}%</div>
          </div>
        </div>
      )}
      {details && (
        <div className="grid grid-cols-2 gap-2 text-sm">
          {Object.entries(details).map(([k, v]) => (
            <div key={k} className="bg-zinc-900/50 rounded-lg px-3 py-2 flex justify-between">
              <span className="text-zinc-500">{k}</span>
              <span className="text-zinc-200">{typeof v === "number" ? v.toFixed(1) : String(v)}</span>
            </div>
          ))}
        </div>
      )}
      {chartData.length > 0 && (
        <div className="text-xs text-zinc-600">
          共 {chartData.length} 个交易日数据 · 更新于 {latest?.date}
        </div>
      )}
    </div>
  );
}
