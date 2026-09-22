"""信义光能(00968.HK) 盈利模型 — 价格×销量−成本 → 归母，含敏感性与一致预期对比。

模型骨架（以 2026H1 实际报表校准）:

    玻璃收入 = 日熔量 × 天数 × ㎡/吨 × 产能利用率 × 实现均价
    玻璃毛利 = 销量 × (实现均价 − 完全成本)
    总毛利   = 玻璃毛利 + 可再生能源毛利
    经营利润 = 总毛利 − opex(销售/行政/其他净额)
    净利润   = 经营利润 − 财务费用与税项
    少数股东 = 49.25% × 信义能源集团净利
    归母     = 净利润 − 减值 − 少数股东

关键洞察: 参数的不确定区间**恰好解释了整个卖方一致预期区间**——
    bull(Q4=11.0, 无减值) ≈ 376 百万 ≈ etnet 中位数 371
    bear(Q4=9.3, 6亿减值) ≈ -913 百万 ≈ 高盛最低值 -927
因此争论的从来不是"模型", 而是 **Q4 实现价能否站稳 + 年末是否再减值**。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from nous.core.paths import repo_root

MODEL_ENV = "NOUS_PVGLASS_MODEL"
MODEL_FILENAME = "pvglass_model.yaml"


@dataclass
class ScenarioResult:
    name: str
    label: str
    asp_q4: float
    impairment: float
    # 玻璃
    q3_revenue: float
    q3_gp: float
    q4_revenue: float
    q4_gp: float
    h2_glass_revenue: float
    h2_glass_gp: float
    h2_glass_gm: float
    # 合计
    h2_gp: float
    h2_opex: float
    h2_operating: float
    h2_net: float
    h2_minority: float
    h2_attributable: float
    fy_attributable: float
    fy_eps_fen: float
    pe: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def default_model_path() -> Path:
    import os

    override = os.environ.get(MODEL_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return repo_root() / "config" / MODEL_FILENAME


def load_model(path: str | Path | None = None) -> dict[str, Any]:
    model_path = Path(path).expanduser() if path else default_model_path()
    with model_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _area_quarter(model: dict[str, Any], utilization: float) -> float:
    """单季出货面积（百万㎡）。"""
    cap = model["capacity"]
    tonnes = _num(cap["t_per_day"]) * _num(cap["days_per_quarter"])
    sqm = tonnes * _num(cap["area_per_ton"]) * utilization
    return sqm / 1e6


def run(model: dict[str, Any], scenario: str = "base") -> ScenarioResult:
    """按情景计算 H2 与 FY2026 归母。"""
    h1 = model["h1_actual"]
    h2 = model["h2_model"]
    overrides = (model.get("scenarios") or {}).get(scenario) or {}

    asp_q3 = _num(h2["asp"]["q3"])
    asp_q4 = _num(overrides.get("asp_q4", h2["asp"]["q4"]))
    unit_cost = _num(h2["unit_cost"])
    util_q3 = _num(h2["utilization"]["q3"])
    util_q4 = _num(h2["utilization"]["q4"])

    vol_q3 = _area_quarter(model, util_q3)
    vol_q4 = _area_quarter(model, util_q4)

    q3_revenue = vol_q3 * asp_q3
    q4_revenue = vol_q4 * asp_q4
    q3_gp = vol_q3 * (asp_q3 - unit_cost)
    q4_gp = vol_q4 * (asp_q4 - unit_cost)

    h2_glass_revenue = q3_revenue + q4_revenue
    h2_glass_gp = q3_gp + q4_gp
    h2_glass_gm = (h2_glass_gp / h2_glass_revenue * 100) if h2_glass_revenue else 0.0

    renewable_revenue = _num(h2["renewable_revenue"])
    renewable_gm = _num(h2["renewable_gm"])
    h2_gp = h2_glass_gp + renewable_revenue * renewable_gm

    opex = _num(overrides.get("opex", h2["opex"]))
    h2_operating = h2_gp - opex
    h2_net = h2_operating - _num(h2["finance_tax"])

    net_factor = _num(overrides.get("renewable_group_net_factor", h2["renewable_group_net_factor"]))
    renewable_group_net = _num(h1["renewable_group_net"]) * net_factor
    h2_minority = renewable_group_net * _num(h2["minority_ratio"])

    impairment = _num(overrides.get("impairment", h2["impairment"]))
    h2_attributable = h2_net - impairment - h2_minority
    fy_attributable = _num(h1["attributable"]) + h2_attributable

    shares = _num(model["company"]["shares_million"], 1.0)
    eps_fen = (fy_attributable / shares * 100) if shares else 0.0

    price = _num(model["company"]["price_hkd"])
    fx = _num(model["company"]["hkd_cny"], 1.0)
    pe: float | None = None
    if fy_attributable > 0:
        market_cap_cny = price * shares * fx  # 百万人民币
        pe = market_cap_cny / fy_attributable

    return ScenarioResult(
        name=scenario,
        label=str(overrides.get("label", scenario)),
        asp_q4=asp_q4,
        impairment=impairment,
        q3_revenue=q3_revenue,
        q3_gp=q3_gp,
        q4_revenue=q4_revenue,
        q4_gp=q4_gp,
        h2_glass_revenue=h2_glass_revenue,
        h2_glass_gp=h2_glass_gp,
        h2_glass_gm=h2_glass_gm,
        h2_gp=h2_gp,
        h2_opex=opex,
        h2_operating=h2_operating,
        h2_net=h2_net,
        h2_minority=h2_minority,
        h2_attributable=h2_attributable,
        fy_attributable=fy_attributable,
        fy_eps_fen=eps_fen,
        pe=pe,
    )


def run_all(model: dict[str, Any]) -> list[ScenarioResult]:
    names = list((model.get("scenarios") or {}).keys()) or ["base"]
    return [run(model, name) for name in names]


def sensitivity(
    model: dict[str, Any],
    *,
    asp_min: float = 9.0,
    asp_max: float = 11.2,
    step: float = 0.2,
    scenario: str = "base",
) -> list[dict[str, float]]:
    """Q4 实现均价 → FY2026 归母 的敏感性（其余参数固定为 base 情景）。"""
    base_overrides = (model.get("scenarios") or {}).get(scenario) or {}
    out: list[dict[str, float]] = []
    asp = asp_min
    while asp <= asp_max + 1e-9:
        patched = dict(model)
        scenarios = dict(patched.get("scenarios") or {})
        scenarios["_probe"] = {**base_overrides, "asp_q4": round(asp, 4), "impairment": 0.0}
        patched["scenarios"] = scenarios
        result = run(patched, "_probe")
        out.append(
            {
                "asp_q4": round(asp, 4),
                "glass_gm": result.h2_glass_gm,
                "fy_attributable": result.fy_attributable,
                "pe": result.pe or 0.0,
            }
        )
        asp += step
    return out


def vs_consensus(model: dict[str, Any], results: list[ScenarioResult]) -> list[dict[str, Any]]:
    """情景 vs 一致预期中位数。"""
    ref = model.get("consensus_ref") or {}
    rows: list[dict[str, Any]] = []
    for key, label in (("fy2026_median", "FY2026中位数"), ("fy2027_median", "FY2027中位数")):
        value = ref.get(key)
        if value is None:
            continue
        rows.append(
            {
                "benchmark": label,
                "value": _num(value),
                "base": results[1].fy_attributable if len(results) > 1 else 0.0,
                "gap_pct": (
                    (results[1].fy_attributable - _num(value)) / abs(_num(value)) * 100
                    if len(results) > 1 and _num(value)
                    else 0.0
                ),
            }
        )
    return rows
