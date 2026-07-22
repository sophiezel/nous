"use client";

import { useState, ReactNode } from "react";
import { AutoRefresh } from "./AutoRefresh";

interface PollingPageProps {
  pollKey: string;
  fetchFn: () => Promise<any>;
  initialData: any;
  render: (data: any, loading: boolean) => ReactNode;
}

/**
 * 通用轮询页面包装器
 * RSC 获取初始数据 → PollingPage 接管 → 按 cron 窗口自动刷新
 */
export function PollingPage({ pollKey, fetchFn, initialData, render }: PollingPageProps) {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(false);

  return (
    <>
      <AutoRefresh
        pollKey={pollKey}
        onPoll={async () => {
          setLoading(true);
          try {
            const fresh = await fetchFn();
            if (fresh) setData(fresh);
          } catch {} finally {
            setLoading(false);
          }
        }}
      />
      {render(data, loading)}
    </>
  );
}
