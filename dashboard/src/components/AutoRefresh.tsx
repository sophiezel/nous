"use client";

import { useEffect, useState, useRef } from "react";

/**
 * 轻量轮询组件 — 嵌入任何 Server Component 页面
 * 每隔 interval ms 触发 onPoll, 根据 pollKey 自动调整频率
 */
const PEAK_WINDOWS: Record<string, { after: string; dur: number; peak: number; offpeak: number }> = {
  macro:     { after: "08:05", dur: 15, peak: 120_000, offpeak: 1800_000 },
  sentiment: { after: "16:30", dur: 20, peak: 120_000, offpeak: 1800_000 },
  margin:    { after: "16:30", dur: 20, peak: 120_000, offpeak: 1800_000 },
  lhb:       { after: "18:00", dur: 20, peak: 120_000, offpeak: 1800_000 },
  hsgt:      { after: "16:30", dur: 20, peak: 120_000, offpeak: 1800_000 },
  flow:      { after: "16:30", dur: 20, peak: 120_000, offpeak: 1800_000 },
  portfolio: { after: "16:30", dur: 30, peak: 120_000, offpeak: 1800_000 },
};

function getInterval(key: string): number {
  const w = PEAK_WINDOWS[key];
  if (!w) return 300_000;
  const now = new Date();
  const m = now.getHours() * 60 + now.getMinutes();
  const [ah, am] = w.after.split(":").map(Number);
  const start = ah * 60 + am;
  return (m >= start && m <= start + w.dur) ? w.peak : w.offpeak;
}

interface Props {
  pollKey: string;
  onPoll: () => Promise<void>;
}

export function AutoRefresh({ pollKey, onPoll }: Props) {
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    let stopped = false;

    const schedule = () => {
      if (stopped) return;
      const interval = getInterval(pollKey);
      timerRef.current = setTimeout(async () => {
        if (stopped) return;
        try { await onPoll(); } catch {}
        schedule();
      }, interval);
    };

    schedule();
    return () => { stopped = true; if (timerRef.current) clearTimeout(timerRef.current); };
  }, [pollKey]);

  return null; // invisible
}
