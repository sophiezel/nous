#!/usr/bin/env python3
"""压力测试引擎

预置 5 个极端情景，估算组合在各情景下的损失。

用法:
    python3 stress_test.py

核心函数:
    run_stress_test(positions, total_asset) -> StressTestReport

依赖:
    - numpy
    - screener.db (~/code/stock-screener/data/screener.db)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional
import math
import sys
import os

import numpy as np

# ── 修正导入路径 ──────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."  # to nous repo root))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from nous.data.storage import get_daily_range, get_db  # noqa: E402


# ============================================================
# 预置极端情景
# ============================================================
SCENARIOS = {
    "hk_crash": {
        "name": "恒指单日暴跌8%",
        "description": "类似2024年10月港股流动性危机",
        "index_shocks": {"hsi": -0.08, "csi300": -0.03},
        "correlation_boost": 1.2,
    },
    "a_share_shock": {
        "name": "A股单日暴跌5%",
        "description": "类似2024年2月A股流动性冲击",
        "index_shocks": {"csi300": -0.05, "hsi": -0.02},
        "correlation_boost": 1.1,
    },
    "trade_war": {
        "name": "中美关税再升级",
        "description": "关税加征20%+科技制裁",
        "index_shocks": {"csi300": -0.04, "hsi": -0.06},
        "correlation_boost": 1.3,
    },
    "rate_hike": {
        "name": "央行意外加息50bp",
        "description": "货币政策急转弯",
        "index_shocks": {"csi300": -0.03, "hsi": -0.04},
        "correlation_boost": 1.0,
    },
    "liquidity_crunch": {
        "name": "流动性危机",
        "description": "短期利率飙升+信用利差扩大",
        "index_shocks": {"csi300": -0.06, "hsi": -0.03},
        "correlation_boost": 1.4,
    },
}

# ============================================================
# 板块映射 & 经验 Beta
# ============================================================
SECTOR_MAP: dict[str, str] = {
    "白酒": "消费",
    "银行": "金融",
    "保险": "金融",
    "券商": "金融",
    "光伏": "新能源",
    "锂电池": "新能源",
    "风电": "新能源",
    "半导体": "科技",
    "芯片": "科技",
    "AI": "科技",
    "软件": "科技",
    "医药": "医药",
    "医疗器械": "医药",
    "房地产": "地产",
    "建材": "地产",
    "汽车": "汽车",
    "零部件": "汽车",
}

BETA_EMPIRICAL: dict[str, float] = {
    "消费": 0.9,
    "金融": 1.0,
    "新能源": 1.3,
    "科技": 1.2,
    "医药": 0.8,
    "地产": 1.1,
    "汽车": 1.1,
}

# 市场→基准指数映射
MARKET_INDEX_MAP: dict[str, str] = {
    "a": "csi300",
    "h": "hsi",
    "hk": "hsi",
}

# 沪深300 & 恒指在数据库中的可能代码
CSI300_SYMBOLS = ["000300", "399300", "CSI300", "sh000300", "sz399300"]
HSI_SYMBOLS = ["HSI", "000800", "hkHSI", "HSI.HK"]


# ============================================================
# 结果结构
# ============================================================
@dataclass
class StressTestResult:
    """单个情景的压力测试结果"""

    scenario_name: str
    description: str
    portfolio_loss_pct: float  # 组合估计损失%
    portfolio_loss_amount: float  # 组合估计损失金额
    worst_position: str  # 最大受损标的
    worst_position_loss: float  # 最大单票损失%
    margin_call_risk: bool  # 是否有爆仓风险
    recovery_time_estimate: str  # 恢复时间估计


@dataclass
class StressTestReport:
    """压力测试报告"""

    scenarios: list[StressTestResult]
    total_exposure: float  # 当前总仓位
    max_stress_loss: float  # 最坏情景损失
    worst_scenario: str  # 最坏情景名

    def report(self) -> str:
        """生成可视化报告文本"""
        lines: list[str] = []
        lines.append("═══════════════════════════════════════════════")
        lines.append("        组合压力测试报告")
        lines.append("═══════════════════════════════════════════════")
        lines.append("")
        lines.append(f"  当前总仓位: {self.total_exposure:.1%}")
        lines.append(
            f"  最坏情景: {self.worst_scenario} "
            f"(损失 {self.max_stress_loss:.2%})"
        )
        lines.append("")
        lines.append("── 各情景详情 ──")
        lines.append("")

        for s in self.scenarios:
            lines.append(f"  【{s.scenario_name}】{s.description}")
            lines.append(f"    组合估计损失: {s.portfolio_loss_pct:.2%}  "
                         f"(¥{s.portfolio_loss_amount:,.2f})")
            lines.append(f"    最大受损标的: {s.worst_position} "
                         f"({s.worst_position_loss:.2%})")
            risk_label = "⚠️ 有爆仓风险" if s.margin_call_risk else "✅ 安全"
            lines.append(f"    爆仓风险: {risk_label}")
            lines.append(f"    恢复时间估计: {s.recovery_time_estimate}")
            lines.append("")

        lines.append("── 风险告警 ──")
        if any(s.margin_call_risk for s in self.scenarios):
            lines.append("  ⚠️ 部分情景存在爆仓风险，建议减仓或对冲")
        if self.max_stress_loss > 0.20:
            lines.append("  ⚠️ 最坏情景损失超过20%，组合脆弱度偏高")
        if self.max_stress_loss > 0.30:
            lines.append("  ⚠️ 最坏情景损失超过30%，建议立即审视持仓")
        lines.append("")
        lines.append("═══════════════════════════════════════════════")

        return "\n".join(lines)


# ============================================================
# 压力测试引擎
# ============================================================
class StressTester:
    """压力测试执行引擎"""

    def __init__(self, lookback_days: int = 60):
        self.lookback_days = lookback_days
        # 缓存指数日线（避免反复查询）
        self._index_cache: dict[str, np.ndarray] = {}

    # ── 数据获取 ────────────────────────────────────

    def _fetch_daily(self, symbol: str) -> list[float]:
        """获取标的最新 lookback_days+1 条日线收盘价（保证有收益率计算）"""
        end = date.today()
        start = end - timedelta(days=self.lookback_days * 2)
        rows = get_daily_range(symbol, start.isoformat(), end.isoformat())
        closes = [r["close"] for r in rows if r["close"] is not None]
        if len(closes) > self.lookback_days + 1:
            closes = closes[-(self.lookback_days + 1):]
        return closes

    def _fetch_index_daily(self, symbol: str) -> list[float]:
        """从数据库查找指数日线"""
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT 1 FROM stock_daily WHERE symbol=? LIMIT 1",
                (symbol,),
            ).fetchone()
        finally:
            conn.close()
        if row:
            return self._fetch_daily(symbol)
        return []

    def _find_index(self, candidates: list[str]) -> list[float]:
        """遍历候选代码查找指数日线"""
        for sym in candidates:
            data = self._fetch_index_daily(sym)
            if len(data) >= 10:
                return data
        return []

    # ── 指数收益率 ──────────────────────────────────

    def _get_index_returns(self, index_name: str) -> np.ndarray:
        """获取指数日收益率，带缓存"""
        if index_name in self._index_cache:
            return self._index_cache[index_name]

        if index_name == "csi300":
            closes = self._find_index(CSI300_SYMBOLS)
        elif index_name == "hsi":
            closes = self._find_index(HSI_SYMBOLS)
        else:
            closes = []

        ret = self._returns(closes)
        self._index_cache[index_name] = ret
        return ret

    # ── 收益率计算 ──────────────────────────────────

    @staticmethod
    def _returns(closes: list[float]) -> np.ndarray:
        """收盘价列表 → 日收益率 numpy 数组"""
        arr = np.array(closes, dtype=float)
        if len(arr) < 2:
            return np.array([])
        return (arr[1:] - arr[:-1]) / arr[:-1]

    # ── Beta 估算 ───────────────────────────────────

    def _estimate_beta(
        self,
        stock_returns: np.ndarray,
        index_returns: np.ndarray,
        sector_broad: str,
    ) -> float:
        """计算个股对指数的历史 Beta

        优先使用日线回归 β = cov / var。
        数据不足或计算失败时回退到板块经验 β。
        """
        if len(index_returns) < 10 or len(stock_returns) < 10:
            return BETA_EMPIRICAL.get(sector_broad, 1.0)

        # 对齐长度
        min_len = min(len(index_returns), len(stock_returns))
        idx_r = index_returns[-min_len:]
        stk_r = stock_returns[-min_len:]

        var_idx = np.var(idx_r, ddof=1)
        if var_idx <= 0:
            return BETA_EMPIRICAL.get(sector_broad, 1.0)

        cov = np.cov(stk_r, idx_r, ddof=1)[0, 1]
        beta = cov / var_idx

        if math.isnan(beta) or math.isinf(beta):
            return BETA_EMPIRICAL.get(sector_broad, 1.0)

        return float(beta)

    # ── 恢复时间估计 ────────────────────────────────

    @staticmethod
    def _recovery_time(loss_pct: float) -> str:
        """根据损失程度估计恢复时间"""
        if loss_pct < 0.10:
            return "1-2周"
        elif loss_pct < 0.20:
            return "1-2月"
        elif loss_pct < 0.30:
            return "3-6月"
        elif loss_pct < 0.50:
            return "6-12月"
        else:
            return "1年以上"

    # ── 主方法 ──────────────────────────────────────

    def run(
        self,
        positions: list[dict],
        total_asset: float,
    ) -> StressTestReport:
        """执行全情景压力测试

        Args:
            positions: [{'symbol', 'weight', 'market', 'sector'}, ...]
            total_asset: 总资产

        Returns:
            StressTestReport 对象
        """
        if not positions:
            return StressTestReport(
                scenarios=[],
                total_exposure=0.0,
                max_stress_loss=0.0,
                worst_scenario="",
            )

        # ── 1. 整理持仓信息 ─────────────────────────

        symbols: list[str] = []
        weights: dict[str, float] = {}
        sectors: dict[str, str] = {}
        broad_sectors: dict[str, str] = {}
        markets: dict[str, str] = {}

        total_weight = sum(p.get("weight", 0.0) for p in positions)
        if total_weight <= 0:
            total_weight = 1.0

        for p in positions:
            sym = p.get("symbol", "")
            if not sym:
                continue
            symbols.append(sym)
            w = p.get("weight", 0.0) / total_weight
            weights[sym] = w
            sec = p.get("sector", "")
            sectors[sym] = sec
            broad = SECTOR_MAP.get(sec, sec)
            broad_sectors[sym] = broad
            markets[sym] = p.get("market", "a").lower()

        total_exposure = total_weight  # 归一化后 = 1.0 或更小

        # ── 2. 拉取个股日线 & 计算收益率 ────────────

        returns_map: dict[str, np.ndarray] = {}

        for sym in symbols:
            try:
                closes = self._fetch_daily(sym)
                returns_map[sym] = self._returns(closes)
            except Exception:
                returns_map[sym] = np.array([])

        # ── 3. 估算每只个股对各指数的 Beta ──────────

        # 为每个指数估算个股 Beta
        stock_betas: dict[str, dict[str, float]] = {}
        # stock_betas[symbol][index_name] = beta

        for sym in symbols:
            stock_ret = returns_map.get(sym, np.array([]))
            br = broad_sectors.get(sym, "")
            stock_betas[sym] = {}

            # 尝试计算相对沪深300 的β
            csi_ret = self._get_index_returns("csi300")
            stock_betas[sym]["csi300"] = self._estimate_beta(
                stock_ret, csi_ret, br,
            )

            # 尝试计算相对恒指的β
            hsi_ret = self._get_index_returns("hsi")
            stock_betas[sym]["hsi"] = self._estimate_beta(
                stock_ret, hsi_ret, br,
            )

        # ── 4. 运行每个情景 ─────────────────────────

        results: list[StressTestResult] = []

        for scenario_key, scenario in SCENARIOS.items():
            try:
                result = self._run_scenario(
                    scenario_key=scenario_key,
                    scenario=scenario,
                    positions=positions,
                    symbols=symbols,
                    weights=weights,
                    broad_sectors=broad_sectors,
                    markets=markets,
                    stock_betas=stock_betas,
                    total_asset=total_asset,
                )
                results.append(result)
            except Exception as e:
                # 独立崩溃保护：单个情景失败不影响整体
                results.append(StressTestResult(
                    scenario_name=scenario.get("name", scenario_key),
                    description=scenario.get("description", ""),
                    portfolio_loss_pct=0.0,
                    portfolio_loss_amount=0.0,
                    worst_position="",
                    worst_position_loss=0.0,
                    margin_call_risk=False,
                    recovery_time_estimate="N/A（计算失败）",
                ))

        # ── 5. 汇总结果 ─────────────────────────────

        if not results:
            return StressTestReport(
                scenarios=[],
                total_exposure=total_exposure,
                max_stress_loss=0.0,
                worst_scenario="",
            )

        max_loss = max(r.portfolio_loss_pct for r in results)
        worst_idx = max(
            range(len(results)),
            key=lambda i: results[i].portfolio_loss_pct,
        )
        worst_name = results[worst_idx].scenario_name

        return StressTestReport(
            scenarios=results,
            total_exposure=total_exposure,
            max_stress_loss=max_loss,
            worst_scenario=worst_name,
        )

    # ── 单情景执行 ────────────────────────────────

    def _run_scenario(
        self,
        scenario_key: str,
        scenario: dict,
        positions: list[dict],
        symbols: list[str],
        weights: dict[str, float],
        broad_sectors: dict[str, str],
        markets: dict[str, str],
        stock_betas: dict[str, dict[str, float]],
        total_asset: float,
    ) -> StressTestResult:
        """执行单个情景模拟"""
        index_shocks: dict[str, float] = scenario.get("index_shocks", {})
        correlation_boost: float = scenario.get("correlation_boost", 1.0)

        # 计算每只标的的估计跌幅
        # 跌幅 = 历史β × 对应指数冲击 × correlation_boost
        per_stock_loss: dict[str, float] = {}

        for sym in symbols:
            market = markets.get(sym, "a")
            index_name = MARKET_INDEX_MAP.get(market, "csi300")

            # 选择适用的指数冲击
            shock = 0.0
            if index_name in index_shocks:
                shock = index_shocks[index_name]
            elif "csi300" in index_shocks:
                # 兜底：A股用沪深300冲击
                shock = index_shocks["csi300"]
            elif index_shocks:
                # 取第一个可用的冲击
                shock = next(iter(index_shocks.values()))

            # 获取该股票的 beta 对应该指数
            beta = stock_betas.get(sym, {}).get(index_name, 1.0)

            # 估算跌幅 = beta × 指数冲击 × 相关性增强
            estimated_loss = beta * shock * correlation_boost
            per_stock_loss[sym] = estimated_loss

        # 组合损失 = Σ(weight × 个股跌幅)
        total_w = sum(weights.values())
        portfolio_loss_pct = 0.0
        if total_w > 0:
            portfolio_loss_pct = sum(
                weights.get(sym, 0.0) * per_stock_loss.get(sym, 0.0)
                for sym in symbols
            ) / total_w

        portfolio_loss_amount = portfolio_loss_pct * total_asset

        # 最大受损标的
        worst_sym = max(per_stock_loss, key=per_stock_loss.get)  # type: ignore[arg-type]
        worst_loss = per_stock_loss[worst_sym]

        # 爆仓风险：模拟损失 > 50% 总资产
        margin_call_risk = portfolio_loss_amount > total_asset * 0.50

        # 恢复时间
        recovery = self._recovery_time(abs(portfolio_loss_pct))

        return StressTestResult(
            scenario_name=scenario.get("name", scenario_key),
            description=scenario.get("description", ""),
            portfolio_loss_pct=portfolio_loss_pct,
            portfolio_loss_amount=portfolio_loss_amount,
            worst_position=worst_sym,
            worst_position_loss=worst_loss,
            margin_call_risk=margin_call_risk,
            recovery_time_estimate=recovery,
        )


# ============================================================
# 便捷函数接口
# ============================================================

def run_stress_test(
    positions: list[dict],
    total_asset: float,
    lookback_days: int = 60,
) -> StressTestReport:
    """运行压力测试

    Args:
        positions: 持仓列表 [{'symbol', 'weight', 'market', 'sector'}, ...]
        total_asset: 总资产
        lookback_days: Beta 估算所用日线回看天数

    Returns:
        StressTestReport 对象
    """
    tester = StressTester(lookback_days=lookback_days)
    return tester.run(positions, total_asset)


def get_stress_report(
    positions: list[dict],
    total_asset: float,
    lookback_days: int = 60,
) -> str:
    """便捷：直接获取报告文本"""
    report = run_stress_test(positions, total_asset, lookback_days)
    return report.report()


# ============================================================
# 独立运行演示
# ============================================================

def _demo():
    """演示压力测试"""
    print("压力测试引擎 — 演示")
    print("=" * 50)
    print()

    # 尝试从数据库获取一些真实标的
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT symbol, name FROM stock_basic
               WHERE market='a' AND symbol IN (
                   SELECT symbol FROM stock_daily GROUP BY symbol
                   HAVING COUNT(*) >= 100
               ) LIMIT 6"""
        ).fetchall()
        conn.close()
        demo_symbols = [dict(r) for r in rows]
    except Exception:
        demo_symbols = [
            {"symbol": "600519", "name": "贵州茅台"},
            {"symbol": "000858", "name": "五粮液"},
            {"symbol": "600036", "name": "招商银行"},
            {"symbol": "300750", "name": "宁德时代"},
        ]

    if len(demo_symbols) < 2:
        print("⚠ 数据库无足够数据，使用模拟示例")
        demo_positions = [
            {"symbol": "600519", "name": "贵州茅台", "market": "a",
             "weight": 0.30, "sector": "白酒"},
            {"symbol": "000858", "name": "五粮液", "market": "a",
             "weight": 0.20, "sector": "白酒"},
            {"symbol": "600036", "name": "招商银行", "market": "a",
             "weight": 0.15, "sector": "银行"},
            {"symbol": "300750", "name": "宁德时代", "market": "a",
             "weight": 0.15, "sector": "锂电池"},
            {"symbol": "00700", "name": "腾讯控股", "market": "hk",
             "weight": 0.10, "sector": "软件"},
            {"symbol": "600030", "name": "中信证券", "market": "a",
             "weight": 0.10, "sector": "券商"},
        ]
    else:
        n = len(demo_symbols)
        print(f"从数据库加载 {n} 个标的:")
        demo_positions = []
        sectors_pool = ["白酒", "bank", "锂电池", "科技", "医药", "消费"]
        for i, s in enumerate(demo_symbols):
            sec = sectors_pool[i % len(sectors_pool)]
            demo_positions.append({
                "symbol": s["symbol"],
                "name": s["name"],
                "market": "a",
                "weight": round(1.0 / n, 4),
                "sector": sec,
            })
            print(f"  {s['symbol']} {s['name']} ({sec})")

    total_asset = 1_000_000.0  # 演示：100万总资产
    print()
    print(f"演示组合: {len(demo_positions)} 只标的, 总资产 ¥{total_asset:,.0f}")
    print()

    try:
        report = run_stress_test(demo_positions, total_asset)
        print(report.report())
    except Exception as e:
        print(f"压力测试失败: {e}")
        import traceback
        traceback.print_exc()

    # 打印各情景的明细
    print()
    print("── 各情景 Beta 及 个股跌幅明细 ──")
    print()
    tester = StressTester(lookback_days=60)
    try:
        # 准备数据
        symbols = [p["symbol"] for p in demo_positions]
        weights = {}
        broad_sectors = {}
        markets = {}
        total_w = sum(p.get("weight", 0.0) for p in demo_positions)
        if total_w <= 0:
            total_w = 1.0
        for p in demo_positions:
            sym = p["symbol"]
            weights[sym] = p.get("weight", 0.0) / total_w
            sec = p.get("sector", "")
            broad = SECTOR_MAP.get(sec, sec)
            broad_sectors[sym] = broad
            markets[sym] = p.get("market", "a").lower()

        returns_map = {}
        for sym in symbols:
            try:
                closes = tester._fetch_daily(sym)
                returns_map[sym] = tester._returns(closes)
            except Exception:
                returns_map[sym] = np.array([])

        # 打印 Beta
        for sym in symbols:
            br = broad_sectors.get(sym, "")
            stk_ret = returns_map.get(sym, np.array([]))
            csi_ret = tester._get_index_returns("csi300")
            hsi_ret = tester._get_index_returns("hsi")
            beta_a = tester._estimate_beta(stk_ret, csi_ret, br)
            beta_h = tester._estimate_beta(stk_ret, hsi_ret, br)
            print(f"  {sym} ({br}): β_csi300={beta_a:.3f}, β_hsi={beta_h:.3f}")

        # 打印每情景个股跌幅
        for scenario_key, scenario in SCENARIOS.items():
            print()
            print(f"  【{scenario['name']}】")
            stock_betas_demo = {}
            for sym in symbols:
                br = broad_sectors.get(sym, "")
                stk_ret = returns_map.get(sym, np.array([]))
                csi_ret = tester._get_index_returns("csi300")
                hsi_ret = tester._get_index_returns("hsi")
                stock_betas_demo[sym] = {
                    "csi300": tester._estimate_beta(stk_ret, csi_ret, br),
                    "hsi": tester._estimate_beta(stk_ret, hsi_ret, br),
                }

            shocks = scenario["index_shocks"]
            boost = scenario["correlation_boost"]

            for sym in symbols:
                mkt = markets.get(sym, "a")
                idx_name = MARKET_INDEX_MAP.get(mkt, "csi300")
                shock = shocks.get(idx_name, shocks.get("csi300", 0.0))
                beta = stock_betas_demo[sym].get(idx_name, 1.0)
                loss = beta * shock * boost
                print(f"    {sym}: beta={beta:.3f}, shock={shock:.2%}, "
                      f"loss={loss:.2%}")

    except Exception as e:
        print(f"  明细生成失败: {e}")


if __name__ == "__main__":
    _demo()
