import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Parse JSON string from SQLite TEXT column into object. Returns null on failure. */
export function safeJsonParse(json: string | Record<string, unknown> | null): Record<string, unknown> | null {
  if (!json) return null;
  if (typeof json === "object") return json as Record<string, unknown>;
  try {
    const parsed = JSON.parse(json);
    return typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatPercent(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function typeLabel(type: string): string {
  const labels: Record<string, string> = {
    daily_picks: "每日荐股",
    buy_signals: "买入信号",
    trader_daily: "交易日报",
    portfolio_review: "持仓复盘",
    midday_patrol: "盘中巡检",
    hk_close: "港股收盘",
    afternoon_review: "午后复盘",
    ai_chain: "AI扩散阶段",
    ai_signals: "AI买卖信号",
    risk_alert: "风控告警",
    power_signals: "电力信号",
    health_check: "数据健康",
    macro_score: "宏观评分",
    sentiment: "市场情绪",
    test: "测试",
  };
  return labels[type] || type;
}

export function typeColor(type: string): string {
  const colors: Record<string, string> = {
    daily_picks: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    buy_signals: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    trader_daily: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    portfolio_review: "bg-violet-500/10 text-violet-400 border-violet-500/20",
    midday_patrol: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
    hk_close: "bg-rose-500/10 text-rose-400 border-rose-500/20",
    afternoon_review: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
    ai_chain: "bg-purple-500/10 text-purple-400 border-purple-500/20",
    ai_signals: "bg-pink-500/10 text-pink-400 border-pink-500/20",
    risk_alert: "bg-red-500/10 text-red-400 border-red-500/20",
    power_signals: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20",
    health_check: "bg-teal-500/10 text-teal-400 border-teal-500/20",
  };
  return colors[type] || "bg-zinc-500/10 text-zinc-400 border-zinc-500/20";
}
