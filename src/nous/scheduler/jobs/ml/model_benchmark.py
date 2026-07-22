#!/usr/bin/env python3
"""模型选股 vs 沪深300 基准对比

每日生成对比快照, 追踪指标:
- 累计收益 / 夏普 / 回撤 / 超额收益

数据来源:
- 模型选股: model_trade_log (JSONL)
- 沪深300: index_daily (screener.db)

用法:
    python scripts/model_benchmark.py [--days 60] [--verbose]

输出:
    - ~/wiki/finance/reports/model_trades/benchmark.json (快照)
    - 终端打印对比摘要
"""
import json
import sys
import math
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

# 项目路径
sys.path.insert(0, str(Path(__file__).resolve().parents[4]  # nous repo root))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]  # nous repo root))

LOG_DIR = Path.home() / "wiki" / "finance" / "reports" / "model_trades"
LOG_DIR.mkdir(parents=True, exist_ok=True)
BENCHMARK_PATH = LOG_DIR / "benchmark.json"


def load_trade_logs(days: int = 60) -> list[dict]:
    """加载近 N 天交易日志"""
    records = []
    today = date.today()
    for m_offset in range(3):  # 查近3个月
        m = today.month - m_offset
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        ym = f"{y}{m:02d}"
        path = LOG_DIR / f"trades_{ym}.jsonl"
        if path.exists():
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                records.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue

    # 过滤日期
    cutoff = date.today() - timedelta(days=days)
    filtered = []
    for r in records:
        d = r.get("buy_date") or r.get("sell_date") or ""
        if d:
            try:
                rd = date.fromisoformat(d)
                if rd >= cutoff:
                    filtered.append(r)
            except (ValueError, TypeError):
                filtered.append(r)
        else:
            filtered.append(r)

    return filtered


def load_csi300_data(days: int = 120) -> list[dict]:
    """从 screener.db 加载沪深300日线"""
    try:
        import sqlite3
        db_path = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"
        if not db_path.exists():
            print(f"  [benchmark] 数据库不存在: {db_path}", file=sys.stderr)
            return []

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """SELECT trade_date, close FROM index_daily
               WHERE symbol = 'IDX_000300' AND trade_date >= ?
               ORDER BY trade_date ASC""",
            (cutoff,),
        ).fetchall()
        conn.close()

        if not rows:
            print(f"  [benchmark] 沪深300数据为空 (尝试 IDX_000300)", file=sys.stderr)
            return []

        return [{"date": r["trade_date"], "close": r["close"]} for r in rows]
    except Exception as e:
        print(f"  [benchmark] 加载沪深300失败: {e}", file=sys.stderr)
        return []


def calc_model_performance(logs: list[dict]) -> dict:
    """计算模型选股绩效"""
    buys = [r for r in logs if r.get("event") == "buy"]
    sells = [r for r in logs if r.get("event") == "sell"]

    # 去重: 按 symbol 去重, 取最新
    buy_set = {}
    for b in buys:
        buy_set[b.get("symbol", "")] = b
    unique_buys = list(buy_set.values())

    # 已平仓: 有 pnl_pct 的
    closed_sells = [s for s in sells if s.get("pnl_pct") is not None]

    if not closed_sells:
        return {
            "total_buys": len(unique_buys),
            "total_closed": 0,
            "still_holding": len(unique_buys),
            "wins": 0, "losses": 0,
            "win_rate": 0,
            "avg_pnl_pct": 0,
            "avg_win_pct": 0,
            "avg_loss_pct": 0,
            "profit_factor": float("inf"),
            "total_pnl_pct": 0,
            "max_single_win": 0,
            "max_single_loss": 0,
        }

    pnls = [s.get("pnl_pct", 0) for s in closed_sells]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    n = len(pnls)
    win_rate = len(wins) / n if n > 0 else 0
    avg_pnl = sum(pnls) / n
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    total_win = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = total_win / total_loss if total_loss > 0 else float("inf")

    # 最大单笔盈利/亏损
    max_win = max(pnls) if pnls else 0
    max_loss = min(pnls) if pnls else 0

    # 累计收益率 (等权组合)
    total_returns = sum(pnl * (1 / max(n, 1)) for pnl in pnls)

    return {
        "total_buys": len(unique_buys),
        "total_closed": n,
        "still_holding": len(unique_buys) - n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate * 100, 2),
        "avg_pnl_pct": round(avg_pnl, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "total_pnl_pct": round(total_returns, 2),
        "max_single_win": round(max_win, 2),
        "max_single_loss": round(max_loss, 2),
    }


def calc_csi300_performance(csi300_data: list[dict]) -> dict:
    """计算沪深300绩效 (买入持有)"""
    if len(csi300_data) < 2:
        return {"total_return": 0, "annual_return": 0, "volatility": 0, "sharpe": 0, "max_drawdown": 0}

    closes = [r["close"] for r in csi300_data]
    n = len(closes)

    # 收益率序列
    daily_returns = []
    for i in range(1, n):
        r = (closes[i] - closes[i - 1]) / closes[i - 1]
        daily_returns.append(r)

    # 累计收益
    total_return = (closes[-1] - closes[0]) / closes[0]

    # 年化收益率
    trading_days = n - 1
    years = max(trading_days / 252, 0.01)
    annual_return = (1 + total_return) ** (1 / years) - 1

    # 年化波动率
    if len(daily_returns) > 1:
        volatility = (sum((r - sum(daily_returns) / len(daily_returns)) ** 2 for r in daily_returns)
                      / (len(daily_returns) - 1)) ** 0.5 * math.sqrt(252)
    else:
        volatility = 0

    # 夏普 (无风险利率 2%)
    risk_free = 0.02
    sharpe = (annual_return - risk_free) / volatility if volatility > 0 else 0

    # 最大回撤
    peak = closes[0]
    max_dd = 0
    for c in closes:
        if c > peak:
            peak = c
        dd = (peak - c) / peak
        if dd > max_dd:
            max_dd = dd

    return {
        "total_return": round(total_return * 100, 2),
        "annual_return": round(annual_return * 100, 2),
        "volatility": round(volatility * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(max_dd * 100, 2),
        "trading_days": trading_days,
        "start_date": csi300_data[0]["date"],
        "end_date": csi300_data[-1]["date"],
    }


def generate_benchmark(days: int = 60, verbose: bool = False) -> dict:
    """生成基准对比快照"""
    print(f"\n{'='*70}")
    print(f"  模型选股 vs 沪深300 基准对比 (近{days}天)")
    print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    # 加载数据
    logs = load_trade_logs(days=days)
    csi300 = load_csi300_data(days=days + 30)  # 多加载30天用于滚算

    if verbose:
        print(f"  交易日志: {len(logs)} 条记录")
        buy_count = len([r for r in logs if r.get("event") == "buy"])
        sell_count = len([r for r in logs if r.get("event") == "sell"])
        print(f"    买入: {buy_count}, 卖出: {sell_count}")
        print(f"  沪深300: {len(csi300)} 个交易日")

    # 计算绩效
    model_perf = calc_model_performance(logs)
    csi_perf = calc_csi300_performance(csi300)

    # 输出格式
    def fmt(v, suffix=""):
        if isinstance(v, str):
            return v
        if isinstance(v, float):
            return f"{v:.2f}{suffix}"
        return str(v)

    # 模型选股摘要
    print(f"  ┌─ 模型选股 {'─'*50}")
    print(f"  │ 总买入:     {model_perf['total_buys']:>4d}")
    print(f"  │ 已平仓:     {model_perf['total_closed']:>4d}")
    print(f"  │ 持仓中:     {model_perf['still_holding']:>4d}")
    print(f"  │ 胜率:       {fmt(model_perf['win_rate'], '%'):>8s}")
    print(f"  │ 平均盈亏:   {fmt(model_perf['avg_pnl_pct'], '%'):>8s}")
    print(f"  │ 平均盈利:   {fmt(model_perf['avg_win_pct'], '%'):>8s}")
    print(f"  │ 平均亏损:   {fmt(model_perf['avg_loss_pct'], '%'):>8s}")
    print(f"  │ 盈亏比:     {fmt(model_perf['profit_factor']):>8s}")
    print(f"  │ 最大盈利:   {fmt(model_perf['max_single_win'], '%'):>8s}")
    print(f"  │ 最大亏损:   {fmt(model_perf['max_single_loss'], '%'):>8s}")
    print(f"  └{'─'*60}")

    # 沪深300摘要
    print(f"\n  ┌─ 沪深300 {'─'*52}")
    print(f"  │ 累计收益:   {fmt(csi_perf['total_return'], '%'):>8s}")
    print(f"  │ 年化收益:   {fmt(csi_perf['annual_return'], '%'):>8s}")
    print(f"  │ 年化波动:   {fmt(csi_perf['volatility'], '%'):>8s}")
    print(f"  │ 夏普比率:   {fmt(csi_perf['sharpe']):>8s}")
    print(f"  │ 最大回撤:   {fmt(csi_perf['max_drawdown'], '%'):>8s}")
    print(f"  │ 交易日数:   {csi_perf['trading_days']:>4d}")
    print(f"  └{'─'*60}")

    # 超额收益
    if model_perf["total_closed"] > 0:
        excess = model_perf["avg_pnl_pct"] - csi_perf["total_return"]
        print(f"\n  ═══ 超额收益 (模型平均单笔 vs 沪深300累计): {excess:+.2f}% ═══\n")
    else:
        print(f"\n  ═══ 暂无已平仓交易, 无法计算超额收益 ═══\n")

    # 构建快照
    snapshot = {
        "generated_at": datetime.now().isoformat(),
        "period_days": days,
        "model": model_perf,
        "benchmark_csi300": csi_perf,
        "excess_return_pct": round(excess, 2) if model_perf["total_closed"] > 0 else None,
    }

    # 保存快照
    try:
        with open(BENCHMARK_PATH, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)
        print(f"  基准快照已保存: {BENCHMARK_PATH}\n")
    except Exception as e:
        print(f"  [benchmark] 快照保存失败: {e}\n", file=sys.stderr)

    return snapshot


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="模型选股 vs 沪深300 基准对比")
    parser.add_argument("--days", type=int, default=60, help="回溯天数 (默认: 60)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--show-trades", action="store_true", help="显示近期交易")
    args = parser.parse_args()

    snapshot = generate_benchmark(days=args.days, verbose=args.verbose)

    if args.show_trades:
        logs = load_trade_logs(days=args.days)
        sells = [r for r in logs if r.get("event") == "sell" and r.get("pnl_pct") is not None]
        if sells:
            print(f"\n  近期已平仓交易 ({len(sells)} 笔):")
            print(f"  {'代码':>8s} {'买入日期':>12s} {'卖出日期':>12s} {'盈亏%':>8s} {'原因':>12s}")
            print(f"  {'─'*55}")
            for s in sorted(sells, key=lambda x: x.get("sell_date", ""), reverse=True)[:10]:
                sym = s.get("symbol", "?")
                bd = s.get("buy_date", "")
                sd = s.get("sell_date", "")
                pnl = s.get("pnl_pct", 0)
                reason = s.get("reason", "")
                print(f"  {sym:>8s} {bd:>12s} {sd:>12s} {pnl:>+7.2f}% {reason:>12s}")
            print()
