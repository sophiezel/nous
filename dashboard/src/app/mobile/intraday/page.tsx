"use client";

import { useState, useEffect } from "react";
import { TrendingUp, Globe, Activity, RefreshCw } from "lucide-react";

type IndexData = {
  symbol: string;
  label: string;
  close: number;
  changePct: number;
  history: { date: string; close: number }[];
};

type PoolStock = {
  theme: string;
  stocks: { symbol: string; name: string; price: number; change_pct: number; volume: number; source: string }[];
};

type QuantPick = { symbol: string; name: string; score: number };

type PeriodPnl = { label: string; pnl: number };

export default function MobileIntradayPage() {
  const [activeTab, setActiveTab] = useState("推荐");
  const [indices, setIndices] = useState<IndexData[]>([]);
  const [pools, setPools] = useState<PoolStock[]>([]);
  const [quantPicks, setQuantPicks] = useState<QuantPick[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const tabs = ["推荐", "模拟", "量化", "实盘"];

  useEffect(() => {
    async function load() {
      try {
        const [indicesRes, poolRes] = await Promise.all([
          fetch("/api/intraday/indices"),
          fetch("/api/intraday/pool-stocks"),
        ]);
        if (indicesRes.ok) {
          const idxJson = await indicesRes.json();
          setIndices(idxJson.indices || []);
        }
        if (poolRes.ok) {
          const poolJson = await poolRes.json();
          setPools(poolJson.pools || []);
          setQuantPicks(poolJson.quantPicks || []);
        }
      } catch (e) {
        setError("加载失败");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  // 非交易时段检查（客户端本地时间）
  const hour = new Date().getHours();
  const isTradingHours = hour >= 9 && hour < 15;
  const isWeekend = [0, 6].includes(new Date().getDay());

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded bg-emerald-500 flex items-center justify-center shrink-0">
            <Activity className="w-3 h-3 text-white" />
          </div>
          <h1 className="text-sm font-bold">盘中监控</h1>
        </div>
        <div className="animate-pulse space-y-3">
          {[1, 2, 3].map(i => <div key={i} className="h-20 rounded-xl bg-zinc-900/50 border border-zinc-800" />)}
        </div>
      </div>
    );
  }

  if (!isTradingHours || isWeekend) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded bg-emerald-500 flex items-center justify-center shrink-0">
            <Activity className="w-3 h-3 text-white" />
          </div>
          <h1 className="text-sm font-bold">盘中监控</h1>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-8 text-center">
          <RefreshCw className="w-8 h-8 text-zinc-600 mx-auto mb-2" />
          <p className="text-sm text-zinc-500">等待开盘</p>
          <p className="text-[10px] text-zinc-600 mt-1">
            {isWeekend ? "周末休市" : "交易时段 09:30 - 15:00"}
          </p>
        </div>
      </div>
    );
  }

  // 获取当前tab的数据源
  const currentPool = activeTab === "量化" ? [] : pools.find(p => {
    if (activeTab === "推荐") return p.theme.includes("推荐") || p.theme.includes("核心");
    if (activeTab === "模拟") return p.theme.includes("模拟");
    if (activeTab === "实盘") return p.theme.includes("实盘") || p.theme.includes("持仓");
    return false;
  })?.stocks || [];
  const allStocks = activeTab === "量化" ? quantPicks : currentPool;

  return (
    <div className="space-y-3">
      {/* 顶部固定指数条 */}
      <div className="flex items-center gap-2">
        <div className="w-5 h-5 rounded bg-emerald-500 flex items-center justify-center shrink-0">
          <Globe className="w-3 h-3 text-white" />
        </div>
        <h1 className="text-sm font-bold">盘中监控</h1>
      </div>

      {indices.length > 0 && (
        <div className="flex gap-1.5 overflow-x-auto -mx-4 px-4 pb-1 scrollbar-none">
          {indices.map(idx => (
            <div key={idx.symbol} className="shrink-0 rounded-lg border border-zinc-800 bg-zinc-900/60 px-2.5 py-1.5 min-w-[80px] text-center">
              <div className="text-[8px] text-zinc-500">{idx.label}</div>
              <div className="text-xs font-bold tabular-nums text-zinc-100">{idx.close?.toFixed(0)}</div>
              <div className={`text-[9px] font-medium ${idx.changePct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {idx.changePct >= 0 ? "+" : ""}{idx.changePct?.toFixed(2)}%
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tab 切换 */}
      <div className="flex gap-1 bg-zinc-900/60 rounded-xl p-1 border border-zinc-800/50">
        {tabs.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 text-[10px] font-medium py-1.5 rounded-lg transition-all ${
              activeTab === tab
                ? "bg-emerald-500/10 text-emerald-400 shadow-sm"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* 可展开股票行 */}
      <div className="space-y-1.5">
        {error && <p className="text-[10px] text-red-400 text-center py-2">{error}</p>}
        {(!allStocks || allStocks.length === 0) && !error && (
          <p className="text-[10px] text-zinc-500 text-center py-4">暂无数据</p>
        )}
        {allStocks.slice(0, 20).map((stock: any, i: number) => (
          <StockRow key={stock.symbol || i} stock={stock} activeTab={activeTab} />
        ))}
      </div>
    </div>
  );
}

function StockRow({ stock, activeTab }: { stock: any; activeTab: string }) {
  const [expanded, setExpanded] = useState(false);
  const [chartDots, setChartDots] = useState<number[]>([]);
  const pct = stock.change_pct ?? stock.last_return ?? 0;

  useEffect(() => {
    if (expanded) {
      // 模拟分时图数据点
      const dots = Array.from({ length: 240 }, () => Math.random() * 2 - 1);
      // 累积
      const cum = dots.reduce((acc: number[], v) => {
        const last = acc.length > 0 ? acc[acc.length - 1] : 0;
        acc.push(+(last + v).toFixed(3));
        return acc;
      }, []);
      setChartDots(cum);
    }
  }, [expanded]);

  // 模拟4时段盈亏
  const periodPnls: PeriodPnl[] = [
    { label: "早盘", pnl: +(pct * 0.4).toFixed(2) },
    { label: "午盘", pnl: +(pct * 0.3).toFixed(2) },
    { label: "尾盘", pnl: +(pct * 0.2).toFixed(2) },
    { label: "收盘", pnl: +(pct * 0.1).toFixed(2) },
  ];

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-xs"
      >
        <span className="font-mono text-zinc-500 w-14 shrink-0 text-[10px]">{stock.symbol || ""}</span>
        <span className="text-zinc-200 flex-1 text-left truncate">{stock.name || ""}</span>
        {stock.score != null && (
          <span className="text-violet-400 tabular-nums text-[10px]">{stock.score.toFixed(1)}</span>
        )}
        <span className={`tabular-nums font-medium ${pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
          {pct >= 0 ? "+" : ""}{pct.toFixed(2)}%
        </span>
        <span className={`text-[9px] transition-transform ${expanded ? "rotate-180" : ""}`}>▾</span>
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-zinc-800/50 pt-2">
          {/* 分时图 (简易Canvas CSS) */}
          {chartDots.length > 0 && (
            <div className="h-10 mb-2 relative">
              <svg viewBox={`0 0 ${chartDots.length} 100`} className="w-full h-full" preserveAspectRatio="none">
                <defs>
                  <linearGradient id="mini-chart-grad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity="0.3" />
                    <stop offset="100%" stopColor="#10b981" stopOpacity="0" />
                  </linearGradient>
                </defs>
                <polygon
                  points={`0,100 ${chartDots.map((v, i) => `${i},${50 - v * 40}`).join(" ")} ${chartDots.length - 1},100`}
                  fill="url(#mini-chart-grad)"
                />
                <polyline
                  points={chartDots.map((v, i) => `${i},${50 - v * 40}`).join(" ")}
                  fill="none" stroke="#10b981" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"
                />
              </svg>
            </div>
          )}

          {/* 4时段盈亏标签 */}
          <div className="flex gap-1.5">
            {periodPnls.map(p => (
              <span
                key={p.label}
                className={`text-[9px] px-1.5 py-0.5 rounded-full ${
                  p.pnl >= 0 ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"
                }`}
              >
                {p.label} {p.pnl >= 0 ? "+" : ""}{p.pnl.toFixed(2)}%
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
