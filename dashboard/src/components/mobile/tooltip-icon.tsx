"use client";

import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { HelpCircle, X } from "lucide-react";

interface TooltipIconProps {
  title: string;
  children: React.ReactNode;
}

export function TooltipIcon({ title, children }: TooltipIconProps) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const [mounted, setMounted] = useState(false);

  useEffect(() => { setMounted(true); }, []);

  function openTooltip() {
    if (btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect();
      const tooltipH = 280; // approximate max height
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const showBelow = spaceBelow > tooltipH || spaceBelow > spaceAbove;
      setPos({
        top: showBelow ? rect.bottom + 6 : rect.top - tooltipH - 6,
        left: Math.min(Math.max(8, rect.left + rect.width / 2 - 128), window.innerWidth - 264),
      });
    }
    setOpen(true);
  }

  const tooltip = open && mounted ? createPortal(
    <>
      <div className="fixed inset-0 z-[99]" onClick={() => setOpen(false)} />
      <div
        className="fixed z-[100] w-64 bg-zinc-900 border border-zinc-700 rounded-xl p-4 shadow-2xl text-left"
        style={{ top: Math.max(8, pos.top), left: Math.max(8, pos.left) }}
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-zinc-200">{title}</span>
          <button onClick={() => setOpen(false)} className="text-zinc-600 hover:text-zinc-400">
            <X className="w-3 h-3" />
          </button>
        </div>
        <div className="text-[11px] text-zinc-400 leading-relaxed space-y-1.5">
          {children}
        </div>
      </div>
    </>,
    document.body
  ) : null;

  return (
    <span className="inline-flex items-center">
      <button
        ref={btnRef}
        onClick={openTooltip}
        className="inline-flex items-center justify-center w-4 h-4 rounded-full text-zinc-600 hover:text-zinc-400 hover:bg-zinc-800 transition-colors ml-0.5"
      >
        <HelpCircle className="w-3 h-3" />
      </button>
      {tooltip}
    </span>
  );
}

// ── Pre-built tooltip contents ──────────────────────────

export function MarketRegimeTooltip() {
  return (
    <TooltipIcon title="市场状态 · 4分类模型">
      <p>AI 模型根据均线、波动率、成交量将市场分为 4 种状态（准确率 98%）：</p>
      <div className="space-y-1 mt-2">
        <div><span className="text-emerald-400 font-medium">牛市 BULL</span> — 趋势上涨，适合进攻，建议仓位 60-90%</div>
        <div><span className="text-red-400 font-medium">熊市 BEAR</span> — 趋势下跌，防御为主，建议仓位 0-30%</div>
        <div><span className="text-amber-400 font-medium">震荡 SIDEWAYS</span> — 方向不明，精选个股，建议仓位 20-50%</div>
        <div><span className="text-orange-400 font-medium">高波动 VOLATILE</span> — 剧烈波动，风险极高，建议仓位 0-20%</div>
      </div>
    </TooltipIcon>
  );
}

export function MacroScoreTooltip() {
  return (
    <TooltipIcon title="宏观评分">
      <p>综合 CPI、PPI、PMI、M2、LPR 等宏观指标加权计算，范围 0-100：</p>
      <div className="space-y-1 mt-2">
        <div><span className="text-emerald-400 font-medium">≥70 扩张</span> — 经济上行期，企业盈利改善，适合进攻配置</div>
        <div><span className="text-amber-400 font-medium">50-69 中性</span> — 经济平稳，适合均衡配置，精选个股</div>
        <div><span className="text-red-400 font-medium">&lt;50 收缩</span> — 经济下行期，业绩承压，适合防御或减仓</div>
      </div>
      <p className="mt-2 text-zinc-500">仓位由宏观×市场状态×情绪×波动率四维加权计算，非单一指标决定。</p>
    </TooltipIcon>
  );
}

export function ModelICTooltip() {
  return (
    <TooltipIcon title="模型信号 · IC 指标">
      <p>IC = 6个AI模型预测<span className="text-zinc-200">200只A股5日涨跌</span>的准确度（Pearson相关系数），数值已×100：</p>
      <div className="space-y-1 mt-2">
        <div><span className="text-emerald-400 font-medium">&gt;10.0 优秀</span> — 预测可靠，可跟随模型信号</div>
        <div><span className="text-amber-400 font-medium">5.0-10.0 有效</span> — 有一定预测力，参考使用</div>
        <div><span className="text-red-400 font-medium">&lt;5.0 弱/无效</span> — 预测力不足，暂停跟随信号</div>
      </div>
      <p className="mt-2 text-zinc-500">
        IC衡量的是个股收益预测准确度，不是大势方向判断。
        CAT=CatBoost · XGB=XGBoost · LGB=LightGBM
      </p>
    </TooltipIcon>
  );
}

export function VIXTooltip() {
  return (
    <TooltipIcon title="VIX · 恐慌指数">
      <p>基于标普500期权价格计算的隐含波动率，反映市场恐慌程度：</p>
      <div className="space-y-1 mt-2">
        <div><span className="text-red-400 font-medium">&gt;30 恐慌</span> — 市场极度不安，往往是大跌或见底信号</div>
        <div><span className="text-amber-400 font-medium">20-30 担忧</span> — 波动加剧，警惕风险</div>
        <div><span className="text-emerald-400 font-medium">&lt;15 平静</span> — 市场自满，注意反转风险</div>
      </div>
      <p className="mt-2 text-zinc-500">VIX 飙升 → 减仓信号。VIX 低位横盘 → 关注突破方向。</p>
    </TooltipIcon>
  );
}

export function QuantSignalTooltip() {
  return (
    <TooltipIcon title="量化信号 · L5 决策层">
      <p className="font-medium text-zinc-300">模型 IC 对比：</p>
      <p>6个模型对<span className="text-zinc-200">200只个股5日涨跌</span>的预测准确度PK。条越长→预测能力越强。</p>
      <p className="font-medium text-zinc-300 mt-2">因子贡献 TOP 8：</p>
      <p>SHAP算法揭示当前市场<span className="text-zinc-200">由哪些因子驱动</span>。排第一的因子=市场核心定价逻辑。</p>
      <p className="mt-2 text-zinc-500">
        用法：IC{'>'}10 → 模型信号可信，跟随bias方向<br />
        IC{'<'}5 → 暂停跟随，等待恢复<br />
        因子排名→判断市场风格（动量/价值/质量）
      </p>
    </TooltipIcon>
  );
}
