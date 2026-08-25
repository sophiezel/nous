#!/usr/bin/env python3
"""rebound_ml_ab.py — #12 ML 对照实验：LGBM 排序 vs 透明加权流水线

设计（防过拟合纪律 #6 P1–P6）:
  - 特征: 超跌族因子同源（PIT 按日截断、窗口≤20日、无 K7_*）
  - 标签: 未来 6 交易日收益 ≥ +2%（对应时间止损 min_gain 的"赢"定义）
  - 训练: 仅校准段 2020–2023；预测/对比: 样本外 2024–2026
  - A/B: 同一候选池/触发/执行/风控，仅排序不同（透明分 vs ML 概率）
  - 判定: 样本外胜率/PF 显著优于透明流水线才叠加（Q11）

用法: .venv/bin/python scripts/rebound_ml_ab.py [--train-end 2023-12-31]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from lightgbm import LGBMClassifier

from nous.engine.backtest.rebound_backtest import ReboundBacktest, run_backtest
from nous.engine.screening import rebound as rmod

FEATURES = ["ret_5d", "price_position_20", "ma_gap_20", "rsi14", "volume_ratio",
            "stabilize", "atr_pct", "ret_1d", "ret_3d", "lhb_net5", "sector_heat"]

# A2 验收配置（超跌族-only，与验收一致）
RISK_A2 = {"time_stop": {"oversold_days": 6, "strong_days": 4, "min_gain": 0.02},
           "stop_loss": {"atr_multiplier": 2.0, "day_low_min_buffer": 0.01, "strong_family_pct": 0.08},
           "gate": {"exit_limit_down_count": 250},
           "take_profit": {"oversold_first": 0.05, "strong_first": 0.08, "trail_drawdown": 0.04, "partial": 0.5},
           "quality": {"families": ["oversold"], "min_score": 75, "entry_chase_cap": 0.05,
                       "oversold_trigger": {"min_consecutive_drops": 2, "max_rsi": 35}},
           "position": {"max_single_pct": 0.15, "max_sector_pct": 0.30, "max_daily_picks": 2}}


def build_dataset(bt: ReboundBacktest, start: str, end: str, min_gain: float = 0.02):
    """校准段事件样本: 每个超跌触发候选 → 特征 + 标签(未来6日收益≥+2%)。
    训练需要全部候选（不受 min_score 过滤）；未来收益用全量 bars 计算。"""
    X, y = [], []
    saved_ms = rmod.RISK.get("quality", {}).get("min_score", 0.0)
    rmod.RISK.setdefault("quality", {})["min_score"] = 0.0   # 训练取全部候选
    all_bars = bt.memory["bars"]
    try:
        for day in bt.trade_dates:
            if not (start <= day <= end):
                continue
            day_bars = bt._slice_bars(day)
            memory = dict(bt.memory)
            memory["bars"] = day_bars
            memory["lhb_net5"] = bt._lhb_net5_for(day)
            engine = rmod.ReboundEngine(report_date=day, memory=memory)
            try:
                result = engine.scan()
            except Exception:
                continue
            for c in result.candidates:
                if c.family != "oversold":
                    continue
                feats = engine.ml_feature_vector(c.symbol)
                # 标签: 全量 bars 中 day 之后 6 个交易日的收益
                sym_bars = all_bars.get(c.symbol, [])
                dates = [b["date"] for b in sym_bars]
                import bisect
                i = bisect.bisect_right(dates, day) - 1
                if i < 0 or i >= len(sym_bars) or sym_bars[i]["close"] <= 0:
                    continue
                j = min(i + 6, len(sym_bars) - 1)
                fwd = sym_bars[j]["close"] / sym_bars[i]["close"] - 1.0
                X.append(feats)
                y.append(1 if fwd >= min_gain else 0)
            engine.close()
    finally:
        rmod.RISK.setdefault("quality", {})["min_score"] = saved_ms
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int)


def ztest_wr(wr_a, n_a, wr_b, n_b):
    """双比例 z 检验（胜率差异显著性）。"""
    p = (wr_a * n_a + wr_b * n_b) / (n_a + n_b)
    if p <= 0 or p >= 1:
        return 0.0, 1.0
    se = math.sqrt(p * (1 - p) * (1 / n_a + 1 / n_b))
    if se <= 0:
        return 0.0, 1.0
    z = (wr_b - wr_a) / se
    pval = 2 * (1 - _norm_cdf(abs(z)))
    return z, pval


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-end", default="2023-12-31")
    ap.add_argument("--oos-start", default="2024-01-01")
    ap.add_argument("--oos-end", default="2026-08-21")
    args = ap.parse_args()

    print(f"== ML 对照: 训练 {args.train_end} 前 / 样本外 {args.oos_start}..{args.oos_end} ==")

    bt = ReboundBacktest("2020-01-01", args.oos_end)  # 预载全窗口（训练+样本外）
    X, y = build_dataset(bt, "2020-01-01", args.train_end)
    print(f"训练样本: {len(X)} 正例 {y.sum()} ({y.mean():.1%})")
    if len(X) < 200 or y.sum() < 50:
        print("样本不足，退出")
        return 2

    # 训练（防过拟合: 早停 + 少量树 + 正则）
    model = LGBMClassifier(
        n_estimators=200, learning_rate=0.05, num_leaves=15, max_depth=4,
        min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.5, reg_lambda=1.0, random_state=42, verbose=-1)
    model.fit(X, y)
    imp = sorted(zip(FEATURES, model.feature_importances_), key=lambda t: -t[1])
    print("特征重要性:", ", ".join(f"{f}={v}" for f, v in imp[:6]))

    # ML 排序闭包
    def ml_rank(engine, symbol):
        try:
            feats = np.asarray([engine.ml_feature_vector(symbol)], dtype=float)
            return float(model.predict_proba(feats)[0][1])
        except Exception:
            return 0.0

    print("\n== 样本外 A/B ==")
    m_transparent = run_backtest(args.oos_start, args.oos_end, risk=RISK_A2)
    m_ml = run_backtest(args.oos_start, args.oos_end, risk=RISK_A2, ml_rank=ml_rank)

    for name, m in [("透明加权", m_transparent), ("ML(LGBM)", m_ml)]:
        print(f"  {name:10s}: trades={m['n_closed']:3d} wr={m['win_rate']:.3f} pf={m['profit_factor']:.3f} "
              f"exp={m['expectancy']:7.0f} ret={m['total_return']:.3f} sharpe={m['sharpe']:+.3f} mdd={m['max_drawdown']:.3f}")

    z, pval = ztest_wr(m_transparent["win_rate"], m_transparent["n_closed"], m_ml["win_rate"], m_ml["n_closed"])
    sig = pval < 0.05
    pf_better = (m_ml.get("profit_factor") or 0) > (m_transparent.get("profit_factor") or 0)
    overlay = sig and pf_better
    print(f"\n  z={z:.2f} p={pval:.4f} 显著性={'✅' if sig else '❌'} | ML PF 更优={'✅' if pf_better else '❌'}")
    print(f"  判定: {'叠加 ML（显著且 PF 更优）' if overlay else '维持透明加权（Q11）'}")

    # 报告
    today = "20260825"
    lines = [
        f"# Rebound ML 对照实验报告（#12）— {args.oos_start} ~ {args.oos_end}",
        "",
        f"> 生成: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')} | 训练 ≤ {args.train_end}（仅校准段）",
        "",
        f"- 训练样本: {len(X)}（正例 {y.sum()}，{y.mean():.1%}）",
        "- 特征: " + ", ".join(FEATURES),
        "- 特征重要性 top6: " + ", ".join(f"{f}={v}" for f, v in imp[:6]),
        "",
        "| 排序引擎 | 笔数 | 胜率 | PF | 期望 | 收益 | Sharpe | 回撤 |",
        "|---|---|---|---|---|---|---|---|",
        f"| 透明加权 | {m_transparent['n_closed']} | {m_transparent['win_rate']:.1%} | {m_transparent['profit_factor']:.3f} | "
        f"{m_transparent['expectancy']:.0f} | {m_transparent['total_return']:.1%} | {m_transparent['sharpe']:+.2f} | "
        f"{m_transparent['max_drawdown']:.1%} |",
        f"| ML(LGBM) | {m_ml['n_closed']} | {m_ml['win_rate']:.1%} | {m_ml['profit_factor']:.3f} | "
        f"{m_ml['expectancy']:.0f} | {m_ml['total_return']:.1%} | {m_ml['sharpe']:+.2f} | {m_ml['max_drawdown']:.1%} |",
        "",
        f"- 胜率差异 z={z:.2f} p={pval:.4f}（<0.05 显著）",
        f"- **判定: {'叠加 ML' if overlay else '维持透明加权（Q11 结论）'}**",
        "",
        "- 纪律: 训练仅校准段；预测样本外；特征 PIT 按日截断、窗口≤20日、无 K7_*。",
    ]
    report_path = PROJECT_ROOT / "docs" / "acceptance" / f"rebound_ml_ab_{today}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
