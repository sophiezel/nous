"""
IC驱动的仓位管理 — Kelly准则 + ATR约束

核心逻辑:
1. 将模型IC映射到预测胜率 p = Φ(IC / sqrt(IC² + σ²_IC))
2. Kelly公式: f* = (p*b - (1-p)) / b
3. f* 受 ATR 和流动性双重约束
4. 产出: 最终仓位比例 f_final

参考文献: d3-kelly-criterion-ml.md (2026-05-21蒸馏)
"""

from __future__ import annotations

import logging
import numpy as np
from scipy.stats import norm

logger = logging.getLogger(__name__)

# 安全参数
MAX_POSITION_FRACTION = 0.25  # 单票最大仓位
MIN_POSITION_FRACTION = 0.02  # 单票最小仓位(低于此不建仓)
DEFAULT_IC_VOL = 0.12         # IC波动率默认值(基于实测A股W-F IC std≈0.03, 加截面变异性)
DEFAULT_EDGE_RATIO = 1.2      # 赔率(盈利:亏损), A股保守估计

# 体制感知风控参数
REGIME_RISK_PARAMS = {
    "BULL": {
        "position_mult": 1.0,      # 满仓位
        "atr_stop_mult": 1.5,      # 紧止损
        "edge_ratio": 1.5,          # 高赔率(牛市趋势持续)
    },
    "BEAR": {
        "position_mult": 0.5,      # 半仓位
        "atr_stop_mult": 2.5,      # 宽止损(熊市假突破多)
        "edge_ratio": 1.0,          # 保守赔率
    },
    "SIDEWAYS": {
        "position_mult": 0.75,     # 中等仓位
        "atr_stop_mult": 2.0,      # 中等止损
        "edge_ratio": 1.2,          # 标准赔率
    },
    "VOLATILE": {
        "position_mult": 0.3,      # 低仓位(高波动=高风险)
        "atr_stop_mult": 3.0,      # 宽止损(极端波动)
        "edge_ratio": 0.8,          # 低赔率
    },
}


def get_regime_risk_params(regime: str | None = None) -> dict:
    """获取当前市场体制对应的风控参数。

    如果无法获取体制，返回 SIDEWAYS 中性参数。

    Args:
        regime: "BULL"/"BEAR"/"SIDEWAYS"/"VOLATILE"，None则尝试自动检测

    Returns:
        {"position_mult", "atr_stop_mult", "edge_ratio"}
    """
    if regime is None:
        try:
            from nous.engine.ml.market_regime import predict_current_regime
            result = predict_current_regime()
            regime = result.get("regime", "SIDEWAYS")
        except Exception:
            regime = "SIDEWAYS"

    return REGIME_RISK_PARAMS.get(regime, REGIME_RISK_PARAMS["SIDEWAYS"])


def ic_to_win_probability(ic: float, ic_vol: float = DEFAULT_IC_VOL) -> float:
    """将IC映射为预测胜率。

    假设 IC ~ N(μ, σ²_IC)，则 "预测正确" 等价于 IC > 0 的概率:
    p = P(IC > 0) = Φ(IC / σ_IC)

    Args:
        ic: 信息系数 (模型预测值与实际收益的相关性)
        ic_vol: IC的历史波动率

    Returns:
        预测胜率 p ∈ (0, 1)
    """
    if ic_vol <= 0:
        ic_vol = DEFAULT_IC_VOL

    # 信噪比
    snr = ic / ic_vol
    # IC可能为负 — 负IC意味着预测方向相反
    p = norm.cdf(snr)
    return float(p)


def kelly_fraction(
    win_prob: float,
    edge_ratio: float = DEFAULT_EDGE_RATIO,
    max_fraction: float = MAX_POSITION_FRACTION,
    min_fraction: float = MIN_POSITION_FRACTION,
    fractional: float = 0.5,
) -> float:
    """Kelly公式计算最优仓位。

    f* = (p * b - q) / b
    其中 p=胜率, q=1-p, b=赔率

    使用 Fractional Kelly (默认 half-Kelly) 降低参数敏感性。

    Args:
        win_prob: 预测胜率
        edge_ratio: 赔率 (盈利:亏损比)
        max_fraction: 仓位上限
        min_fraction: 仓位下限
        fractional: Kelly分数 (0.5=half-Kelly, 1=full-Kelly)

    Returns:
        仓位比例 f ∈ [0, max_fraction]
    """
    q = 1.0 - win_prob
    b = edge_ratio
    # Kelly 公式
    f_star = (win_prob * b - q) / b if b > 0 else 0
    # Half-Kelly 保守化
    f_adjusted = f_star * fractional
    # 约束
    f_final = max(0.0, min(f_adjusted, max_fraction))

    if f_final > 0 and f_final < min_fraction:
        f_final = min_fraction  # 太小没意义，给个下限

    return f_final


def ic_to_position(
    ic: float,
    ic_vol: float = DEFAULT_IC_VOL,
    atr_pct: float = 0.03,
    portfolio_value: float = 1_000_000,
    daily_volume: float = 0,
    max_fraction: float = MAX_POSITION_FRACTION,
    edge_ratio: float = DEFAULT_EDGE_RATIO,
) -> dict:
    """IC → 最终仓位 (含ATR和流动性约束)。

    完整决策链:
    IC → 胜率 → Kelly f* → ATR调整 → 流动性约束 → f_final

    Args:
        ic: 模型IC (可为负)
        ic_vol: IC历史波动率
        atr_pct: ATR占价格百分比
        portfolio_value: 组合总价值
        daily_volume: 日均成交额 (用于流动性约束)
        max_fraction: 最大仓位上限
        edge_ratio: 赔率

    Returns:
        dict with keys: f_final, shares, win_prob, kelly_f, atr_limited, liquidity_limited
    """
    # 负IC → 不做多
    if ic < 0:
        return {
            "f_final": 0.0,
            "shares": 0,
            "win_prob": 0.0,
            "kelly_f": 0.0,
            "atr_limited": False,
            "liquidity_limited": False,
            "signal": "negative_ic_skip",
        }

    # Step 1: IC → 胜率
    win_prob = ic_to_win_probability(ic, ic_vol)

    # Step 2: Kelly
    kelly_f = kelly_fraction(win_prob, edge_ratio=edge_ratio, max_fraction=max_fraction)

    # Step 3: ATR约束 — 以1.5倍ATR作为止损，控制单票风险
    risk_per_share_atr = 1.5 * atr_pct
    if risk_per_share_atr > 0 and kelly_f > 0:
        max_atr_shares = (kelly_f * portfolio_value * 0.02) / risk_per_share_atr
        atr_limited = kelly_f * portfolio_value / risk_per_share_atr > max_atr_shares
    else:
        max_atr_shares = float("inf")
        atr_limited = False

    # Step 4: 流动性约束 — 单票不超过日均成交额的5%
    if daily_volume > 0:
        max_liq_value = daily_volume * 0.05
        liquidity_limited = kelly_f * portfolio_value > max_liq_value
        f_liq = max_liq_value / portfolio_value if portfolio_value > 0 else 0
        f_final = min(kelly_f, f_liq)
    else:
        liquidity_limited = False
        f_final = kelly_f

    # 最终
    f_final = max(0.0, min(f_final, max_fraction))
    shares = int(f_final * portfolio_value / (atr_pct * 100)) if atr_pct > 0 else 0

    return {
        "f_final": round(f_final, 4),
        "shares": shares,
        "win_prob": round(win_prob, 4),
        "kelly_f": round(kelly_f, 4),
        "atr_limited": atr_limited,
        "liquidity_limited": liquidity_limited,
        "signal": "buy" if f_final >= MIN_POSITION_FRACTION else "insufficient_edge",
    }


def batch_ic_to_positions(
    ics: list[float],
    atr_pcts: list[float],
    portfolio_value: float = 1_000_000,
    daily_volumes: list[float] | None = None,
    ic_vol: float = DEFAULT_IC_VOL,
    max_fraction: float = MAX_POSITION_FRACTION,
) -> list[dict]:
    """批量IC→仓位计算。

    Args:
        ics: 各标的IC列表
        atr_pcts: 各标的ATR%
        portfolio_value: 组合总价值
        daily_volumes: 各标的日均成交额 (可选)
        ic_vol: IC历史波动率
        max_fraction: 单票最大仓位

    Returns:
        list of position dicts
    """
    results = []
    for i, (ic, atr_pct) in enumerate(zip(ics, atr_pcts)):
        dv = daily_volumes[i] if daily_volumes and i < len(daily_volumes) else 0
        pos = ic_to_position(
            ic=ic, ic_vol=ic_vol, atr_pct=atr_pct,
            portfolio_value=portfolio_value, daily_volume=dv,
            max_fraction=max_fraction,
        )
        results.append(pos)
    return results


# ─── CLI ───

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IC→Kelly仓位计算")
    parser.add_argument("--ic", type=float, required=True, help="模型IC")
    parser.add_argument("--ic-vol", type=float, default=DEFAULT_IC_VOL)
    parser.add_argument("--atr-pct", type=float, default=0.03)
    parser.add_argument("--portfolio", type=float, default=1_000_000)
    parser.add_argument("--volume", type=float, default=0)
    args = parser.parse_args()

    result = ic_to_position(
        ic=args.ic, ic_vol=args.ic_vol, atr_pct=args.atr_pct,
        portfolio_value=args.portfolio, daily_volume=args.volume,
    )

    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))
