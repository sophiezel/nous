import { StatCard } from "@/components/stat-card";
import { ReportCard } from "@/components/report-card";
import {
  getRecentReports,
  getLatestMacroScore,
  getLatestSentiment,
  getReportCount,
  getLatestReportByType,
} from "@/lib/db";
import { getLatestQuantSignal } from "@/lib/quant-db";
import { typeLabel, formatDate } from "@/lib/utils";
import { TrendingUp, Activity, FileText, Shield, Brain, BarChart3 } from "lucide-react";

export const dynamic = "force-dynamic";
export const revalidate = 60;

export default function HomePage() {
  const macro = getLatestMacroScore();
  const sentiment = getLatestSentiment();
  const totalReports = getReportCount();

  // Latest key reports
  const latestPicks = getLatestReportByType("daily_picks");
  const latestBuy = getLatestReportByType("buy_signals");
  const latestReview = getLatestReportByType("portfolio_review");
  const latestHealth = getLatestReportByType("health_check");

  // Recent 8 reports
  const recent = (getRecentReports(undefined, 8)).map((r) => ({
    id: r.id,
    type: r.type,
    title: r.title,
    preview: r.content.substring(0, 150).replace(/[#*`\n]/g, " ").trim(),
    created_at: r.created_at,
  }));

  const positionPercent = macro
    ? `${(macro.position * 100).toFixed(0)}%`
    : "--";

  const macroScoreDisplay = macro ? macro.score.toFixed(1) : "--";

  const sentimentScoreDisplay = sentiment ? sentiment.score : "--";

  // ── Quant signals ──
  const quant = getLatestQuantSignal();
  const quantIC = quant?.ensemble_ic?.toFixed(2) ?? "--";
  const quantBias = quant?.prediction_bias ?? "--";
  const quantBuys = quant?.buy_signals ?? 0;
  const quantSells = quant?.sell_signals ?? 0;
  const quantTotal = quant?.total_stocks ?? 0;
  const quantBestModel = (() => {
    if (!quant) return null;
    const ics = [
      { name: "CatBoost", ic: quant.catboost_ic },
      { name: "XGBoost", ic: quant.xgboost_ic },
      { name: "LightGBM", ic: quant.lightgbm_ic },
      { name: "MLP", ic: quant.mlp_ic },
      { name: "Ridge", ic: quant.ridge_ic },
    ];
    return ics.sort((a, b) => b.ic - a.ic)[0];
  })();

  const quantBiasDisplay = (() => {
    if (!quantBias || quantBias === "--") return "待采集";
    const m: Record<string, string> = { BULLISH: "看多", BEARISH: "看空", SIDEWAYS: "横盘" };
    return m[quantBias] || quantBias;
  })();
  const quantBiasTrend: "up" | "down" | "neutral" =
    quantBias === "BULLISH" ? "up" : quantBias === "BEARISH" ? "down" : "neutral";

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold tracking-tight">仪表盘</h1>
        <p className="text-sm text-zinc-500 mt-1">
          投研数据总览 · {new Date().toLocaleDateString("zh-CN")}
        </p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          title="宏观评分"
          value={macroScoreDisplay}
          subtitle={macro ? `仓位建议: ${positionPercent}` : "暂无数据"}
          icon={<TrendingUp className="w-4 h-4" />}
          obfuscate
          trend={
            macro && macro.score >= 70
              ? "up"
              : macro && macro.score >= 50
              ? "neutral"
              : "down"
          }
        />
        <StatCard
          title="市场情绪"
          value={sentimentScoreDisplay}
          obfuscate
          subtitle={
            sentiment
              ? `${sentiment.limit_up_count}家涨停`
              : "暂无数据"
          }
          icon={<Activity className="w-4 h-4" />}
          trend={
            sentiment && sentiment.score >= 60
              ? "up"
              : sentiment && sentiment.score >= 40
              ? "neutral"
              : "down"
          }
        />
        <StatCard
          title="累计报告"
          value={totalReports}
          subtitle="历史总计"
          icon={<FileText className="w-4 h-4" />}
          trend="neutral"
        />
        <StatCard
          title="系统状态"
          value={latestHealth ? "正常" : "等待"}
          subtitle={latestHealth ? formatDate(latestHealth.created_at) : "首次运行中"}
          icon={<Shield className="w-4 h-4" />}
          trend="up"
        />
        <StatCard
          title="量化方向"
          value={quantBiasDisplay}
          subtitle={quantBias !== "--" ? `综合IC ${quantIC}` : "bridge未推送"}
          icon={<Brain className="w-4 h-4" />}
          obfuscate
          trend={quantBiasTrend}
        />
        <StatCard
          title="模型信号"
          value={quantTotal > 0 ? `买${quantBuys}·卖${quantSells}` : "--"}
          subtitle={quantBestModel ? `${quantBestModel.name} IC ${(quantBestModel.ic*100).toFixed(1)}` : "等待训练"}
          icon={<BarChart3 className="w-4 h-4" />}
          trend={quantBuys > quantSells * 2 ? "up" : quantSells > quantBuys * 2 ? "down" : "neutral"}
        />
      </div>

      {/* Key Reports */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {latestPicks && (
          <ReportCard
            id={latestPicks.id}
            type={latestPicks.type}
            title={latestPicks.title || latestPicks.type}
            preview={latestPicks.content.substring(0, 150).replace(/[#*`\n]/g, " ").trim()}
            created_at={latestPicks.created_at}
          />
        )}
        {latestBuy && (
          <ReportCard
            id={latestBuy.id}
            type={latestBuy.type}
            title={latestBuy.title || latestBuy.type}
            preview={latestBuy.content.substring(0, 150).replace(/[#*`\n]/g, " ").trim()}
            created_at={latestBuy.created_at}
          />
        )}
      </div>

      {latestReview && (
        <ReportCard
          id={latestReview.id}
          type={latestReview.type}
          title={latestReview.title || latestReview.type}
          preview={latestReview.content.substring(0, 200).replace(/[#*`\n]/g, " ").trim()}
          created_at={latestReview.created_at}
        />
      )}

      {/* Recent Reports */}
      <div>
        <h2 className="text-sm font-semibold text-zinc-300 mb-3">最近报告</h2>
        {recent.length === 0 ? (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-8 text-center">
            <FileText className="w-8 h-8 text-zinc-600 mx-auto mb-2" />
            <p className="text-sm text-zinc-500">暂无报告</p>
            <p className="text-xs text-zinc-600 mt-1">
              等待 cron 任务推送数据...
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {recent.map((r) => (
              <ReportCard key={r.id} {...r} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
