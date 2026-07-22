"""基准对标分析：组合 vs 指数（沪深300/中证500/恒生）"""
from dataclasses import dataclass, field
from typing import Optional
import math
import sqlite3
from datetime import datetime, date
from pathlib import Path
import sys

# 确保模块可独立运行
_proj_root = str(Path(__file__).resolve().parent.parent.parent)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)


# ── 指数代码映射 ──────────────────────────────

INDEX_MAP = {
    "csi300": "IDX_000300",
    "csi500": "IDX_000905",
    "hsi": "IDX_HSI",
}

INDEX_AK_MAP = {
    "IDX_000300": ("sh000300", "stock_zh_index_daily"),
    "IDX_000905": ("sh000905", "stock_zh_index_daily"),
    "IDX_HSI": ("HSI", "stock_hk_index_daily_sina"),
}

INDEX_NAMES = {
    "csi300": "沪深300",
    "csi500": "中证500",
    "hsi": "恒生指数",
}


# ── 数据类 ──────────────────────────────────────


@dataclass
class BenchmarkComparison:
    """组合 vs 基准对标结果"""

    total_return: float = 0.0  # 组合总收益
    benchmark_return: float = 0.0  # 基准总收益
    excess_return: float = 0.0  # 超额收益（累计）
    tracking_error: float = 0.0  # 跟踪误差（年化）
    information_ratio: float = 0.0  # 信息比率
    up_capture: float = 0.0  # 上行捕获率
    down_capture: float = 0.0  # 下行捕获率
    beta: float = 0.0  # Beta
    alpha: float = 0.0  # 年化 Alpha
    correlation: float = 0.0  # 相关系数
    benchmark_name: str = "csi300"
    benchmark_label: str = "沪深300"

    def report(self) -> str:
        """生成可读对标报告"""
        lines = []
        lines.append("=" * 60)
        lines.append(f"基准对标报告：组合 vs {self.benchmark_label} ({self.benchmark_name})")
        lines.append("=" * 60)
        lines.append(f"  组合总收益:     {self.total_return:>+9.2%}")
        lines.append(f"  基准总收益:     {self.benchmark_return:>+9.2%}")
        lines.append(f"  超额收益:       {self.excess_return:>+9.2%}")
        lines.append("")
        lines.append(f"  年化 Alpha:     {self.alpha:>+9.2%}")
        lines.append(f"  Beta:           {self.beta:>9.4f}")
        lines.append(f"  跟踪误差(年化): {self.tracking_error:>9.2%}")
        lines.append(f"  信息比率:       {self.information_ratio:>9.4f}")
        lines.append(f"  相关系数:       {self.correlation:>9.4f}")
        lines.append("")
        lines.append(f"  上行捕获率:     {self.up_capture:>9.2%}")
        lines.append(f"  下行捕获率:     {self.down_capture:>9.2%}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ── 指数日线存储 ──────────────────────────────


def _get_db_path() -> str:
    """获取 screener.db 路径"""
    from nous.data import storage
    return str(storage.DB_PATH)


def _ensure_index_table(conn: sqlite3.Connection):
    """确保 index_daily 表存在"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_daily (
            symbol TEXT NOT NULL,
            trade_date DATE NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    conn.commit()


def _save_index_daily(rows: list[dict]):
    """将指数日线写入 screener.db"""
    try:
        from nous.data import storage
        conn = storage.get_db()
    except Exception:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

    try:
        _ensure_index_table(conn)
        conn.executemany(
            "INSERT OR REPLACE INTO index_daily "
            "(symbol, trade_date, open, high, low, close, volume, amount) "
            "VALUES (:symbol, :trade_date, :open, :high, :low, :close, :volume, :amount)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _load_index_daily(
    symbol: str, start: str, end: str
) -> list[dict]:
    """从 screener.db 读取指数日线"""
    try:
        from nous.data import storage
        conn = storage.get_db()
    except Exception:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

    try:
        _ensure_index_table(conn)
        rows = conn.execute(
            "SELECT trade_date, close FROM index_daily "
            "WHERE symbol = ? AND trade_date >= ? AND trade_date <= ? "
            "ORDER BY trade_date ASC",
            (symbol, start, end),
        ).fetchall()
        return [{"trade_date": r[0], "close": r[1]} for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── 指数数据获取 ──────────────────────────────


def fetch_index_daily(
    index_code: str, start: str, end: str
) -> list[dict]:
    """从 screener.db 读取指数日线；若无则尝试 akshare 拉取

    Args:
        index_code: '000300', '000905', 'HSI' 或 'IDX_000300' 等
        start: 起始日期 'YYYY-MM-DD'
        end:   截止日期 'YYYY-MM-DD'

    Returns:
        [{"trade_date": str, "close": float}, ...] 升序排列
    """
    # 规范化 symbol
    sym = index_code.upper()
    if not sym.startswith("IDX_"):
        if sym in ("000300", "CSI300"):
            sym = "IDX_000300"
        elif sym in ("000905", "CSI500"):
            sym = "IDX_000905"
        elif sym in ("HSI", "HSI"):
            sym = "IDX_HSI"
        elif sym.startswith("SH") or sym.startswith("SZ"):
            sym = f"IDX_{sym}"
        else:
            sym = f"IDX_{sym}"

    # 先尝试从数据库读取
    rows = _load_index_daily(sym, start, end)
    if rows:
        return rows

    # 数据库无数据 → akshare 拉取
    try:
        return _fetch_from_akshare(sym, start, end)
    except ImportError:
        print("[benchmarks] akshare 未安装，无法拉取指数数据")
        return []
    except Exception as e:
        print(f"[benchmarks] akshare 拉取指数 {sym} 失败: {e}")
        return []


def _fetch_from_akshare(sym: str, start: str, end: str) -> list[dict]:
    """通过 akshare 拉取指数日线并保存到数据库"""
    import akshare as ak

    if sym == "IDX_HSI":
        # 恒生指数 - Sina源（东财push2被代理拦截）
        df = ak.stock_hk_index_daily_sina(symbol="HSI")
    elif sym == "IDX_000300":
        df = ak.stock_zh_index_daily(symbol="sh000300")
    elif sym == "IDX_000905":
        df = ak.stock_zh_index_daily(symbol="sh000905")
    else:
        # 尝试 sh 前缀
        code = sym.replace("IDX_", "").lower()
        df = ak.stock_zh_index_daily(symbol=f"sh{code}")

    if df is None or df.empty:
        return []

    # 列名标准化
    df = df.rename(
        columns={
            "date": "trade_date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
    )
    if "amount" not in df.columns and "turnover" in df.columns:
        df["amount"] = df["turnover"]

    # 日期过滤
    df["trade_date"] = df["trade_date"].astype(str)
    df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
    df = df.sort_values("trade_date")

    # 保存到数据库
    rows_to_save = []
    result = []
    for _, r in df.iterrows():
        row = {
            "symbol": sym,
            "trade_date": str(r["trade_date"]),
            "open": float(r.get("open", 0) or 0),
            "high": float(r.get("high", 0) or 0),
            "low": float(r.get("low", 0) or 0),
            "close": float(r.get("close", 0) or 0),
            "volume": float(r.get("volume", 0) or 0),
            "amount": float(r.get("amount", 0) or 0),
        }
        rows_to_save.append(row)
        result.append({"trade_date": row["trade_date"], "close": row["close"]})

    if rows_to_save:
        _save_index_daily(rows_to_save)

    return result


# ── 辅助函数 ──────────────────────────────────


def _daily_returns(prices: list[float]) -> list[float]:
    """从价格序列计算日收益"""
    if len(prices) < 2:
        return []
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


def _cumulative_return(returns: list[float]) -> float:
    """累计收益"""
    if not returns:
        return 0.0
    cum = 1.0
    for r in returns:
        cum *= 1.0 + r
    return cum - 1.0


def _annualized_return(cum_ret: float, days: int) -> float:
    """年化收益"""
    if days <= 0 or cum_ret <= -1:
        return -1.0
    return (1.0 + cum_ret) ** (252.0 / days) - 1.0


def _mean(xs: list[float]) -> float:
    if not xs:
        return 0.0
    return sum(xs) / len(xs)


def _std(xs: list[float], ddof: int = 1) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    variance = sum((x - m) ** 2 for x in xs) / (len(xs) - ddof)
    return math.sqrt(variance)


def _cov(xs: list[float], ys: list[float]) -> float:
    """协方差"""
    if len(xs) < 2 or len(ys) < 2:
        return 0.0
    n = min(len(xs), len(ys))
    mx = _mean(xs[:n])
    my = _mean(ys[:n])
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)


# ── 对标核心函数 ──────────────────────────────


def compare_to_benchmark(
    equity_curve: list[dict],
    benchmark: str = "csi300",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> BenchmarkComparison:
    """组合 vs 基准对标

    Args:
        equity_curve: [{date, equity}, ...] 组合每日权益曲线
        benchmark:    'csi300' | 'csi500' | 'hsi'
        start_date:   起始日期（默认取组合首日）
        end_date:     结束日期（默认取组合末日）

    Returns:
        BenchmarkComparison
    """
    result = BenchmarkComparison(benchmark_name=benchmark)
    result.benchmark_label = INDEX_NAMES.get(benchmark, benchmark.upper())

    if not equity_curve or len(equity_curve) < 2:
        print("[benchmarks] 权益曲线不足2个数据点")
        return result

    # 日期范围
    if start_date is None:
        start_date = equity_curve[0]["date"]
    if end_date is None:
        end_date = equity_curve[-1]["date"]

    # 1. 计算组合收益
    equities = [v["equity"] for v in equity_curve]
    # 按日期对齐的收益
    port_dates = [v["date"] for v in equity_curve]
    port_rets = _daily_returns(equities)

    if not port_rets:
        return result

    # 组合总收益
    result.total_return = _cumulative_return(port_rets)
    port_dates_aligned = port_dates[1:]  # 收益序列对应的日期

    # 2. 获取基准数据
    sym = INDEX_MAP.get(benchmark, "IDX_000300")
    index_data = fetch_index_daily(sym, start_date, end_date)

    if not index_data:
        print(f"[benchmarks] 警告：基准 {benchmark}({sym}) 无数据，使用简化结果")
        return result

    # 3. 对齐日期
    index_dict = {r["trade_date"]: r["close"] for r in index_data}

    aligned_port_rets = []
    aligned_bench_rets = []

    for i, dt in enumerate(port_dates_aligned):
        if dt in index_dict:
            aligned_port_rets.append(port_rets[i])
            # 基准日收益
            idx_close = index_dict[dt]
            # 找上一个有效基准收盘价
            prev_close = None
            for prev_dt in port_dates[: i + 1]:
                if prev_dt in index_dict:
                    prev_close = index_dict[prev_dt]
            if prev_close is not None and prev_close > 0:
                aligned_bench_rets.append((idx_close - prev_close) / prev_close)
            else:
                aligned_port_rets.pop()
                # 凑成对齐时间戳的第一个基准收益用第一个可用收盘价... 更精确：找前面最近的
                # 重试：找 index_data 中上一个交易日
                prev_idx = None
                for j in range(len(index_data) - 1, -1, -1):
                    if index_data[j]["trade_date"] < dt:
                        prev_idx = index_data[j]["close"]
                        break
                if prev_idx is not None and prev_idx > 0:
                    aligned_bench_rets.append((idx_close - prev_idx) / prev_idx)
                else:
                    # 回退
                    if aligned_port_rets:
                        aligned_port_rets.pop()
                    continue

    if len(aligned_port_rets) < 2:
        print("[benchmarks] 对齐后数据不足")
        return result

    # 4. 基准总收益
    result.benchmark_return = _cumulative_return(aligned_bench_rets)

    # 5. 超额收益（累计）
    # 用链接方式
    excess_cum = 1.0
    for pr, br in zip(aligned_port_rets, aligned_bench_rets):
        excess_cum *= (1.0 + pr) / (1.0 + br) if (1.0 + br) != 0 else 1.0
    result.excess_return = excess_cum - 1.0

    # 6. 超额收益序列（用于跟踪误差）
    excess_rets = [
        pr - br
        for pr, br in zip(aligned_port_rets, aligned_bench_rets)
    ]

    # 7. 跟踪误差（年化）
    te = _std(excess_rets, ddof=1) * math.sqrt(252.0)
    result.tracking_error = round(te, 6)

    # 8. 信息比率
    mean_excess = _mean(excess_rets)
    if te > 1e-10:
        result.information_ratio = round(mean_excess / te * math.sqrt(252.0), 4)

    # 9. Alpha + Beta
    if len(aligned_port_rets) >= 2:
        c = _cov(aligned_port_rets, aligned_bench_rets)
        v = _std(aligned_bench_rets, ddof=1) ** 2
        if v > 1e-10:
            result.beta = round(c / v, 4)
        # Alpha = R_p - beta * R_b（日频，年化）
        mean_p = _mean(aligned_port_rets)
        mean_b = _mean(aligned_bench_rets)
        alpha_daily = mean_p - result.beta * mean_b
        result.alpha = round(alpha_daily * 252.0, 6)

    # 10. 相关系数
    if len(aligned_port_rets) >= 2:
        sp = _std(aligned_port_rets, ddof=1)
        sb = _std(aligned_bench_rets, ddof=1)
        if sp > 0 and sb > 0:
            result.correlation = round(
                _cov(aligned_port_rets, aligned_bench_rets) / (sp * sb), 4
            )

    # 11. 上行/下行捕获率
    up_sum = 0.0
    up_b_sum = 0.0
    dn_sum = 0.0
    dn_b_sum = 0.0
    for pr, br in zip(aligned_port_rets, aligned_bench_rets):
        if br > 0:
            up_sum += pr
            up_b_sum += br
        elif br < 0:
            dn_sum += pr
            dn_b_sum += br

    if up_b_sum > 0:
        result.up_capture = round(up_sum / up_b_sum, 4)
    if dn_b_sum < 0:
        result.down_capture = round(dn_sum / dn_b_sum, 4)

    return result


# ── 独立运行验证 ───────────────────────────────


def _demo():
    """生成模拟权益曲线演示对标"""
    import random

    random.seed(42)

    print("=" * 60)
    print("基准对标演示（模拟数据）")
    print("=" * 60)

    # 模拟 60 个交易日的权益曲线
    equity = 1_000_000.0
    curve = []
    for i in range(60):
        ret = random.uniform(-0.015, 0.02)
        equity *= 1.0 + ret
        curve.append(
            {
                "date": f"2025-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}",
                "equity": round(equity, 2),
            }
        )

    # 尝试用真实基准数据
    for bm in ["csi300", "csi500"]:
        print(f"\n--- 基准: {INDEX_NAMES.get(bm, bm)} ---")
        result = compare_to_benchmark(
            curve,
            benchmark=bm,
            start_date=curve[0]["date"],
            end_date=curve[-1]["date"],
        )
        print(result.report())
        print("")

    # 如果无网络 / akshare 未安装，显示基础信息
    print("\n提示：若基准数据不可用，指数日线会自动从 screener.db 读取。")
    print("首次运行会自动通过 akshare 拉取并缓存到 index_daily 表。")
    print("可通过以下命令检查数据库中的指数数据：")
    print("  sqlite3 data/screener.db \"SELECT * FROM index_daily LIMIT 5\"")
    print("")


if __name__ == "__main__":
    _demo()
