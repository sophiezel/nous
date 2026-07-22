"""绩效归因分析：Brinson 模型分解组合收益来源"""
from dataclasses import dataclass, field
from typing import Optional
import math


# ── 简化版行业板块映射（申万一级行业） ──
# 仅做演示；实际应接入 wind / tushare 行业分类
SECTOR_ALIASES = {
    "银行": "金融",
    "证券": "金融",
    "保险": "金融",
    "房地产": "地产",
    "煤炭": "周期",
    "钢铁": "周期",
    "有色": "周期",
    "化工": "周期",
    "建材": "周期",
    "建筑": "基建",
    "机械": "制造",
    "电气设备": "制造",
    "汽车": "制造",
    "国防军工": "军工",
    "计算机": "TMT",
    "电子": "TMT",
    "通信": "TMT",
    "传媒": "TMT",
    "医药生物": "医药",
    "食品饮料": "消费",
    "商贸零售": "消费",
    "农林牧渔": "消费",
    "家用电器": "消费",
    "纺织服装": "消费",
    "轻工制造": "制造",
    "公用事业": "公用",
    "交通运输": "公用",
    "环保": "公用",
    "综合": "其他",
}


def _normalize_sector(sector: str) -> str:
    """将细分行业映射到大类板块"""
    return SECTOR_ALIASES.get(sector, sector)


# ── 数据类 ──────────────────────────────────────


@dataclass
class AttributionResult:
    """Brinson 绩效归因结果"""

    total_excess: float = 0.0  # 超额收益（累计）
    allocation_effect: float = 0.0  # 配置效应（累计）
    selection_effect: float = 0.0  # 选股效应（累计）
    interaction_effect: float = 0.0  # 交互效应（累计）
    breakdown_by_sector: dict = field(default_factory=dict)
    # 示例：{ "金融": {"allocation": 0.02, "selection": 0.015, "interaction": -0.005} }

    def report(self) -> str:
        """生成可读归因报告"""
        lines = []
        lines.append("=" * 60)
        lines.append("Brinson 绩效归因报告")
        lines.append("=" * 60)
        lines.append(f"  超额收益（累计）:     {self.total_excess:>+7.2%}")
        lines.append(f"  板块配置效应:         {self.allocation_effect:>+7.2%}")
        lines.append(f"  选股效应:             {self.selection_effect:>+7.2%}")
        lines.append(f"  交互效应:             {self.interaction_effect:>+7.2%}")
        lines.append("")

        if self.breakdown_by_sector:
            lines.append(f"{'板块':<12} {'配置效应':>10} {'选股效应':>10} {'交互效应':>10} {'合计':>10}")
            lines.append("-" * 52)
            for sector, effects in sorted(self.breakdown_by_sector.items()):
                total = (
                    effects.get("allocation", 0)
                    + effects.get("selection", 0)
                    + effects.get("interaction", 0)
                )
                lines.append(
                    f"{sector:<12} {effects.get('allocation', 0):>+10.2%} "
                    f"{effects.get('selection', 0):>+10.2%} "
                    f"{effects.get('interaction', 0):>+10.2%} "
                    f"{total:>+10.2%}"
                )
            lines.append("-" * 52)

        lines.append("=" * 60)
        return "\n".join(lines)


@dataclass
class SectorBreakdown:
    """简化版板块收益分解（无基准时使用）"""

    total_return: float = 0.0
    breakdown_by_sector: dict = field(default_factory=dict)
    # {sector: {"weight": 0.3, "return": 0.05, "contribution": 0.015}}

    def report(self) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append("板块收益分解（无基准）")
        lines.append("=" * 60)
        lines.append(f"  组合总收益: {self.total_return:>+7.2%}")
        lines.append("")
        if self.breakdown_by_sector:
            lines.append(f"{'板块':<12} {'权重':>8} {'收益':>10} {'贡献':>10}")
            lines.append("-" * 40)
            for sector, info in sorted(
                self.breakdown_by_sector.items(),
                key=lambda x: abs(x[1].get("contribution", 0)),
                reverse=True,
            ):
                lines.append(
                    f"{sector:<12} {info.get('weight', 0):>8.1%} "
                    f"{info.get('return', 0):>+10.2%} "
                    f"{info.get('contribution', 0):>+10.2%}"
                )
            lines.append("-" * 40)
        lines.append("=" * 60)
        return "\n".join(lines)


# ── Brinson 归因核心函数 ─────────────────────


def brinson_attribution(
    portfolio_returns: list[float],
    benchmark_returns: list[float],
    portfolio_weights_by_sector: dict,
    benchmark_weights_by_sector: dict,
    portfolio_sector_returns: dict,
    benchmark_sector_returns: dict,
) -> AttributionResult:
    """Brinson 归因分解

    Args:
        portfolio_returns: 组合日收益序列 [r1, r2, ...]
        benchmark_returns: 基准日收益序列 [r1, r2, ...]
        portfolio_weights_by_sector: {日期: {板块: 权重}}
        benchmark_weights_by_sector: {日期: {板块: 权重}}
        portfolio_sector_returns:   {日期: {板块: 收益}}
        benchmark_sector_returns:   {日期: {板块: 收益}}

    公式（日频计算后累计）：
        Allocation   = Σ (w_p - w_b) × r_b
        Selection    = Σ w_b × (r_p - r_b)
        Interaction  = Σ (w_p - w_b) × (r_p - r_b)
        Total Excess = Allocation + Selection + Interaction
    """
    result = AttributionResult()

    if (
        not portfolio_returns
        or not benchmark_returns
        or not portfolio_weights_by_sector
        or not benchmark_weights_by_sector
    ):
        return result

    # 收集所有板块（全周期并集）
    all_sectors = set()
    for dates_map in [
        portfolio_weights_by_sector,
        benchmark_weights_by_sector,
        portfolio_sector_returns,
        benchmark_sector_returns,
    ]:
        for d, secs in dates_map.items():
            all_sectors.update(secs.keys())
    all_sectors = sorted(all_sectors)

    # 按日期对齐
    dates = sorted(set(portfolio_weights_by_sector.keys()) & set(benchmark_weights_by_sector.keys()))
    if not dates:
        return result

    # 逐日计算
    total_alloc = 0.0
    total_select = 0.0
    total_interact = 0.0
    sector_accum = {s: {"allocation": 0.0, "selection": 0.0, "interaction": 0.0} for s in all_sectors}

    for date in dates:
        wp = portfolio_weights_by_sector.get(date, {})
        wb = benchmark_weights_by_sector.get(date, {})
        rp = portfolio_sector_returns.get(date, {})
        rb = benchmark_sector_returns.get(date, {})

        for sector in all_sectors:
            w_p = wp.get(sector, 0.0)
            w_b = wb.get(sector, 0.0)
            r_p = rp.get(sector, 0.0)
            r_b = rb.get(sector, 0.0)

            # Brinson 公式
            alloc = (w_p - w_b) * r_b
            select = w_b * (r_p - r_b)
            interact = (w_p - w_b) * (r_p - r_b)

            total_alloc += alloc
            total_select += select
            total_interact += interact

            sector_accum[sector]["allocation"] += alloc
            sector_accum[sector]["selection"] += select
            sector_accum[sector]["interaction"] += interact

    # 累加日收益得到累计超额
    # 直接用超额收益序列累加（更准确）
    if len(portfolio_returns) == len(benchmark_returns) and len(portfolio_returns) > 0:
        excess_returns = [
            (1 + p) / (1 + b) - 1
            for p, b in zip(portfolio_returns, benchmark_returns)
        ]
        total_excess_cum = 1.0
        for er in excess_returns:
            total_excess_cum *= 1 + er
        result.total_excess = total_excess_cum - 1.0
    else:
        result.total_excess = total_alloc + total_select + total_interact

    result.allocation_effect = total_alloc
    result.selection_effect = total_select
    result.interaction_effect = total_interact

    # 板块明细（保留有效值 > 1e-10 的）
    result.breakdown_by_sector = {}
    for s in all_sectors:
        a = sector_accum[s]["allocation"]
        b = sector_accum[s]["selection"]
        c = sector_accum[s]["interaction"]
        if abs(a) > 1e-10 or abs(b) > 1e-10 or abs(c) > 1e-10:
            result.breakdown_by_sector[s] = {
                "allocation": round(a, 6),
                "selection": round(b, 6),
                "interaction": round(c, 6),
            }

    return result


# ── 简化版：板块收益分解（无基准） ──────────


def sector_breakdown(
    daily_holdings: list[dict],
    equity_curve: list[dict],
    sector_map: Optional[dict] = None,
) -> SectorBreakdown:
    """将组合收益按板块分解（无需基准数据）

    Args:
        daily_holdings: 每日持仓 [{date, symbol, weight, sector}, ...]
        equity_curve:   权益曲线 [{date, equity}, ...]
        sector_map:     {symbol: sector} 可选映射（不提供则用 daily_holdings 中的 sector）

    Returns:
        SectorBreakdown
    """
    result = SectorBreakdown()

    if not equity_curve:
        return result

    # 总收益
    first_eq = equity_curve[0]["equity"]
    last_eq = equity_curve[-1]["equity"]
    if first_eq > 0:
        result.total_return = round((last_eq - first_eq) / first_eq, 6)

    if not daily_holdings:
        return result

    # 按板块聚合权重和收益
    # 将数据按日期分组
    from collections import defaultdict

    holdings_by_date = defaultdict(list)
    for h in daily_holdings:
        dt = h["date"]
        sec = h.get("sector", "未知")
        if sector_map:
            sec = sector_map.get(h["symbol"], sec)
        sec = _normalize_sector(sec)
        holdings_by_date[dt].append(
            {
                "symbol": h["symbol"],
                "weight": h.get("weight", 0),
                "sector": sec,
            }
        )

    # 板块加权收益
    sector_stats = defaultdict(lambda: {"weight_sum": 0.0, "return_sum": 0.0, "count": 0})

    for i in range(1, len(equity_curve)):
        cur_date = equity_curve[i]["date"]
        prev_eq = equity_curve[i - 1]["equity"]
        cur_eq = equity_curve[i]["equity"]
        if prev_eq <= 0:
            continue
        daily_ret = (cur_eq - prev_eq) / prev_eq

        holdings = holdings_by_date.get(cur_date, [])
        if not holdings:
            continue

        total_weight = sum(h["weight"] for h in holdings)
        if total_weight <= 0:
            continue

        for h in holdings:
            sec = h["sector"]
            w = h["weight"]
            # 收益按权重比例分配
            sector_stats[sec]["weight_sum"] += w
            sector_stats[sec]["return_sum"] += daily_ret * (w / total_weight) if total_weight > 0 else 0
            sector_stats[sec]["count"] += 1

    # 计算板块贡献
    total_weight_all = sum(v["weight_sum"] for v in sector_stats.values())
    if total_weight_all > 0:
        for sec, stats in sorted(sector_stats.items()):
            avg_weight = stats["weight_sum"] / stats["count"] if stats["count"] > 0 else 0
            contribution = stats["return_sum"]
            # 归一化权重
            norm_weight = stats["weight_sum"] / total_weight_all if total_weight_all > 0 else 0
            result.breakdown_by_sector[sec] = {
                "weight": round(norm_weight, 4),
                "return": round(stats["return_sum"], 6),
                "contribution": round(contribution, 6),
            }

    return result


# ── 独立运行验证 ───────────────────────────────


def _demo():
    """生成模拟数据演示 Brinson 归因"""
    import random

    random.seed(42)

    sectors = ["金融", "消费", "TMT", "医药", "制造", "周期", "公用"]
    dates = [f"2025-01-{(i+1):02d}" for i in range(20)]

    # 组合权重
    pw = {}
    bw = {}
    pr = {}
    br = {}

    for d in dates:
        # 基准：接近等权
        wb = {s: 1.0 / len(sectors) for s in sectors}
        # 组合：超配 TMT、医药，低配周期
        wp = {
            "金融": 0.12,
            "消费": 0.14,
            "TMT": 0.22,
            "医药": 0.20,
            "制造": 0.14,
            "周期": 0.08,
            "公用": 0.10,
        }
        # 收益
        r_b = {s: random.uniform(-0.02, 0.03) for s in sectors}
        r_p = {s: r_b[s] + random.uniform(-0.01, 0.015) for s in sectors}

        pw[d] = wp
        bw[d] = wb
        pr[d] = r_p
        br[d] = r_b

    # 组合/基准日收益（加权平均）
    port_ret = []
    bench_ret = []
    for d in dates:
        pr_d = sum(pw[d].get(s, 0) * pr[d].get(s, 0) for s in sectors)
        br_d = sum(bw[d].get(s, 0) * br[d].get(s, 0) for s in sectors)
        port_ret.append(pr_d)
        bench_ret.append(br_d)

    # 运行归因
    result = brinson_attribution(
        portfolio_returns=port_ret,
        benchmark_returns=bench_ret,
        portfolio_weights_by_sector=pw,
        benchmark_weights_by_sector=bw,
        portfolio_sector_returns=pr,
        benchmark_sector_returns=br,
    )

    print(result.report())

    # 演示简化版
    print("\n\n简化版板块收益分解演示：\n")
    demo_holdings = [
        {"date": "2025-01-02", "symbol": "000001", "weight": 0.15, "sector": "金融"},
        {"date": "2025-01-02", "symbol": "000002", "weight": 0.15, "sector": "地产"},
        {"date": "2025-01-02", "symbol": "600519", "weight": 0.20, "sector": "消费"},
        {"date": "2025-01-02", "symbol": "300750", "weight": 0.25, "sector": "制造"},
        {"date": "2025-01-02", "symbol": "688981", "weight": 0.25, "sector": "TMT"},
    ]
    demo_curve = [
        {"date": "2025-01-02", "equity": 1_000_000},
        {"date": "2025-01-03", "equity": 1_020_000},
        {"date": "2025-01-06", "equity": 1_050_000},
        {"date": "2025-01-07", "equity": 1_030_000},
        {"date": "2025-01-08", "equity": 1_080_000},
        {"date": "2025-01-09", "equity": 1_100_000},
    ]
    sb = sector_breakdown(demo_holdings, demo_curve)
    print(sb.report())


if __name__ == "__main__":
    _demo()
