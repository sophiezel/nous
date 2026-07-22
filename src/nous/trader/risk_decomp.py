"""持仓风险分解模块

对投资组合进行多维风险分析：
- 相关矩阵 & 组合波动率
- 市场 β 估算（优先从日线计算，回退为板块经验值）
- 风格因子暴露（市值/动量/波动率/价值）
- 集中度分析（单票/板块/市场）
- 分散度评分

核心类：
- RiskDecomposition: 风险分解结果 dataclass
- RiskAnalyzer: 风险分析器，从 screener.db 拉取日线

用法:
    analyzer = RiskAnalyzer(lookback_days=60)
    result = analyzer.analyze(positions)
    print(result.report())
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
# 板块映射（子板块 → 大行业）
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

# 经验 β（回退用，按大行业）
BETA_EMPIRICAL: dict[str, float] = {
    "消费": 0.9,
    "金融": 1.0,
    "新能源": 1.3,
    "科技": 1.2,
    "医药": 0.8,
    "地产": 1.1,
    "汽车": 1.1,
}

# 沪深300 可能存在的代码（优先查找）
CSI300_SYMBOLS = ["000300", "399300", "CSI300", "sh000300", "sz399300"]


# ============================================================
# 结果结构
# ============================================================
@dataclass
class RiskDecomposition:
    """持仓风险分解结果"""

    portfolio_vol: float = 0.0  # 年化组合波动率
    market_beta: float = 1.0  # 相对沪深300 的 β
    max_single_position: float = 0.0  # 最大单票权重
    max_sector_concentration: float = 0.0  # 最大板块集中度
    hk_exposure: float = 0.0  # 港股敞口
    correlation_matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    factor_exposures: dict[str, float] = field(default_factory=dict)
    diversification_score: float = 0.0  # 分散度评分 0-100
    warnings: list[str] = field(default_factory=list)

    def report(self) -> str:
        """生成风险报告文本"""
        lines = []
        lines.append("═══ 持仓风险分解报告 ═══")
        lines.append("")
        lines.append(f"  组合年化波动率:  {self.portfolio_vol:.2%}")
        lines.append(f"  市场 Beta:       {self.market_beta:.2f}")
        lines.append(f"  分散度评分:      {self.diversification_score:.1f}/100")
        lines.append("")
        lines.append("── 集中度 ──")
        lines.append(f"  最大单票权重:    {self.max_single_position:.1%}")
        lines.append(f"  最大板块集中度:  {self.max_sector_concentration:.1%}")
        lines.append(f"  港股敞口:        {self.hk_exposure:.1%}")
        lines.append("")

        if self.factor_exposures:
            lines.append("── 风格因子暴露 ──")
            for factor, value in sorted(self.factor_exposures.items()):
                lines.append(f"  {factor}: {value:.4f}")
            lines.append("")

        if self.warnings:
            lines.append("── ⚠️ 风险告警 ──")
            for w in self.warnings:
                lines.append(f"  • {w}")
            lines.append("")

        return "\n".join(lines)


# ============================================================
# 风险分析器
# ============================================================
class RiskAnalyzer:
    """持仓风险分析器

    从 screener.db 拉取日线数据，计算各项风险指标。
    """

    def __init__(self, lookback_days: int = 60):
        self.lookback_days = lookback_days

    # ── 数据获取 ────────────────────────────────────

    def _fetch_daily(self, symbol: str) -> list[float]:
        """获取标的日线收盘价（升序，最多 lookback_days + 1 条以保证有收益率）"""
        end = date.today()
        start = end - timedelta(days=self.lookback_days * 2)  # 留余量
        rows = get_daily_range(symbol, start.isoformat(), end.isoformat())
        # 只取最近需要的数量
        closes = [r["close"] for r in rows if r["close"] is not None]
        if len(closes) > self.lookback_days + 1:
            closes = closes[-(self.lookback_days + 1):]
        return closes

    def _fetch_csi300(self) -> list[float]:
        """尝试从数据库查找沪深300日线"""
        # 先检查 stock_basic / stock_daily 中是否有指数代码
        conn = get_db()
        try:
            for sym in CSI300_SYMBOLS:
                row = conn.execute(
                    "SELECT 1 FROM stock_daily WHERE symbol=? LIMIT 1", (sym,)
                ).fetchone()
                if row:
                    conn.close()
                    return self._fetch_daily(sym)
        finally:
            conn.close()
        return []

    # ── 收益率计算 ──────────────────────────────────

    @staticmethod
    def _returns(closes: list[float]) -> np.ndarray:
        """收盘价列表 → 日收益率 numpy 数组"""
        arr = np.array(closes, dtype=float)
        if len(arr) < 2:
            return np.array([])
        return (arr[1:] - arr[:-1]) / arr[:-1]

    # ── 相关矩阵 ────────────────────────────────────

    @staticmethod
    def _build_correlation_matrix(
        symbol_returns: dict[str, np.ndarray],
        symbols: list[str],
    ) -> dict[str, dict[str, float]]:
        """构建 symbol × symbol 相关矩阵"""
        n = len(symbols)
        if n == 0:
            return {}

        # 对齐长度：截取最短的
        min_len = min(len(r) for r in symbol_returns.values() if len(r) > 0)
        if min_len < 2:
            return {s: {t: 0.0 for t in symbols} for s in symbols}

        matrix = []
        valid_symbols = []
        for s in symbols:
            r = symbol_returns[s]
            if len(r) >= min_len:
                matrix.append(r[-min_len:])
                valid_symbols.append(s)

        if len(valid_symbols) < 2:
            return {s: {t: 1.0 if s == t else 0.0 for t in symbols} for s in symbols}

        arr = np.array(matrix)  # (n, min_len)
        corr = np.corrcoef(arr)

        result: dict[str, dict[str, float]] = {}
        for i, s in enumerate(valid_symbols):
            inner: dict[str, float] = {}
            for j, t in enumerate(valid_symbols):
                v = corr[i, j]
                inner[t] = 0.0 if (math.isnan(v) or math.isinf(v)) else float(v)
            result[s] = inner
        return result

    # ── 组合波动率 ──────────────────────────────────

    @staticmethod
    def _portfolio_vol(
        weights: np.ndarray,
        cov_matrix: np.ndarray,
    ) -> float:
        """年化组合波动率 = sqrt(w^T · Σ · w) × sqrt(252)"""
        if cov_matrix.ndim < 2 or cov_matrix.shape[0] < 1 or cov_matrix.shape[1] < 1:
            return 0.0
        if len(weights) == 0:
            return 0.0
        try:
            vol = math.sqrt(max(0.0, float(weights @ cov_matrix @ weights)))
        except (ValueError, np.linalg.LinAlgError):
            return 0.0
        return vol * math.sqrt(252.0)

    # ── 贝塔估算 ────────────────────────────────────

    def _estimate_beta(
        self,
        portfolio_returns: np.ndarray,
        index_returns: np.ndarray,
        sector_broad: str,
    ) -> float:
        """估算 β

        优先从日线计算 cov(p, idx) / var(idx)。
        如果指数数据不可用或不足，回退为板块经验值。
        """
        if len(index_returns) < 10 or len(portfolio_returns) < 10:
            return BETA_EMPIRICAL.get(sector_broad, 1.0)

        # 对齐长度
        min_len = min(len(index_returns), len(portfolio_returns))
        idx_r = index_returns[-min_len:]
        pf_r = portfolio_returns[-min_len:]

        var_idx = np.var(idx_r, ddof=1)
        if var_idx <= 0:
            return BETA_EMPIRICAL.get(sector_broad, 1.0)

        cov = np.cov(pf_r, idx_r, ddof=1)[0, 1]
        beta = cov / var_idx

        if math.isnan(beta) or math.isinf(beta):
            return BETA_EMPIRICAL.get(sector_broad, 1.0)

        return float(beta)

    # ── 风格因子暴露（简化实现）─────────────────────

    @staticmethod
    def _factor_exposures(
        symbol_returns: dict[str, np.ndarray],
        betas: dict[str, float],
        weights: dict[str, float],
    ) -> dict[str, float]:
        """估算组合层面的风格因子暴露

        - 动量: 最近20日收益率
        - 波动率: 日收益率的年化波动率
        - β: 持仓加权 β
        - 价值: 固定为 0（需基本面数据）
        - 市值暴露: 固定为 0（需市值数据）
        """
        exp: dict[str, float] = {}
        total_w = sum(weights.values())
        if total_w <= 0:
            return {"动量": 0.0, "波动率": 0.0, "Beta": 0.0, "价值": 0.0, "市值": 0.0}

        # 加权动量
        momentum = 0.0
        vol_sum = 0.0
        for sym, w in weights.items():
            r = symbol_returns.get(sym)
            if r is not None and len(r) >= 20:
                mom = float(np.mean(r[-20:])) * 252  # 年化
                momentum += mom * (w / total_w)
                v = float(np.std(r[-20:], ddof=1)) * math.sqrt(252)
                vol_sum += v * (w / total_w)

        exp["动量"] = momentum
        exp["波动率"] = vol_sum

        # Beta
        beta_w = 0.0
        for sym, w in weights.items():
            beta_w += betas.get(sym, 1.0) * (w / total_w)
        exp["Beta"] = beta_w

        # 价值 / 市值 — 无数据源时给默认值
        exp["价值"] = 0.0
        exp["市值"] = 0.0

        return exp

    # ── 主入口 ──────────────────────────────────────

    def analyze(self, positions: list[dict]) -> RiskDecomposition:
        """执行持仓风险分解

        positions: [{'symbol': '600519', 'name': '贵州茅台', 'market': 'a',
                     'weight': 0.15, 'sector': '白酒'}, ...]

        返回 RiskDecomposition 对象。
        """
        if not positions:
            return RiskDecomposition(
                warnings=["无持仓数据"],
            )

        # 提取信息
        symbols: list[str] = []
        weights: dict[str, float] = {}
        sectors: dict[str, str] = {}       # symbol → 子板块
        broad_sectors: dict[str, str] = {}  # symbol → 大行业
        markets: dict[str, str] = {}
        names: dict[str, str] = {}

        total_weight = sum(p.get("weight", 0.0) for p in positions)
        if total_weight <= 0:
            total_weight = 1.0  # 等权回退

        for p in positions:
            sym = p.get("symbol", "")
            if not sym:
                continue
            symbols.append(sym)
            w = p.get("weight", 0.0) / total_weight  # 归一化
            weights[sym] = w
            sec = p.get("sector", "")
            sectors[sym] = sec
            broad = SECTOR_MAP.get(sec, sec)
            broad_sectors[sym] = broad
            markets[sym] = p.get("market", "a").lower()
            names[sym] = p.get("name", sym)

        # 1. 拉取日线
        closes_map: dict[str, list[float]] = {}
        returns_map: dict[str, np.ndarray] = {}
        for sym in symbols:
            try:
                cls = self._fetch_daily(sym)
                closes_map[sym] = cls
                returns_map[sym] = self._returns(cls)
            except Exception:
                closes_map[sym] = []
                returns_map[sym] = np.array([])

        # 2. 相关矩阵
        corr = self._build_correlation_matrix(returns_map, symbols)

        # 3. 协方差矩阵 & 组合波动率
        n = len(symbols)
        portfolio_vol = 0.0
        if n > 0:
            # 构建收益对齐矩阵
            min_len = min(
                (len(returns_map[s]) for s in symbols if len(returns_map[s]) > 0),
                default=0,
            )
            if min_len >= 2:
                valid_syms = [
                    s for s in symbols if len(returns_map[s]) >= min_len
                ]
                if len(valid_syms) >= 1:
                    arr = np.array(
                        [returns_map[s][-min_len:] for s in valid_syms]
                    )  # (n_valid, min_len)
                    if len(valid_syms) == 1:
                        # 单标的：np.cov 返回标量，直接取 std × sqrt(252)
                        std = float(np.std(arr[0], ddof=1))
                        portfolio_vol = std * math.sqrt(252.0)
                    else:
                        cov = np.cov(arr)  # (n_valid, n_valid)
                        w_arr = np.array(
                            [weights[s] for s in valid_syms], dtype=float
                        )
                        w_sum = w_arr.sum()
                        if w_sum > 0:
                            w_arr = w_arr / w_sum
                        portfolio_vol = self._portfolio_vol(w_arr, cov)

        # 4. Beta 估算
        # 先尝试获取沪深300日线
        csi300_closes = self._fetch_csi300()
        csi300_returns = self._returns(csi300_closes)

        per_stock_beta: dict[str, float] = {}
        for sym in symbols:
            br = broad_sectors.get(sym, "")
            pf_r_single = returns_map.get(sym, np.array([]))
            beta = self._estimate_beta(pf_r_single, csi300_returns, br)
            per_stock_beta[sym] = beta

        # 组合 β = 持仓加权
        total_w = sum(weights.values())
        if total_w > 0:
            market_beta = sum(
                per_stock_beta.get(s, 1.0) * weights[s] / total_w
                for s in symbols
            )
        else:
            market_beta = 1.0

        # 5. 集中度分析
        max_single = max(weights.values()) if weights else 0.0

        # 板块集中度
        sector_weights: dict[str, float] = {}
        for sym, w in weights.items():
            br = broad_sectors.get(sym, "其他")
            sector_weights[br] = sector_weights.get(br, 0.0) + w
        max_sector = max(sector_weights.values()) if sector_weights else 0.0

        # 港股敞口
        hk_w = sum(
            weights[s] for s in symbols if markets.get(s, "a") in ("hk", "h")
        )

        # 6. 分散度评分
        div_score = self._diversification_score(corr, symbols)

        # 7. 风格因子暴露
        factor_exp = self._factor_exposures(returns_map, per_stock_beta, weights)

        # 8. 告警生成
        warnings: list[str] = []
        if max_single > 0.25:
            warnings.append(f"单票集中度过高: {max_single:.1%} > 25%")
        if max_sector > 0.40:
            warnings.append(f"板块集中度过高: {max_sector:.1%} > 40%")
        if hk_w > 0.30:
            warnings.append(f"港股敞口偏大: {hk_w:.1%} > 30%")
        if portfolio_vol > 0.35:
            warnings.append(f"组合波动率偏高: {portfolio_vol:.1%} > 35%")
        if div_score < 30:
            warnings.append(f"分散度不足: {div_score:.1f}/100")
        if market_beta > 1.5:
            warnings.append(f"高Beta暴露: {market_beta:.2f} > 1.5")
        if market_beta < 0.5:
            warnings.append(f"低Beta暴露: {market_beta:.2f} < 0.5")

        return RiskDecomposition(
            portfolio_vol=portfolio_vol,
            market_beta=market_beta,
            max_single_position=max_single,
            max_sector_concentration=max_sector,
            hk_exposure=hk_w,
            correlation_matrix=corr,
            factor_exposures=factor_exp,
            diversification_score=round(div_score, 1),
            warnings=warnings,
        )

    # ── 分散度评分 ──────────────────────────────────

    @staticmethod
    def _diversification_score(
        corr: dict[str, dict[str, float]],
        symbols: list[str],
    ) -> float:
        """分散度评分 = (1 - mean_abs_corr) × 100

        值越高越分散（0-100）
        """
        if len(symbols) < 2:
            return 100.0

        values: list[float] = []
        for i, s in enumerate(symbols):
            inner = corr.get(s, {})
            for j, t in enumerate(symbols):
                if i < j:
                    v = inner.get(t, 0.0)
                    if not (math.isnan(v) or math.isinf(v)):
                        values.append(abs(v))

        if not values:
            return 100.0

        mean_abs_corr = sum(values) / len(values)
        score = (1.0 - mean_abs_corr) * 100.0
        return max(0.0, min(100.0, score))


# ============================================================
# 便捷函数接口（供 reporter.py 等调用）
# ============================================================

def analyze_positions(
    positions: list[dict],
    lookback_days: int = 60,
) -> RiskDecomposition:
    """便捷函数：分析持仓风险

    Args:
        positions: 持仓列表，每项含 symbol/weight/sector/market/name
        lookback_days: 回看天数

    Returns:
        RiskDecomposition 对象
    """
    analyzer = RiskAnalyzer(lookback_days=lookback_days)
    return analyzer.analyze(positions)


def get_risk_report(
    positions: list[dict],
    lookback_days: int = 60,
) -> str:
    """便捷函数：直接获取风险报告文本"""
    result = analyze_positions(positions, lookback_days)
    return result.report()


def get_sector_concentration(
    positions: list[dict],
) -> dict[str, float]:
    """计算板块集中度

    Returns: {大行业: 权重占比, ...}
    """
    sector_w: dict[str, float] = {}
    total_w = sum(p.get("weight", 0.0) for p in positions)
    if total_w <= 0:
        return {}
    for p in positions:
        sec = p.get("sector", "")
        br = SECTOR_MAP.get(sec, sec)
        w = p.get("weight", 0.0) / total_w
        sector_w[br] = sector_w.get(br, 0.0) + w
    return sector_w


# ============================================================
# 独立运行演示
# ============================================================
def _demo():
    """输出演示报告"""
    import json

    print("持仓风险分解模块 — 演示")
    print("=" * 50)
    print()

    # 尝试从数据库获取一些真实标的作为演示
    try:
        conn = get_db()
        # 取5个有足够日线数据的A股标的
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
        # 等权分配
        n = len(demo_symbols)
        print(f"从数据库加载 {n} 个标的:")
        demo_positions = []
        sectors_pool = ["白酒", "银行", "锂电池", "科技", "医药", "消费"]
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

    print()
    print(f"演示组合: {len(demo_positions)} 只标的")
    print()

    analyzer = RiskAnalyzer(lookback_days=60)
    try:
        result = analyzer.analyze(demo_positions)
        print(result.report())
    except Exception as e:
        print(f"分析失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    _demo()
