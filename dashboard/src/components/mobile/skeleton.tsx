export function SkeletonHero() {
  return (
    <div className="rounded-[20px] border border-zinc-800/80 bg-zinc-900/50 p-5 animate-pulse">
      <div className="flex justify-between mb-5">
        <div>
          <div className="h-5 w-20 bg-zinc-800 rounded mb-1" />
          <div className="h-3 w-32 bg-zinc-800 rounded" />
        </div>
        <div className="h-5 w-12 bg-zinc-800 rounded-full" />
      </div>
      <div className="flex justify-center gap-8 mb-5">
        <div className="w-24 h-24 rounded-full bg-zinc-800" />
        <div className="w-px h-20 bg-zinc-800" />
        <div className="w-24 h-24 rounded-full bg-zinc-800" />
      </div>
      <div className="h-3 bg-zinc-800 rounded-full" />
    </div>
  );
}

export function SkeletonKpi() {
  return (
    <div className="grid grid-cols-2 gap-2 animate-pulse">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="rounded-xl border border-zinc-800/60 p-3">
          <div className="h-3 w-12 bg-zinc-800 rounded mb-2" />
          <div className="h-6 w-16 bg-zinc-800 rounded" />
        </div>
      ))}
    </div>
  );
}

export function SkeletonList({ count = 5 }: { count?: number }) {
  return (
    <div className="space-y-2 animate-pulse">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 p-3 rounded-xl border border-zinc-800/50 bg-zinc-900/30"
        >
          <div className="w-8 h-8 rounded-lg bg-zinc-800 shrink-0" />
          <div className="flex-1 space-y-1.5">
            <div className="h-3 w-16 bg-zinc-800 rounded" />
            <div className="h-3 w-full bg-zinc-800 rounded" />
          </div>
          <div className="w-4 h-4 bg-zinc-800 rounded shrink-0" />
        </div>
      ))}
    </div>
  );
}
