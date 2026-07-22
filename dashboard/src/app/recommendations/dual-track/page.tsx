"use client";

import { useEffect, useState } from "react";

interface Pick {
  engine: string;
  symbol: string;
  name: string;
  score: number;
  pool?: string;
  theme?: string;
  tier?: string;
}

export default function DualTrackPage() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/recommendations/dual-track")
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, []);

  if (loading) return <div className="p-8 text-emerald-400">加载中...</div>;
  if (error) return <div className="p-8 text-rose-400">⚠️ {error}</div>;
  if (!data) return <div className="p-8 text-slate-400">无数据</div>;

  const { f3, trl, consensus, date, pools } = data;

  return (
    <div className="min-h-screen bg-zinc-950 text-slate-200 p-4 md:p-8">
      <h1 className="text-2xl font-bold text-emerald-400 mb-2">
        🦅🐉 双引擎荐股对比 — {date}
      </h1>
      
      {/* 总览卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard label="F3海鹰" value={f3.total} color="emerald" />
        <StatCard label="TRL龙脉" value={trl.total} color="amber" />
        <StatCard label="共识票" value={consensus.count} color="sky" />
        <StatCard 
          label="分池" 
          value={Object.keys(pools).length} 
          color="violet" 
        />
      </div>

      {/* 共识高亮 */}
      {consensus.count > 0 && (
        <div className="mb-6 p-4 bg-sky-900/20 border border-sky-700/30 rounded-lg">
          <h2 className="text-sky-400 font-semibold mb-2">⚡ 共识票 (两引擎同时推荐 → ×1.5仓位)</h2>
          <div className="flex flex-wrap gap-2">
            {consensus.symbols.map((s: string) => (
              <span key={s} className="px-3 py-1 bg-sky-800/30 rounded text-sm">{s}</span>
            ))}
          </div>
        </div>
      )}

      {/* 双列对比 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* F3列 */}
        <div>
          <h2 className="text-lg font-semibold text-emerald-400 mb-3">
            🦅 海鹰F3 (因子分级) — {f3.total}只
          </h2>
          <div className="space-y-1 max-h-[600px] overflow-y-auto">
            {f3.rows?.slice(0, 50).map((p: Pick, i: number) => (
              <div key={i} className="flex justify-between items-center p-2 bg-zinc-900/50 rounded text-sm hover:bg-zinc-800/50">
                <span>
                  <span className="text-emerald-300 font-mono">{p.symbol}</span>
                  <span className="text-slate-400 ml-2">{p.name}</span>
                </span>
                <span className="text-slate-500 text-xs">{p.pool} | {Number(p.score).toFixed(1)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* TRL列 */}
        <div>
          <h2 className="text-lg font-semibold text-amber-400 mb-3">
            🐉 龙脉TRL (主线共振) — {trl.total}只
          </h2>
          <div className="space-y-1 max-h-[600px] overflow-y-auto">
            {trl.rows?.slice(0, 50).map((p: Pick, i: number) => (
              <div key={i} className={`flex justify-between items-center p-2 rounded text-sm ${
                consensus.symbols?.includes(p.symbol) 
                  ? "bg-sky-900/30 border border-sky-700/20" 
                  : "bg-zinc-900/50 hover:bg-zinc-800/50"
              }`}>
                <span>
                  <span className="text-amber-300 font-mono">{p.symbol}</span>
                  <span className="text-slate-400 ml-2">{p.name}</span>
                  {p.theme && <span className="text-violet-400 text-xs ml-2">∥{p.theme}</span>}
                </span>
                <span className="text-slate-500 text-xs">{p.tier} | {Number(p.score).toFixed(1)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 分池对比 */}
      {Object.keys(pools).length > 0 && (
        <div className="mt-8">
          <h2 className="text-lg font-semibold text-violet-400 mb-3">📊 分池对比</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(pools).map(([key, val]: [string, any]) => (
              <div key={key} className="p-3 bg-zinc-900/50 rounded text-sm">
                <div className="text-slate-400">{key}</div>
                <div className="flex gap-3 mt-1">
                  <span className="text-emerald-400">F3:{val.f3}</span>
                  <span className="text-amber-400">TRL:{val.trl}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  const colors: Record<string, string> = {
    emerald: "border-emerald-700/30 bg-emerald-900/10 text-emerald-400",
    amber: "border-amber-700/30 bg-amber-900/10 text-amber-400",
    sky: "border-sky-700/30 bg-sky-900/10 text-sky-400",
    violet: "border-violet-700/30 bg-violet-900/10 text-violet-400",
  };
  return (
    <div className={`p-4 rounded-lg border ${colors[color] || colors.emerald}`}>
      <div className="text-3xl font-bold">{value}</div>
      <div className="text-sm opacity-70">{label}</div>
    </div>
  );
}
