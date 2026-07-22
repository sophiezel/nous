"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Cron 时间表 — 每个数据源的预期更新时间
 * after: cron 运行的时刻 (HH:MM)
 * duration: 高频窗口持续分钟数
 * peakInterval: 窗口内轮询间隔 ms
 * offpeakInterval: 窗口外轮询间隔 ms
 */
const CRON_SCHEDULE: Record<string, {
  after: string; duration: number; peakInterval: number; offpeakInterval: number;
}> = {
  macro:    { after: "08:05", duration: 15, peakInterval: 120_000, offpeakInterval: 1800_000 },
  sentiment:{ after: "16:30", duration: 20, peakInterval: 120_000, offpeakInterval: 1800_000 },
  margin:   { after: "16:30", duration: 20, peakInterval: 120_000, offpeakInterval: 1800_000 },
  lhb:      { after: "18:00", duration: 20, peakInterval: 120_000, offpeakInterval: 1800_000 },
  block:    { after: "18:00", duration: 20, peakInterval: 120_000, offpeakInterval: 1800_000 },
  hsgt:     { after: "16:30", duration: 20, peakInterval: 120_000, offpeakInterval: 1800_000 },
  rec_perf: { after: "16:10", duration: 15, peakInterval: 120_000, offpeakInterval: 1800_000 },
};

const DEFAULT_SCHEDULE = {
  after: "00:00", duration: 0, peakInterval: 300_000, offpeakInterval: 1800_000,
};

function getSchedule(key: string) {
  return CRON_SCHEDULE[key] || DEFAULT_SCHEDULE;
}

function isInWindow(after: string, durationMin: number): boolean {
  const now = new Date();
  const [h, m] = after.split(":").map(Number);
  const startMin = h * 60 + m;
  const endMin = startMin + durationMin;
  const currentMin = now.getHours() * 60 + now.getMinutes();
  return currentMin >= startMin && currentMin <= endMin;
}

function getPollInterval(key: string): number {
  const s = getSchedule(key);
  if (s.duration === 0) return s.offpeakInterval;
  return isInWindow(s.after, s.duration) ? s.peakInterval : s.offpeakInterval;
}

/**
 * 智能轮询 hook — 按 cron 窗口自动调整频率
 * 
 * @param key 数据源 key (参见 CRON_SCHEDULE)
 * @param fetchFn 拉取函数, 返回 Promise<T>
 * @param initialData 初始数据 (从 RSC 传入)
 * @returns [数据, 是否正在加载]
 */
export function useScheduledPoll<T>(
  key: string,
  fetchFn: () => Promise<T>,
  initialData: T,
): [T, boolean] {
  const [data, setData] = useState<T>(initialData);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    let stopped = false;

    const poll = async () => {
      if (stopped || !mountedRef.current) return;
      setLoading(true);
      try {
        const fresh = await fetchFn();
        if (!stopped && mountedRef.current) {
          setData(fresh);
        }
      } catch {
        // 静默失败, 下次再试
      } finally {
        if (!stopped && mountedRef.current) {
          setLoading(false);
          scheduleNext();
        }
      }
    };

    const scheduleNext = () => {
      if (stopped) return;
      const interval = getPollInterval(key);
      timerRef.current = setTimeout(poll, interval);
    };

    // 启动时不等第一个 interval, 但如果数据刚加载不久(<5s)则跳过
    scheduleNext();

    return () => {
      stopped = true;
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [key]);

  return [data, loading];
}
