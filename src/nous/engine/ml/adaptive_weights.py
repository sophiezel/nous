"""
策略自适应权重 — 根据市场状态动态调整因子权重和风控参数

数据来源: market_regime.predict_current_regime() 的输出
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── 每个市场状态的因子权重 + 风控配置 ──────────────────────────

REGIME_CONFIGS = {
    "BULL": {
        "name": "牛市",
        "description": "趋势向上，动量因子主导，可高仓位",
        "factor_weights": {
            "momentum": 1.5,
            "value": 0.8,
            "volatility": 1.0,
            "reversal": 0.5,
        },
        "position_limit": 0.95,  # 最大仓位 95%
        "stop_loss_atr_mult": 2.0,  # 止损 ATR 倍数 (较宽松)
        "take_profit_atr_mult": 4.0,
        "rebalance_frequency": 5,  # 每 5 个交易日再平衡
        "max_single_weight": 0.10,  # 单只最大权重 10%
        "min_position": 0.80,  # 最低仓位 80%
    },
    "BEAR": {
        "name": "熊市",
        "description": "趋势向下，防御为主，低仓位 + 价值因子",
        "factor_weights": {
            "momentum": 0.3,
            "value": 1.5,
            "volatility": 0.7,
            "reversal": 1.0,
        },
        "position_limit": 0.30,  # 最大仓位 30%
        "stop_loss_atr_mult": 1.2,  # 止损严格
        "take_profit_atr_mult": 2.0,
        "rebalance_frequency": 10,  # 减少交易频率
        "max_single_weight": 0.05,
        "min_position": 0.0,  # 可以空仓
    },
    "SIDEWAYS": {
        "name": "震荡市",
        "description": "横盘震荡，反转因子 + 价值因子，中等仓位",
        "factor_weights": {
            "momentum": 0.7,
            "value": 1.2,
            "volatility": 0.8,
            "reversal": 1.5,
        },
        "position_limit": 0.60,
        "stop_loss_atr_mult": 1.5,
        "take_profit_atr_mult": 3.0,
        "rebalance_frequency": 10,
        "max_single_weight": 0.08,
        "min_position": 0.30,
    },
    "VOLATILE": {
        "name": "高波动",
        "description": "波动剧烈，波动率因子主导，低仓位 + 快速再平衡",
        "factor_weights": {
            "momentum": 0.5,
            "value": 0.5,
            "volatility": 1.5,
            "reversal": 0.8,
        },
        "position_limit": 0.40,
        "stop_loss_atr_mult": 1.0,  # 止损非常严格
        "take_profit_atr_mult": 2.5,
        "rebalance_frequency": 3,  # 高频再平衡
        "max_single_weight": 0.05,
        "min_position": 0.0,
    },
}

# ── 默认配置（当状态无法识别时使用） ──
DEFAULT_REGIME = "SIDEWAYS"


def get_regime_config(regime: str) -> dict:
    """
    根据市场状态返回模型权重和风控参数。

    Args:
        regime: 市场状态 (BULL/BEAR/SIDEWAYS/VOLATILE)

    Returns:
        dict with keys:
            factor_weights, position_limit, stop_loss_atr_mult,
            take_profit_atr_mult, rebalance_frequency,
            max_single_weight, min_position
    """
    config = REGIME_CONFIGS.get(regime.upper(), REGIME_CONFIGS[DEFAULT_REGIME])
    return dict(config)  # 返回副本，避免意外修改


def apply_regime_to_screener(
    regime: str,
    base_score_weights: dict | None = None,
) -> dict:
    """
    将市场状态转换为筛选器权重调整。

    返回调整后的评分权重:
        {
            "value_weight": float,
            "trend_weight": float,
            "volume_weight": float,
            "reversal_weight": float,
            "regime": str,
            "regime_label": str,
        }
    """
    config = get_regime_config(regime)
    fw = config["factor_weights"]

    # 将因子权重映射到 screener 的三个维度
    # momentum -> trend, value -> value, volatility -> volume
    adjusted = {
        "value_weight": fw.get("value", 1.0),
        "trend_weight": fw.get("momentum", 1.0),
        "volume_weight": fw.get("volatility", 0.8),
        "reversal_weight": fw.get("reversal", 0.5),
        "regime": regime,
        "regime_label": config["name"],
        "position_limit": config["position_limit"],
        "stop_loss_atr_mult": config["stop_loss_atr_mult"],
        "rebalance_frequency": config["rebalance_frequency"],
    }

    # 如果传入了 base_weights，合并
    if base_score_weights:
        adjusted["value_weight"] = (
            base_score_weights.get("value_weight", 1.0) * adjusted["value_weight"]
        )
        adjusted["trend_weight"] = (
            base_score_weights.get("trend_weight", 1.0) * adjusted["trend_weight"]
        )
        adjusted["volume_weight"] = (
            base_score_weights.get("volume_weight", 1.0) * adjusted["volume_weight"]
        )

    return adjusted


def get_risk_limits(regime: str) -> dict:
    """
    获取风控参数。

    返回:
        {
            "max_position": float,
            "stop_loss_atr": float,
            "take_profit_atr": float,
            "rebalance_days": int,
            "max_single_stock": float,
            "min_position": float,
        }
    """
    config = get_regime_config(regime)
    return {
        "max_position": config["position_limit"],
        "stop_loss_atr": config["stop_loss_atr_mult"],
        "take_profit_atr": config["take_profit_atr_mult"],
        "rebalance_days": config["rebalance_frequency"],
        "max_single_stock": config["max_single_weight"],
        "min_position": config["min_position"],
    }


def get_factor_weights(regime: str) -> dict:
    """仅返回因子权重字典"""
    config = get_regime_config(regime)
    return dict(config["factor_weights"])


def list_available_configs() -> list[dict]:
    """列出所有状态的配置摘要"""
    results = []
    for regime, config in REGIME_CONFIGS.items():
        results.append({
            "regime": regime,
            "name": config["name"],
            "description": config["description"],
            "position_limit": config["position_limit"],
            "rebalance_frequency": config["rebalance_frequency"],
            "top_factors": sorted(
                config["factor_weights"].items(),
                key=lambda x: x[1],
                reverse=True,
            )[:2],
        })
    return results


def main():
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="策略自适应权重")
    parser.add_argument("--regime", type=str, help="市场状态 (BULL/BEAR/SIDEWAYS/VOLATILE)")
    parser.add_argument("--list", action="store_true", help="列出所有配置")
    parser.add_argument("--map", action="store_true", help="显示因子映射")
    args = parser.parse_args()

    if args.list:
        print(f"\n{'=' * 60}")
        print(f"  市场状态配置一览")
        print(f"{'=' * 60}")
        for cfg in list_available_configs():
            factors_str = ", ".join(
                f"{f[0]}={f[1]:.1f}" for f in cfg["top_factors"]
            )
            print(f"\n  {cfg['regime']:>10s} ({cfg['name']})")
            print(f"    {cfg['description']}")
            print(f"    仓位上限:    {cfg['position_limit']:.0%}")
            print(f"    再平衡周期:  {cfg['rebalance_frequency']} 日")
            print(f"    主导因子:    {factors_str}")
        print()

    elif args.regime:
        config = get_regime_config(args.regime.upper())
        fw = config["factor_weights"]
        print(f"\n{'=' * 60}")
        print(f"  {args.regime.upper()} ({config['name']}) — 配置详情")
        print(f"{'=' * 60}")
        print(f"  {config['description']}")
        print(f"\n  因子权重:")
        for factor, weight in sorted(fw.items(), key=lambda x: x[1], reverse=True):
            bar = "█" * int(weight * 10)
            print(f"    {factor:>12s}: {weight:.1f}  {bar}")
        print(f"\n  风控参数:")
        risks = get_risk_limits(args.regime.upper())
        for k, v in risks.items():
            if k in ("max_position", "min_position", "max_single_stock"):
                print(f"    {k:>20s}: {v:.0%}")
            else:
                print(f"    {k:>20s}: {v}")
        print(f"{'=' * 60}\n")

    elif args.map:
        # 显示因子映射规则
        print(f"\n{'=' * 60}")
        print(f"  因子 → 筛选器维度映射")
        print(f"{'=' * 60}")
        print(f"  momentum  → trend_weight   (趋势维度)")
        print(f"  value     → value_weight   (价值维度)")
        print(f"  volatility→ volume_weight  (量价维度)")
        print(f"  reversal  → reversal_weight(反转维度)")
        print(f"{'=' * 60}\n")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
