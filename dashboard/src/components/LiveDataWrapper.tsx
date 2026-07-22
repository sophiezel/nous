"use client";

import { ReactNode, useState } from "react";
import { useScheduledPoll } from "@/hooks/useScheduledPoll";

interface LiveDataWrapperProps<T> {
  /** 数据源 key (sentiment/macro/margin/lhb/hsgt) */
  pollKey: string;
  /** 拉取函数, 返回新数据 */
  fetchFn: () => Promise<T>;
  /** 服务端传入的初始数据 */
  initialData: T;
  /** 渲染函数, 接收最新数据 + loading 状态 */
  children: (data: T, loading: boolean) => ReactNode;
}

export function LiveDataWrapper<T>({
  pollKey,
  fetchFn,
  initialData,
  children,
}: LiveDataWrapperProps<T>) {
  const [data, setData] = useState<T>(initialData);
  const [loading, setLoading] = useState(false);

  const poll = async () => {
    setLoading(true);
    try {
      const fresh = await fetchFn();
      if (fresh) setData(fresh);
    } catch {} finally {
      setLoading(false);
    }
  };

  useScheduledPoll(pollKey, poll, initialData);

  return <>{children(data, loading)}</>;
}
