import { BottomNav } from "@/components/mobile/bottom-nav";

export default function MobileLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      {/* Status bar spacer + header */}
      <header className="sticky top-0 z-40 bg-zinc-950/90 backdrop-blur-xl border-b border-zinc-800/50">
        <div className="flex items-center justify-between h-12 px-4 max-w-lg mx-auto">
          <div className="flex items-center gap-2">
            <svg width="28" height="28" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="2" y="2" width="24" height="24" rx="7" stroke="#10b981" strokeWidth="1.5" />
              <path d="M8 18 L14 8 L20 14 L14 20 Z" fill="#10b981" fillOpacity="0.25" stroke="#10b981" strokeWidth="1.2" strokeLinejoin="round" />
            </svg>
            <span className="text-sm font-bold text-zinc-100">天工AI</span>
          </div>
          <span className="text-[11px] text-zinc-500 tabular-nums">
            {new Date().toLocaleDateString("zh-CN", { month: "short", day: "numeric", weekday: "short" })}
          </span>
        </div>
      </header>

      {/* Content area */}
      <main className="pb-20 px-4 pt-3 max-w-lg mx-auto min-h-screen">
        {children}
      </main>

      {/* Bottom navigation */}
      <BottomNav />
    </>
  );
}
