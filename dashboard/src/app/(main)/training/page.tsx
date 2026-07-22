import {
  getTrainingModelIC,
  getTrainingBacktestCurve,
  getTrainingFactorImportance,
  getTrainingICDecay,
} from "@/lib/db";
import { ZoomableSparkline } from "@/components/mobile/zoomable-sparkline";
import { FactorBars } from "@/components/mobile/ic-chart";
import { cn } from "@/lib/utils";
import { Brain, TrendingUp, BarChart3, Activity } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 300;

function MiniStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col items-center p-3 rounded-lg bg-zinc-800/40 border border-zinc-700/30">
      <span className="text-[9px] text-zinc-500 uppercase tracking-wider">{label}</span>
      <span className={cn("text-sm font-bold tabular-nums mt-0.5", color || "text-zinc-200")}>{value}</span>
    </div>
  );
}

function ModelCard({
  name,
  ic,
  rankIc,
  isBest,
}: {
  name: string;
  ic: number;
  rankIc: number;
  isBest: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border p-4 flex flex-col gap-2",
        isBest
          ? "border-emerald-500/30 bg-emerald-500/5"
          : "border-zinc-700/30 bg-zinc-800/30"
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-zinc-200">{name}</span>
        {isBest && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/20">
            最佳
          </span>
        )}
      </div>
      <MiniStat label="IC" value={(ic * 100).toFixed(1)} color={ic > 0 ? "text-emerald-400" : "text-red-400"} />
      <MiniStat label="Rank IC" value={(rankIc * 100).toFixed(1)} color={rankIc > 0 ? "text-emerald-400" : "text-red-400"} />
      <MiniStat label="Sharpe" value="—" />
      <MiniStat label="Calmar" value="—" />
    </div>
  );
}

export default function TrainingPage() {
  const modelIC = getTrainingModelIC();
  const backtestCurve = getTrainingBacktestCurve(120);
  const factorImportance = getTrainingFactorImportance(10);
  const icDecay = getTrainingICDecay(60);

  const bestModel = modelIC.length > 0
    ? modelIC.reduce((best, m) => (Number(m.ic) > Number(best.ic) ? m : best), modelIC[0])
    : null;

  // Prepare sparkline data from backtest NAV
  const navData = backtestCurve.map((r) => Number(r.nav));
  const navLabels = backtestCurve.map((r) => r.trade_date);

  // IC decay chart data
  const icDates = icDecay.map((r) => r.trade_date?.slice(5) || "");
  const icValues = icDecay.map((r) => Number(r.avg_rank_ic) * 100);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold tracking-tight">训练看板</h1>
        <p className="text-sm text-zinc-500 mt-1">
          模型训练监控 · 回测表现 · 因子分析
        </p>
      </div>

      {/* Model Comparison Cards */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
          <Brain className="w-4 h-4 text-violet-400" />
          模型横向对比
        </h2>
        {modelIC.length === 0 ? (
          <div className="p-8 text-center text-zinc-500 text-sm">暂无模型IC数据 · 等待量化训练推送</div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {modelIC.map((m) => (
              <ModelCard
                key={m.model}
                name={m.model}
                ic={Number(m.ic)}
                rankIc={Number(m.rank_ic)}
                isBest={bestModel?.model === m.model}
              />
            ))}
          </div>
        )}
      </div>

      {/* Backtest Equity Curve */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-emerald-400" />
          回测收益曲线
        </h2>
        {navData.length < 2 ? (
          <div className="p-8 text-center text-zinc-500 text-sm">暂无回测数据</div>
        ) : (
          <ZoomableSparkline
            data={navData}
            labels={navLabels}
            color="#10b981"
            height={80}
            className="w-full"
          />
        )}
        {backtestCurve.length > 0 && (
          <div className="grid grid-cols-3 gap-2 mt-4">
            <MiniStat
              label="当前净值"
              value={Number(backtestCurve[backtestCurve.length - 1]?.nav || 0).toFixed(4)}
              color="text-emerald-400"
            />
            <MiniStat
              label="最新日收益"
              value={`${(Number(backtestCurve[backtestCurve.length - 1]?.daily_return || 0) * 100).toFixed(2)}%`}
              color="text-amber-400"
            />
            <MiniStat
              label="累计盈亏"
              value={`${(Number(backtestCurve[backtestCurve.length - 1]?.pnl_pct || 0)).toFixed(2)}%`}
              color="text-zinc-200"
            />
          </div>
        )}
      </div>

      {/* Factor Importance + IC Decay */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Factor Importance Top 10 */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
          <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-blue-400" />
            因子重要性 Top 10
          </h2>
          {factorImportance.length === 0 ? (
            <div className="p-8 text-center text-zinc-500 text-sm">暂无因子数据</div>
          ) : (
            <FactorBars
              data={factorImportance.map((f) => ({
                factor_name: f.factor_name,
                importance: Number(f.importance_enc) / 100,
              }))}
            />
          )}
        </div>

        {/* IC Decay Curve */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
          <h2 className="text-sm font-semibold text-zinc-300 mb-4 flex items-center gap-2">
            <Activity className="w-4 h-4 text-rose-400" />
            IC 衰减曲线
          </h2>
          {icDecay.length === 0 ? (
            <div className="p-8 text-center text-zinc-500 text-sm">暂无IC衰减数据</div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-[10px] text-zinc-500 mb-2">
                <span className="w-2 h-2 rounded-full bg-emerald-500" /> Rank IC均值
              </div>
              <ZoomableSparkline
                data={icValues}
                labels={icDates}
                color="#f43f5e"
                height={60}
                className="w-full"
              />
              <div className="flex justify-between text-[9px] text-zinc-600 mt-1">
                <span>{icDates[0] || ""}</span>
                <span>{icDates[icDates.length - 1] || ""}</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
