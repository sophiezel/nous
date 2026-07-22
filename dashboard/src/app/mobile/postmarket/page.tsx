"use client";

import { useState, useEffect } from "react";
import { TrendingUp, DollarSign, Activity, BarChart3, BookOpen } from "lucide-react";

// ── Types ──────────────────────────────────────────────

type RecItem = {
  symbol: string;
  name: string;
  rec_score: number;
  return_1d: number | null;
  return_5d: number | null;
  return_20d: number | null;
  last_return: number | null;
};

type NavItem = {
  trade_date: string;
  nav: number;
  daily_return: number;
  pnl_pct: number;
};

type PoolStat = { theme: string; count: number; avg_change: number; best: number; worst: number };

type Eliminated = { symbol: string; name: string; score: number; pe: number; rsi: number };

type TradeLog = { id: number; type: string; title: string; created_at: string; preview: string };

type RecSummary = { totalRecs: number; avgReturn: number; winRate: number; wins: number; losses: number };
type MacroScore = { date: string; score: number };
type HsgtRow = { trade_date: string; direction: string; total_net: number };
type MarginRow = { trade_date: string; margin_balance: number; short_balance: number; total_balance: number };

// ── Collapsible Section ────────────────────────────────

function Section({
  title, icon: Icon, color, defaultOpen, children,
}: {
  title: string; icon: any; color: string; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen ?? true);
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-xs"
      >
        <Icon className="w-4 h-4" style={{ color }} />
        <span className="text-zinc-300 font-medium flex-1 text-left">{title}</span>
        <span className={`text-zinc-500 text-[9px] transition-transform ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open && <div className="px-3 pb-3 border-t border-zinc-800/50 pt-2">{children}</div>}
    </div>
  );
}

// ── Mini Sparkline (inline SVG) ────────────────────────

function MiniSparkline({ data, color = "#10b981", height = 24 }: { data: number[]; color?: string; height?: number }) {
  if (!data || data.length < 2) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const w = 60;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="w-full h-full" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// ── Main Page ──────────────────────────────────────────

export default function MobilePostmarketPage() {
  const [recs, setRecs] = useState<RecItem[]>([]);
  const [nav, setNav] = useState<NavItem[]>([]);
  const [poolStats, setPoolStats] = useState<PoolStat[]>([]);
  const [eliminated, setEliminated] = useState<Eliminated[]>([]);
  const [tradeLogs, setTradeLogs] = useState<TradeLog[]>([]);
  const [summary, setSummary] = useState<RecSummary | null>(null);
  const [macroScore, setMacroScore] = useState<MacroScore | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [compRes, sumRes] = await Promise.all([
          fetch("/api/postmarket/comparison"),
          fetch("/api/postmarket/summary"),
        ]);
        if (compRes.ok) {
          const d = await compRes.json();
          setRecs(d.recs || []);
          setPoolStats(d.poolStats || []);
          setEliminated(d.eliminated || []);
          setTradeLogs(d.tradeLogs || []);
        }
        if (sumRes.ok) {
          const d = await sumRes.json();
          setSummary(d.recSummary || null);
          setNav(d.nav || []);
          setMacroScore(d.macroScore || null);
        }
      } catch { /* ignore */ } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded bg-emerald-500 flex items-center justify-center shrink-0">
            <BarChart3 className="w-3 h-3 text-white" />
          </div>
          <h1 className="text-sm font-bold">盘后复盘</h1>
        </div>
        <div className="animate-pulse space-y-3">
          {[1, 2, 3, 4, 5].map(i => <div key={i} className="h-24 rounded-xl bg-zinc-900/50 border border-zinc-800" />)}
        </div>
      </div>
    );
  }

  // 横向条形数据
  const barMax = Math.max(...recs.map(r => Math.abs(r.last_return || 0)), 1);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="w-5 h-5 rounded bg-emerald-500 flex items-center justify-center shrink-0">
          <BarChart3 className="w-3 h-3 text-white" />
        </div>
        <h1 className="text-sm font-bold">盘后复盘</h1>
        {macroScore && (
          <span className="text-[10px] text-zinc-500 ml-auto">宏观 {macroScore.score.toFixed(0)}</span>
        )}
      </div>

      {/* 模块1: 今日概览 */}
      <Section title="今日概览" icon={Activity} color="#10b981" defaultOpen={true}>
        {summary ? (
          <div className="grid grid-cols-4 gap-2 text-center">
            <div>
              <div className="text-lg font-bold tabular-nums text-zinc-100">{summary.totalRecs}</div>
              <div className="text-[8px] text-zinc-500">推荐总数</div>
            </div>
            <div>
              <div className={`text-lg font-bold tabular-nums ${summary.avgReturn >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {summary.avgReturn >= 0 ? "+" : ""}{summary.avgReturn.toFixed(1)}%
              </div>
              <div className="text-[8px] text-zinc-500">平均收益</div>
            </div>
            <div>
              <div className="text-lg font-bold tabular-nums text-emerald-400">{summary.winRate}%</div>
              <div className="text-[8px] text-zinc-500">胜率</div>
            </div>
            <div>
              <div className="text-lg font-bold tabular-nums text-zinc-100">{summary.wins}/{summary.losses}</div>
              <div className="text-[8px] text-zinc-500">胜/负</div>
            </div>
          </div>
        ) : (
          <p className="text-[10px] text-zinc-500 text-center py-2">暂无数据</p>
        )}
      </Section>

      {/* 模块2: 推荐股盈亏对比 */}
      <Section title="推荐股盈亏对比" icon={DollarSign} color="#f59e0b" defaultOpen={true}>
        {recs.length === 0 ? (
          <p className="text-[10px] text-zinc-500 text-center py-2">暂无推荐数据</p>
        ) : (
          <div className="space-y-1.5">
            {recs.slice(0, 10).map((r, i) => {
              const pct = r.last_return || 0;
              const barW = Math.min(Math.abs(pct) / barMax * 100, 100);
              return (
                <div key={r.symbol || i} className="flex items-center gap-2 text-[10px]">
                  <span className="text-zinc-600 w-4 text-center">{i + 1}</span>
                  <span className="text-zinc-200 w-14 truncate">{r.name || r.symbol}</span>
                  {/* 横向条形 */}
                  <div className="flex-1 h-3 bg-zinc-800 rounded-full overflow-hidden relative">
                    <div
                      className={`h-full rounded-full ${pct >= 0 ? "bg-emerald-500/60" : "bg-red-500/60"}`}
                      style={{
                        width: `${barW}%`,
                        marginLeft: pct >= 0 ? "0" : `${100 - barW}%`,
                      }}
                    />
                  </div>
                  <span className={`tabular-nums font-medium w-12 text-right ${pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                    {pct >= 0 ? "+" : ""}{pct.toFixed(1)}%
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Section>

      {/* 模块3: 剔除池统计 */}
      <Section title="剔除池统计" icon={TrendingUp} color="#ef4444" defaultOpen={false}>
        {eliminated.length === 0 ? (
          <p className="text-[10px] text-zinc-500 text-center py-2">暂无剔除数据</p>
        ) : (
          <div className="space-y-1">
            {eliminated.map((e, i) => (
              <div key={e.symbol || i} className="flex items-center gap-2 text-[10px] py-1 border-b border-zinc-800/30 last:border-0">
                <span className="text-zinc-600 w-4">{i + 1}</span>
                <span className="text-zinc-300 w-14 truncate">{e.name}</span>
                <span className="text-zinc-500 font-mono">{e.symbol}</span>
                <span className="ml-auto text-red-400 tabular-nums">{e.score?.toFixed(1)}</span>
              </div>
            ))}
          </div>
        )}
        {poolStats.length > 0 && (
          <div className="mt-2 pt-2 border-t border-zinc-800/30">
            <span className="text-[9px] text-zinc-500 block mb-1">主题池统计</span>
            <div className="grid grid-cols-2 gap-1">
              {poolStats.map(p => (
                <div key={p.theme} className="bg-zinc-800/30 rounded p-1.5 text-[9px]">
                  <span className="text-zinc-400">{p.theme}</span>
                  <span className="text-zinc-200 ml-1">{p.count}只</span>
                  <span className={`block tabular-nums ${p.avg_change >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                    {p.avg_change >= 0 ? "+" : ""}{p.avg_change.toFixed(2)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </Section>

      {/* 模块4: 量化盘盈亏曲线 */}
      <Section title="量化盘盈亏曲线" icon={BarChart3} color="#8b5cf6" defaultOpen={false}>
        {nav.length < 2 ? (
          <p className="text-[10px] text-zinc-500 text-center py-2">暂无净值数据</p>
        ) : (
          <div>
            <div className="flex items-center gap-3 mb-2">
              <div>
                <span className="text-[9px] text-zinc-500">最新净值</span>
                <p className="text-base font-bold tabular-nums text-zinc-100">{nav[nav.length - 1]?.nav?.toFixed(4)}</p>
              </div>
              <div>
                <span className="text-[9px] text-zinc-500">累计收益</span>
                <p className={`text-base font-bold tabular-nums ${(nav[nav.length - 1]?.pnl_pct || 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {(nav[nav.length - 1]?.pnl_pct || 0) >= 0 ? "+" : ""}{(nav[nav.length - 1]?.pnl_pct || 0).toFixed(2)}%
                </p>
              </div>
            </div>
            {/* 净值曲线 */}
            <div className="h-16 relative">
              <MiniSparkline data={nav.map(n => n.nav)} color="#8b5cf6" height={64} />
            </div>
            <div className="flex justify-between text-[8px] text-zinc-600 mt-0.5">
              <span>{nav[0]?.trade_date?.slice(5)}</span>
              <span>{nav[nav.length - 1]?.trade_date?.slice(5)}</span>
            </div>
          </div>
        )}
      </Section>

      {/* 模块5: AI总结 */}
      <Section title="AI总结" icon={BookOpen} color="#3b82f6" defaultOpen={false}>
        {tradeLogs.length === 0 ? (
          <p className="text-[10px] text-zinc-500 text-center py-2">暂无日志</p>
        ) : (
          <div className="space-y-1.5">
            {tradeLogs.map((log, i) => (
              <div key={log.id || i} className="flex items-start gap-2 text-[10px] py-1.5 border-b border-zinc-800/30 last:border-0">
                <span className="w-1 h-1 rounded-full bg-blue-400 mt-1 shrink-0" />
                <div className="flex-1 min-w-0">
                  <span className="text-zinc-300 block truncate">{log.title || log.type}</span>
                  <span className="text-zinc-600 text-[8px]">{log.created_at?.slice(5, 16)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
        {summary && (
          <div className="mt-2 pt-2 border-t border-zinc-800/30 text-[10px] text-zinc-400 leading-relaxed">
            <p>
              本日推荐 <span className="text-zinc-200">{summary.totalRecs}</span> 只，平均收益 
              <span className={summary.avgReturn >= 0 ? "text-emerald-400" : "text-red-400"}> {summary.avgReturn >= 0 ? "+" : ""}{summary.avgReturn.toFixed(2)}%</span>，
              胜率 <span className="text-zinc-200">{summary.winRate}%</span>。
              其中盈利 <span className="text-emerald-400">{summary.wins}</span> 只，亏损 <span className="text-red-400">{summary.losses}</span> 只。
            </p>
          </div>
        )}
      </Section>
    </div>
  );
}
