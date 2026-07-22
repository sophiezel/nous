"use client";

// Tiny horizontal bar chart for model IC comparison
export function ICBarChart({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const maxIc = Math.max(...entries.map(([, v]) => Math.abs(v)), 0.01);

  return (
    <div className="space-y-1.5">
      {entries.map(([name, ic]) => (
        <div key={name} className="flex items-center gap-2 text-[10px]">
          <span className="w-14 text-zinc-500 truncate">{name}</span>
          <div className="flex-1 h-3 bg-zinc-800 rounded-sm overflow-hidden relative">
            <div
              className={`absolute inset-y-0 left-1/2 rounded-sm transition-all ${
                ic >= 0 ? "bg-emerald-500/60" : "bg-red-500/60"
              }`}
              style={{ width: `${(Math.abs(ic) / maxIc) * 50}%`, left: ic >= 0 ? "50%" : `${50 - (Math.abs(ic) / maxIc) * 50}%` }}
            />
          </div>
          <span className={`w-10 text-right font-mono tabular-nums ${ic > 0.05 ? "text-emerald-400" : ic > 0 ? "text-amber-400" : "text-red-400"}`}>
            {(ic * 100).toFixed(1)}
          </span>
        </div>
      ))}
    </div>
  );
}

// Ranked horizontal bar chart for factor importance
export function FactorBars({ data }: { data: { factor_name: string; importance: number }[] }) {
  const max = data[0]?.importance || 0.01;

  return (
    <div className="space-y-1">
      {data.slice(0, 8).map((f) => (
        <div key={f.factor_name} className="flex items-center gap-1.5 text-[9px]">
          <span className="w-16 text-zinc-500 truncate font-mono">{f.factor_name}</span>
          <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-violet-500/60 rounded-full transition-all"
              style={{ width: `${(f.importance / max) * 100}%` }}
            />
          </div>
          <span className="w-8 text-right tabular-nums text-zinc-400">{(f.importance * 100).toFixed(1)}</span>
        </div>
      ))}
    </div>
  );
}
