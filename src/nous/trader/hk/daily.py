#!/usr/bin/env python3
"""
港股量化盘日报生成器

交易日 16:10 运行（港股16:00收盘），生成 Markdown 日报并写入 messages 表。

用法：
    python quant_hk_daily.py [--db PATH] [--date YYYY-MM-DD]

输出：
    - 写入 screener.db 的 messages 表，type='quant_hk_daily'
    - 打印日报摘要到 stdout（cron 可捕获发送微信）
"""

import sys
import json
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path.home() / "code/stock-advisor"
sys.path.insert(0, str(PROJECT_DIR))

from nous.trader.hk.executor import QuantHKExecutor, calc_factor_score


def format_money(val: float) -> str:
    """格式化金额（万/亿）"""
    if abs(val) >= 1_0000_0000:
        return f"¥{val/1_0000_0000:.2f}亿"
    elif abs(val) >= 1_0000:
        return f"¥{val/1_0000:.2f}万"
    else:
        return f"¥{val:.2f}"


def generate_daily_report(db_path: str = "",
                          report_date: str = "") -> str:
    """生成港股量化盘日报 Markdown

    Args:
        db_path: screener.db 路径
        report_date: 日报日期（默认今天）

    Returns:
        markdown 日报字符串
    """
    today = report_date or date.today().isoformat()
    engine = QuantHKExecutor(db_path=db_path)

    state = engine.get_current_state()

    # === 构建日报 ===
    lines = []
    lines.append(f"# 🇭🇰 港股量化盘日报 — {today}")
    lines.append("")

    # --- 总览 ---
    total_asset = state.get("total_asset", 0)
    cash = state.get("cash", 0)
    position_count = state.get("position_count", 0)
    equity = total_asset - cash if total_asset > cash else total_asset

    lines.append("## 📊 总览")
    lines.append("")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 总资产 | {format_money(total_asset)} |")
    lines.append(f"| 现金 | {format_money(cash)} |")
    lines.append(f"| 仓位 | {equity/total_asset*100:.1f}% ({position_count}只) |"
                 if total_asset > 0 else f"| 仓位 | 0% |")

    # NAV 历史
    nav_history = state.get("nav_history", [])
    if nav_history:
        latest_nav = float(nav_history[0]["nav"]) if len(nav_history) > 0 else 0
        if len(nav_history) > 1:
            prev_nav = float(nav_history[1]["nav"]) if len(nav_history) > 1 else latest_nav
            daily_return = (latest_nav / prev_nav - 1) * 100 if prev_nav > 0 else 0
            lines.append(f"| 日收益率 | {daily_return:+.2f}% |")
        if len(nav_history) > 5:
            start_nav = float(nav_history[-1]["nav"]) if len(nav_history) > 5 else latest_nav
            total_return = (latest_nav / start_nav - 1) * 100 if start_nav > 0 else 0
            lines.append(f"| 近5日收益 | {total_return:+.2f}% |")

    # 今日交易
    today_trades = state.get("today_trades", [])
    buy_count = sum(r["cnt"] for r in today_trades if r["action"] == "buy")
    sell_count = sum(r["cnt"] for r in today_trades if r["action"] == "sell")
    buy_amount = sum(r["total_amount"] or 0 for r in today_trades if r["action"] == "buy")
    sell_amount = sum(r["total_amount"] or 0 for r in today_trades if r["action"] == "sell")
    lines.append(f"| 买入 | {buy_count}笔 {format_money(buy_amount)} |")
    lines.append(f"| 卖出 | {sell_count}笔 {format_money(sell_amount)} |")
    lines.append("")

    # --- 持仓明细 ---
    positions = state.get("positions", [])
    lines.append("## 📋 持仓明细")
    lines.append("")
    if positions:
        lines.append(f"| # | 代码 | 名称 | 权重 | 成本 | 现价 | 股数 | 市值 | 评分 |")
        lines.append(f"|---|------|------|------|------|------|------|------|------|")

        for i, pos in enumerate(positions, 1):
            symbol = pos["symbol"]
            name = pos["name"]
            weight = float(pos.get("weight", 0)) * 100
            entry_price = float(pos.get("entry_price", 0))
            shares = int(pos.get("shares", 0))
            factor_score = float(pos.get("factor_score", 0) or 0)

            # 获取最新价格
            current_price = entry_price  # fallback
            try:
                import sqlite3
                db_path_actual = db_path or str(Path.home() / "code/stock-screener" / "data" / "screener.db")
                db = sqlite3.connect(db_path_actual)
                row = db.execute("""
                    SELECT close FROM stock_daily_all
                    WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1
                """, (symbol,)).fetchone()
                if row and row[0]:
                    current_price = float(row[0])
                db.close()
            except Exception:
                pass

            market_value = current_price * shares
            pnl_pct = (current_price / entry_price - 1) * 100 if entry_price > 0 else 0

            lines.append(
                f"| {i} | {symbol} | {name} | {weight:.1f}% | "
                f"{entry_price:.2f} | {current_price:.2f} | {shares} | "
                f"{format_money(market_value)} | {factor_score:.1f} |"
            )
    else:
        lines.append("> 当前无持仓")

    lines.append("")

    # --- 交易记录 ---
    lines.append("## 📝 今日交易")
    lines.append("")

    try:
        import sqlite3
        db_path_actual = db_path or str(Path.home() / "code/stock-screener" / "data" / "screener.db")
        db = sqlite3.connect(db_path_actual)
        db.row_factory = sqlite3.Row
        trades = db.execute("""
            SELECT * FROM quant_hk_trades
            WHERE trade_date = ?
            ORDER BY id
        """, (today,)).fetchall()
        db.close()

        if trades:
            lines.append(f"| 时间 | 代码 | 名称 | 方向 | 价格 | 股数 | 金额 | 原因 |")
            lines.append(f"|------|------|------|------|------|------|------|------|")
            for t in trades:
                lines.append(
                    f"| {t['trade_date']} | {t['symbol']} | {t['name']} | "
                    f"{'🟢买入' if t['action'] == 'buy' else '🔴卖出' if t['action'] == 'sell' else t['action']} | "
                    f"{t['price']:.2f} | {t['shares']} | "
                    f"{format_money(t['amount'])} | {t.get('reason', '')} |"
                )
        else:
            lines.append("> 今日无交易")
    except Exception as e:
        lines.append(f"> 交易记录查询失败: {e}")

    lines.append("")

    # --- 风险提示 ---
    lines.append("## ⚠️ 风险提示")
    lines.append("")
    lines.append("- 港股为 T+0 交易，但本策略限制单日同标的买卖不超过1次完整 round-trip")
    lines.append("- ATR×2 止损机制：当股价跌破入场价 - 2×ATR 时自动卖出")
    lines.append("- 卖出后 60 分钟冷却期内不再交易该标的")
    lines.append(f"- 日换手上限 {engine.MAX_DAILY_TURNOVER*100:.0f}%")
    lines.append(f"- 目标持仓 {engine.TARGET_POSITIONS} 只，最大 {engine.MAX_POSITIONS} 只")
    lines.append("")

    # --- 市场情绪 ---
    lines.append("## 🌐 港股市场")
    lines.append("")
    try:
        import sqlite3
        db_path_actual = db_path or str(Path.home() / "code/stock-screener" / "data" / "screener.db")
        db = sqlite3.connect(db_path_actual)
        # 恒指表现
        hsi = db.execute("""
            SELECT trade_date, close FROM stock_daily_all
            WHERE symbol = 'sh000001'  -- 用上证作为参考（港股指数可能需要额外数据源）
            ORDER BY trade_date DESC LIMIT 1
        """).fetchone()

        if hsi:
            lines.append(f"- 上证指数: {hsi[1]:.2f} ({hsi[0]})")

        # 全球宏观
        macro = db.execute("""
            SELECT symbol, close FROM index_global_daily
            WHERE symbol IN ('VIX', 'USDCNH')
            AND trade_date = (SELECT MAX(trade_date) FROM index_global_daily WHERE symbol IN ('VIX', 'USDCNH'))
        """).fetchall()
        db.close()

        seen = set()
        for r in macro:
            sym = r[0]
            if sym not in seen:
                seen.add(sym)
                lines.append(f"- {sym}: {r[1]}")

    except Exception:
        pass

    lines.append("")
    lines.append("---")
    lines.append(f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)


def write_to_messages(db_path: str, title: str, summary: str, body_md: str,
                      source_cron: str = "quant_hk_daily"):
    """写入 messages 表"""
    try:
        import sqlite3
        db = sqlite3.connect(db_path)
        now = datetime.now().isoformat()
        db.execute("""
            INSERT INTO messages (type, title, summary, body_md, source_cron, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("quant_hk_daily", title, summary, body_md, source_cron, now))
        db.commit()
        db.close()
        return True
    except Exception as e:
        print(f"  [quant_hk_daily] 写入 messages 表失败: {e}", file=sys.stderr)
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="港股量化盘日报生成器")
    parser.add_argument("--db", default="", help="screener.db 路径")
    parser.add_argument("--date", default="", help="日报日期 (YYYY-MM-DD)")
    parser.add_argument("--no-msg", action="store_true", help="不写入 messages 表")
    args = parser.parse_args()

    db_path = args.db or str(Path.home() / "code/stock-screener" / "data" / "screener.db")
    report_date = args.date or date.today().isoformat()

    print(f"📊 生成港股量化盘日报: {report_date}", file=sys.stderr)

    # 生成日报
    report_md = generate_daily_report(db_path=db_path, report_date=report_date)

    # 提取摘要（前500字符）
    summary_text = report_md[:500].replace("#", "").strip()

    # 写入 messages 表
    if not args.no_msg:
        title = f"🇭🇰 港股量化盘日报 {report_date}"
        ok = write_to_messages(
            db_path=db_path,
            title=title,
            summary=summary_text,
            body_md=report_md,
        )
        print(f"  → 写入 messages 表: {'✅成功' if ok else '❌失败'}", file=sys.stderr)

    # 输出日报（cron / stdout 可见）
    print("===REPORT_START===")
    print(report_md)
    print("===REPORT_END===")


if __name__ == "__main__":
    main()
