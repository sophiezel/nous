import { fetchAPI } from "@/lib/api";
import { safeJsonParse } from "@/lib/utils";
import { SentimentClient } from "@/components/mobile/SentimentClient";

export const dynamic = "force-dynamic";
export const revalidate = 60;

function parseHistoryToChart(history: any[]) {
  return [...history].reverse().map((row) => {
    const details = safeJsonParse(row.details);
    return {
      date: row.date.slice(0, 10),
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
        最高连板: (details?.最高连板 as number) ?? 0,
        昨日涨停溢价: (details?.昨日涨停溢价 as number) ?? 0,
      },
    };
  });
}

export default async function MobileSentimentPage() {
  const [history, latest] = await Promise.all([
    fetchAPI("/v1/sentiment/history?days=366"),
    fetchAPI("/v1/sentiment/latest"),
  ]);
  const chartData = parseHistoryToChart(history ?? []);
  const latestDetails = safeJsonParse(latest?.details ?? null);

  return (
    <SentimentClient
      chartData={chartData}
      latest={
        latest
          ? {
              date: latest.date,
              score: latest.score,
              limit_up_count: latest.limit_up_count,
              limit_up_rate: latest.limit_up_rate,
            }
          : null
      }
      latestDetails={latestDetails}
    />
  );
}
