"""Week 4: 因子健康监控

每个因子计算最近 N 周的 Rank IC, 检测衰减/改善趋势,
输出监控报告并保存为 JSON.

Usage:
    PYTHONPATH=. python -c "
    from nous.engine.ml.factor_monitor import monitor_factor_health
    monitor_factor_health(n_weeks=12)
    "
"""

from __future__ import annotations

import sys
import json
import logging
import warnings
from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, linregress

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 路径
# ──────────────────────────────────────────────

from nous.core.paths import factor_dir, screener_db

FACTOR_DIR = factor_dir()
IC_DIR = factor_dir().parent / "ic_analysis"
DB_PATH = screener_db()


# ──────────────────────────────────────────────
# 核心: 逐周计算每个因子的 Rank IC
# ──────────────────────────────────────────────


def compute_weekly_ic_series(
    factor_names: list[str] | None = None,
    forward_period: int = 5,
    n_weeks: int = 12,
    min_samples: int = 50,
) -> pd.DataFrame | None:
    """计算每个因子过去 N 周的逐周 Rank IC.

    流程:
        1. 从 DB 加载因子数据 + close 价格
        2. 按周分组 (ISO 周)
        3. 每周内: 对每个因子计算 cross-sectional Rank IC
        4. 返回 df[week_start, factor_name] = Rank IC

    Args:
        factor_names: 限制到部分因子 (None=全部32因子)
        forward_period: 未来 N 日收益率
        n_weeks: 回看多少周
        min_samples: 每周最少样本数, 不足则填充 NaN

    Returns:
        pd.DataFrame: 行=周起始日期, 列=因子名, 值=Rank IC
    """
    # 1. 加载因子快照 (从 latest 或历史 snapshots)
    snapshots = sorted(FACTOR_DIR.glob("snapshots/factors_*.parquet"))
    if not snapshots:
        logger.error("没有历史因子快照 data/factors/snapshots/")
        return None

    # 取最近 n_weeks 个快照
    if len(snapshots) > n_weeks:
        snapshots = snapshots[-n_weeks:]
    logger.info(f"加载 {len(snapshots)} 个历史因子快照 ({snapshots[0].stem} ~ {snapshots[-1].stem})")

    # 2. 加载全部快照, 合并
    dfs = []
    for sp in snapshots:
        df = pd.read_parquet(sp)
        dfs.append(df)
    df = pd.concat(dfs, ignore_index=True)
    logger.info(f"合并后: {len(df)} 行")

    if len(df) == 0:
        logger.warning("因子快照为空")
        return None

    # 3. 确定因子列
    all_factor_names = [c for c in df.columns if c.startswith("K")]
    if factor_names is not None:
        all_factor_names = [f for f in all_factor_names if f in factor_names]
    if not all_factor_names:
        logger.error("未找到因子列 (列名需以 K 开头)")
        return None

    # 4. 合并 close (计算 forward returns)
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        close_df = pd.read_sql_query("""
            SELECT symbol, trade_date, close
            FROM stock_daily
            WHERE trade_date >= '2020-01-01'
        """, conn)
        conn.close()
    except Exception as e:
        logger.error(f"无法加载 close 数据: {e}")
        return None

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    close_df["trade_date"] = pd.to_datetime(close_df["trade_date"])
    df = df.merge(close_df, on=["symbol", "trade_date"], how="inner")
    logger.info(f"合并 close 后: {len(df)} 行")

    if len(df) < min_samples:
        logger.warning(f"数据不足: {len(df)} 行 < {min_samples}")
        return None

    # 5. 计算 forward returns
    df = df.sort_values(["symbol", "trade_date"])
    df["forward_ret"] = df.groupby("symbol")["close"].shift(-forward_period) / df["close"] - 1

    # 6. 按周分组并计算 IC
    df["week_start"] = df["trade_date"].dt.to_period("W").dt.start_time

    weekly_ic_records = []
    weeks = sorted(df["week_start"].unique())

    for week in weeks:
        week_df = df[df["week_start"] == week]
        if len(week_df) < min_samples:
            continue

        y = week_df["forward_ret"].values
        if np.isnan(y).all():
            continue

        row = {"week_start": week, "n_samples": len(week_df)}
        for factor in all_factor_names:
            x = week_df[factor].values
            valid = ~np.isnan(x) & ~np.isnan(y) & ~np.isinf(x)
            if valid.sum() < min_samples:
                row[factor] = np.nan
                continue
            ic_val, _ = spearmanr(x[valid], y[valid])
            row[factor] = float(ic_val) if not np.isnan(ic_val) else np.nan

        weekly_ic_records.append(row)

    if not weekly_ic_records:
        logger.warning("未能计算出任何有效的周 IC")
        return None

    result_df = pd.DataFrame(weekly_ic_records)
    result_df["week_start"] = pd.to_datetime(result_df["week_start"])
    result_df = result_df.sort_values("week_start").reset_index(drop=True)

    logger.info(f"逐周 IC 表: {len(result_df)} 周 × {len(all_factor_names)} 因子")
    return result_df


# ──────────────────────────────────────────────
# 健康状态分析
# ──────────────────────────────────────────────


def analyze_factor_health(
    weekly_ic_df: pd.DataFrame,
    decay_slope_threshold: float = -0.003,
    improve_slope_threshold: float = 0.003,
    dead_weeks: int = 4,
) -> dict:
    """分析每个因子的健康状态.

    对每个因子:
        1. 线性回归 IC ~ week_index → 斜率
        2. 斜率 < decay_slope_threshold → "decaying" (衰减)
        3. 斜率 > improve_slope_threshold → "improving" (改善)
        4. 否则 → "stable" (稳定)
        5. 连续 dead_weeks 周 IC < 0 → "dead" (死亡)

    Args:
        weekly_ic_df: 来自 compute_weekly_ic_series() 的输出
        decay_slope_threshold: 衰减阈值 (默认 -0.003/周)
        improve_slope_threshold: 改善阈值 (默认 0.003/周)
        dead_weeks: 连续多少周 IC < 0 视为死亡 (默认 4)

    Returns:
        {factor_name: {status, slope, mean_ic, last_ic, recent_negative_weeks, ...}}
    """
    factor_cols = [c for c in weekly_ic_df.columns if c.startswith("K")]
    n_weeks = len(weekly_ic_df)
    week_indices = np.arange(n_weeks)

    results = {}
    for factor in factor_cols:
        ic_series = weekly_ic_df[factor].values
        valid = ~np.isnan(ic_series)

        if valid.sum() < 3:
            # 数据太少, 无法判断趋势
            results[factor] = {
                "status": "insufficient_data",
                "slope": None,
                "mean_ic": None,
                "last_ic": None,
                "n_valid_weeks": int(valid.sum()),
                "recent_negative_weeks": 0,
            }
            continue

        # 线性回归
        valid_indices = week_indices[valid]
        valid_ic = ic_series[valid]
        slope, intercept, r_value, p_value, std_err = linregress(valid_indices, valid_ic)

        # 最近 N 周 (最多取 dead_weeks 周)
        recent_ic = ic_series[-dead_weeks:] if n_weeks >= dead_weeks else ic_series
        recent_negative = int(np.sum(
            [1 for v in recent_ic if not np.isnan(v) and v < 0]
        ))
        # 连续 negative 计数
        consecutive_negative = 0
        for v in reversed(recent_ic):
            if not np.isnan(v) and v < 0:
                consecutive_negative += 1
            else:
                break

        # 判断状态
        if consecutive_negative >= dead_weeks:
            status = "dead"
        elif slope < decay_slope_threshold:
            status = "decaying"
        elif slope > improve_slope_threshold:
            status = "improving"
        else:
            status = "stable"

        last_valid = float(valid_ic[-1]) if len(valid_ic) > 0 else None

        results[factor] = {
            "status": status,
            "slope": round(float(slope), 6),
            "r_value": round(float(r_value), 4),
            "p_value": round(float(p_value), 6),
            "mean_ic": round(float(np.nanmean(ic_series)), 4),
            "last_ic": round(last_valid, 4) if last_valid is not None else None,
            "n_valid_weeks": int(valid.sum()),
            "consecutive_negative_weeks": consecutive_negative,
            "recent_negative_weeks": recent_negative,
        }

    return results


# ──────────────────────────────────────────────
# 报告输出
# ──────────────────────────────────────────────


def print_health_report(health: dict, title: str = "因子健康监控报告"):
    """打印因子健康状态报告到控制台."""
    if not health:
        print("  (无数据)")
        return

    # 分类
    statuses = {"dead": [], "decaying": [], "improving": [], "stable": [], "insufficient_data": []}
    for factor, info in health.items():
        s = info.get("status", "unknown")
        statuses.setdefault(s, []).append((factor, info))

    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

    for status, label, color in [
        ("dead", "💀 DEAD (连续4+周负IC)", "red"),
        ("decaying", "📉 DECAYING (斜率<阈值)", "yellow"),
        ("improving", "📈 IMPROVING (斜率>阈值)", "green"),
        ("stable", "✓ STABLE", ""),
        ("insufficient_data", "? 数据不足", ""),
    ]:
        items = statuses.get(status, [])
        if not items:
            continue
        print(f"\n  {label} ({len(items)} 因子):")
        for factor, info in sorted(items, key=lambda x: x[1].get("slope", 0) or 0):
            slope = info.get("slope")
            mean_ic = info.get("mean_ic")
            last_ic = info.get("last_ic")
            slope_str = f"slope={slope:+.6f}" if slope is not None else ""
            mean_str = f"mean_ic={mean_ic:+.4f}" if mean_ic is not None else ""
            last_str = f"last={last_ic:+.4f}" if last_ic is not None else ""
            details = " | ".join(filter(None, [slope_str, mean_str, last_str]))
            print(f"    {factor:<25s} {details}")

    print(f"\n{'='*60}")

    # 汇总统计
    total = len(health)
    n_dead = len(statuses["dead"])
    n_decaying = len(statuses["decaying"])
    n_improving = len(statuses["improving"])
    n_stable = len(statuses["stable"])
    n_insufficient = len(statuses["insufficient_data"])
    print(f"  汇总: {total} 因子 | "
          f"💀 {n_dead} | 📉 {n_decaying} | 📈 {n_improving} | ✓ {n_stable} | ? {n_insufficient}")
    print(f"{'='*60}")


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────


def monitor_factor_health(
    n_weeks: int = 12,
    forward_period: int = 5,
    decay_slope_threshold: float = -0.003,
    improve_slope_threshold: float = 0.003,
    dead_weeks: int = 4,
    save_report: bool = True,
) -> dict | None:
    """因子健康监控完整流程: 加载快照 → 逐周IC → 趋势分析 → 报告.

    仅使用已有历史因子快照 (data/factors/snapshots/factors_*.parquet).

    Args:
        n_weeks: 回看周数
        forward_period: 预测周期 (日)
        decay_slope_threshold: 衰减斜率阈值
        improve_slope_threshold: 改善斜率阈值
        dead_weeks: 连续负IC周数判定死亡
        save_report: 是否保存 JSON 报告

    Returns:
        {factor: info} | None (数据不足)
    """
    print(f"\n{'='*60}")
    print(f"  因子健康监控")
    print(f"  回看 {n_weeks} 周, forward={forward_period}d, dead_weeks={dead_weeks}")
    print(f"{'='*60}")

    # 检查是否有历史快照
    snapshot_dir = FACTOR_DIR / "snapshots"
    if not snapshot_dir.exists():
        print(f"  历史快照目录不存在: {snapshot_dir}")
        print("  数据不足, 等待积累")
        return None

    snaphots = sorted(snapshot_dir.glob("factors_*.parquet"))
    if len(snaphots) < 2:
        print(f"  仅有 {len(snaphots)} 个历史快照, 需要至少 2 个")
        print("  数据不足, 等待积累")
        return None

    # 1. 计算逐周 IC
    print(f"  计算逐周 Rank IC...")
    weekly_ic_df = compute_weekly_ic_series(
        n_weeks=n_weeks,
        forward_period=forward_period,
    )

    if weekly_ic_df is None or len(weekly_ic_df) < 2:
        print("  数据不足, 等待积累")
        return None

    print(f"  成功计算 {len(weekly_ic_df)} 周的逐因子 IC")

    # 2. 分析健康状态
    health = analyze_factor_health(
        weekly_ic_df,
        decay_slope_threshold=decay_slope_threshold,
        improve_slope_threshold=improve_slope_threshold,
        dead_weeks=dead_weeks,
    )

    # 3. 打印报告
    print_health_report(health)

    # 4. 保存 JSON
    if save_report:
        IC_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        report_path = IC_DIR / f"factor_health_{today}.json"

        # 统计
        status_counts = {}
        for info in health.values():
            s = info.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

        report = {
            "date": today,
            "n_weeks": n_weeks,
            "forward_period": forward_period,
            "n_factors": len(health),
            "status_counts": status_counts,
            "n_weeks_computed": len(weekly_ic_df),
            "factors": health,
        }
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n  报告已保存: {report_path}")

    return health


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    import argparse
    parser = argparse.ArgumentParser(description="因子健康监控")
    parser.add_argument("--weeks", type=int, default=12, help="回看周数")
    parser.add_argument("--forward", type=int, default=5, help="预测周期 (日)")
    parser.add_argument("--decay", type=float, default=-0.003, help="衰减斜率阈值")
    parser.add_argument("--improve", type=float, default=0.003, help="改善斜率阈值")
    parser.add_argument("--dead-weeks", type=int, default=4, help="连续负IC周数判定死亡")
    args = parser.parse_args()

    monitor_factor_health(
        n_weeks=args.weeks,
        forward_period=args.forward,
        decay_slope_threshold=args.decay,
        improve_slope_threshold=args.improve,
        dead_weeks=args.dead_weeks,
    )
