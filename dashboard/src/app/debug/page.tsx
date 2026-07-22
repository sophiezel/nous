import { getLatestMacroScore, getLatestSentiment, getRecentReports, getLatestHsgt, getLatestMargin, getLatestGlobalIndex, getFuturesLatest, getIndexDailyHistory } from "@/lib/db";

export const dynamic = "force-dynamic";

function dbg(label: string, fn: () => unknown): string {
  try { fn(); return `${label}: OK`; }
  catch(e: any) { return `${label}: ${e.message}`; }
}

export default function DebugPage() {
  const macros = dbg("macro", () => getLatestMacroScore());
  const sent = dbg("sentiment", () => getLatestSentiment());
  const reports = dbg("reports", () => getRecentReports(undefined, 4));
  const hsgt = dbg("hsgt", () => getLatestHsgt());
  const margin = dbg("margin", () => getLatestMargin());
  const spx = dbg("spx", () => getLatestGlobalIndex("spx"));
  const ndx = dbg("ndx", () => getLatestGlobalIndex("ndx"));
  const dji = dbg("dji", () => getLatestGlobalIndex("dji"));
  const vix = dbg("vix", () => getLatestGlobalIndex("vix"));
  const idx = dbg("index", () => getIndexDailyHistory("IDX_000001", 1));
  const fut = dbg("futures", () => getFuturesLatest());

  const all = [macros, sent, reports, hsgt, margin, spx, ndx, dji, vix, idx, fut];

  return (
    <div className="p-8 font-mono text-sm space-y-1">
      {all.map((r, i) => (
        <div key={i} className={r.includes("OK") ? "text-emerald-400" : "text-red-400"}>
          {r}
        </div>
      ))}
    </div>
  );
}
