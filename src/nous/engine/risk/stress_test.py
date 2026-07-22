"""
压力测试 — 5 大场景
====================
简化假设: 组合 beta=1, 直接按指数冲击估算损失.

场景:
  1. covid_crash    — -15% 指数冲击 + 波动率飙升 + 相关性上升 + 流动性收紧
  2. 2015_crash     — -30% 指数冲击 + 波动剧增 + 流动性枯竭
  3. trade_war      — -10% 指数 + -20% 科技 + -15% 制造
  4. liquidity_crisis — -70% 成交量冲击 + 买卖价差飙至 2%
  5. rate_hike      — 加息 200bp + 债券收益率飙升 2%
"""

from typing import Dict, Any

# ---------------------------------------------------------------------------
# 场景定义
# ---------------------------------------------------------------------------

SCENARIOS: Dict[str, Dict[str, Any]] = {
    # 1. 新冠式暴跌
    "covid_crash": {
        "index_shock": -0.15,
        "vol_spike": 3.0,
        "correlation": 0.9,
        "liquidity": 0.3,
        "description": "新冠式崩盘 (-15% 指数, 波动率 3x)",
    },
    # 2. 2015 年股灾
    "2015_crash": {
        "index_shock": -0.30,
        "vol_spike": 5.0,
        "liquidity_dry": True,
        "description": "2015 年股灾式崩盘 (-30% 指数, 流动性枯竭)",
    },
    # 3. 贸易战
    "trade_war": {
        "index_shock": -0.10,
        "tech_shock": -0.20,
        "manufacturing_shock": -0.15,
        "description": "贸易战冲击 (-10% 指数, -20% 科技, -15% 制造)",
    },
    # 4. 流动性危机
    "liquidity_crisis": {
        "volume_shock": -0.70,
        "bid_ask_spread": 0.02,
        "description": "流动性危机 (-70% 成交, 买卖价差 2%)",
    },
    # 5. 加息冲击
    "rate_hike": {
        "rate_shock_bp": 200,
        "bond_yield_spike": 0.02,
        "description": "加息 200bp + 债券收益率 +2%",
    },
}


# ---------------------------------------------------------------------------
# 单场景测试
# ---------------------------------------------------------------------------

def run_stress_test(
    holdings: Dict[str, float],
    scenario_name: str,
) -> Dict[str, Any]:
    """
    运行单场景压力测试

    参数
    ----
    holdings : dict
        {symbol: weight}, 权重之和代表总资金 (以 1.0 归一化)
    scenario_name : str
        SCENARIOS 字典中的键

    返回
    ----
    dict
        {
            'scenario':          str,
            'description':       str,
            'shock_pct':         float,    # 指数冲击比例
            'estimated_loss':    float,    # 估计损失 (人民币单位)
            'surviving_capital': float,    # 剩余资金
            'vol_spike':         float|None,
            'liquidity_dry':     bool|None,
        }
    """
    if scenario_name not in SCENARIOS:
        raise KeyError(f"未知场景: {scenario_name}, 可选: {list(SCENARIOS.keys())}")

    scenario = SCENARIOS[scenario_name]
    total_weight = sum(holdings.values())

    # 简化: 假设组合 beta=1, 直接按指数冲击估算
    shock = scenario.get("index_shock", 0.0)
    estimated_loss = total_weight * shock
    surviving_capital = total_weight + estimated_loss

    result: Dict[str, Any] = {
        "scenario": scenario_name,
        "description": scenario.get("description", ""),
        "shock_pct": shock,
        "estimated_loss": round(float(estimated_loss), 4),
        "surviving_capital": round(float(surviving_capital), 4),
    }

    # 附加场景特征
    for key in ("vol_spike", "liquidity_dry", "rate_shock_bp", "bid_ask_spread"):
        if key in scenario:
            result[key] = scenario[key]

    return result


# ---------------------------------------------------------------------------
# 全场景运行
# ---------------------------------------------------------------------------

def run_all_stress_tests(
    holdings: Dict[str, float],
) -> Dict[str, Dict[str, Any]]:
    """
    运行全部 5 个压力测试场景

    参数
    ----
    holdings : dict
        {symbol: weight}

    返回
    ----
    dict
        {场景名: 测试结果 dict}
    """
    results: Dict[str, Dict[str, Any]] = {}
    for name in SCENARIOS:
        results[name] = run_stress_test(holdings, name)
    return results
