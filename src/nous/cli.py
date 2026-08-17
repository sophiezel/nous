"""Nous CLI — unified entry point for all operations.

Usage:
    nous screen    全量筛选
    nous review    鳄鱼派信号复盘
    nous recommend 每日荐股
    nous backtest  策略回测
    nous accept    V2 投研验收门禁
    nous data status|health  数据管理
    nous trade check         持仓检查
    nous model status        模型健康
    nous cron list|run       调度器
    nous serve               启动API
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

app = typer.Typer(
    name="nous",
    help="独立量化投研系统 — 双引擎荐股 × 鳄鱼派信号 × 全链路自动化",
    add_completion=False,
)

console = Console()


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
    config_dir: str = typer.Option("", "--config-dir", help="配置文件目录"),
):
    """Nous 量化投研系统."""
    if config_dir:
        import os
        os.environ["NOUS_CONFIG_DIR"] = config_dir


# ═══════════════════════════════════════════════════════════════════════
# Engine commands
# ═══════════════════════════════════════════════════════════════════════

@app.command()
def screen(
    market: str = typer.Option("a", "--market", "-m", help="市场: a=A股, hk=港股"),
    date: str = typer.Option("", "--date", "-d", help="日期, 默认最新交易日"),
    top_n: int = typer.Option(30, "--top", "-n", help="显示前N只"),
):
    """全量股票筛选 (海鹰F3粗筛 + 龙脉TRL评分)."""
    import time
    from nous.data.storage import get_db
    from nous.engine.pipelines.coarse_filter import coarse_filter_a_long
    from nous.engine.pipelines.trl_recommender import run_trl_track

    t0 = time.time()
    conn = get_db(write=False)
    try:
        report_date = date or conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE market=?", (market,)).fetchone()[0]

        console.print(Panel.fit("[bold cyan]Nous Screen[/bold cyan]", border_style="cyan"))
        console.print(f"  日期: {report_date} | 市场: {market} | 全市场: {total}只\n")

        t1 = time.time()
        symbols = coarse_filter_a_long(top_n=500, as_of_date=report_date)
        console.print(f"  [dim]粗筛(海鹰F3): {len(symbols)}/{total}只 ({(time.time()-t1):.1f}s)[/dim]")

        t2 = time.time()
        picks = run_trl_track(report_date=report_date, dry_run=True)
        if picks:
            console.print(f"  [dim]TRL推荐: {len(picks)}只 ({(time.time()-t2):.1f}s)[/dim]\n")
            table = Table(title=f"荐股 TOP{min(len(picks), top_n)}")
            table.add_column("代码", style="cyan")
            table.add_column("名称")
            table.add_column("得分", justify="right", style="green")
            table.add_column("引擎", style="dim")
            for p in picks[:top_n]:
                table.add_row(
                    str(p.get("symbol", "")), str(p.get("name", "")),
                    f"{p.get('score', 0):.1f}", str(p.get("engine", "TRL")),
                )
            console.print(table)
        else:
            console.print(f"  [dim]TRL: 今日无推荐 ({(time.time()-t2):.1f}s)[/dim]")
            console.print(f"\n  [dim]粗筛TOP{min(len(symbols), top_n)}:[/dim]")
            # Look up names from stock_basic
            placeholders = ",".join("?" * len(symbols[:top_n]))
            names = {}
            for row in conn.execute(
                f"SELECT symbol, name FROM stock_basic WHERE symbol IN ({placeholders})",
                symbols[:top_n]
            ).fetchall():
                names[row[0]] = row[1]
            for s in symbols[:top_n]:
                name = names.get(s, "")
                console.print(f"    {s}  {name}")

        console.print(f"\n  [dim]总耗时: {time.time()-t0:.1f}s[/dim]")
    finally:
        conn.close()


@app.command()
def review(
    mode: str = typer.Option("signal", "--mode", "-m", help="signal|market|full"),
    date: str = typer.Option("", "--date", "-d", help="日期, 默认最新交易日"),
):
    """鳄鱼派信号复盘 — 六信号评分 + 操作建议."""
    import time
    from nous.data.storage import get_db
    from nous.engine.signals.crocodile_signals import evaluate_crocodile_signals

    t0 = time.time()
    conn = get_db(write=False)
    try:
        report_date = date or conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
        result = evaluate_crocodile_signals(conn, trade_date=report_date)

        console.print(Panel.fit("[bold cyan]鳄鱼派信号复盘[/bold cyan]", border_style="cyan"))
        console.print(f"  日期: {report_date} | 总分: [bold]{result['total_score']}[/bold]/100 | 判定: [bold]{result['verdict']}[/bold]\n")

        # Signal table
        table = Table(title="六信号详情")
        table.add_column("信号", style="cyan")
        table.add_column("状态")
        table.add_column("评分", justify="right")
        table.add_column("详情")

        sig_names = {
            "two_feet": "两只脚", "locomotive": "火车头", "crowding": "拥挤度",
            "mainline": "主线", "capital": "资金情绪", "basis": "基差",
        }
        for key, sig in result["signals"].items():
            score = sig.get("score", 0)
            emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
            name = sig_names.get(key, key)
            detail = sig.get("status") or sig.get("level") or sig.get("stage") or sig.get("signal") or ""
            table.add_row(f"{emoji} {name}", detail, f"{score}", str(sig.get("summary", ""))[:60])

        console.print(table)

        # Market context
        if mode in ("market", "full"):
            a_count = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE market='a'").fetchone()[0]
            hk_count = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE market='hk'").fetchone()[0]
            console.print(f"\n  [dim]市场: A股{a_count} + 港股{hk_count}[/dim]")

        console.print(f"\n  [dim]耗时: {time.time()-t0:.1f}s[/dim]")
    finally:
        conn.close()


@app.command()
def recommend(
    date: str = typer.Option("", "--date", "-d", help="日期"),
    top_n: int = typer.Option(20, "--top", "-n", help="显示前N只"),
):
    """每日荐股报告 (粗筛+TRL评分+信号增强)."""
    import time
    from nous.data.storage import get_db
    from nous.engine.pipelines.coarse_filter import coarse_filter_a_long
    from nous.engine.pipelines.trl_recommender import run_trl_track
    from nous.engine.signals.crocodile_signals import evaluate_crocodile_signals

    t0 = time.time()
    conn = get_db(write=False)
    try:
        report_date = date or conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
        console.print(Panel.fit("[bold cyan]每日荐股报告[/bold cyan]", border_style="cyan"))
        console.print(f"  日期: {report_date}\n")

        # Signals for context
        sig = evaluate_crocodile_signals(conn, trade_date=report_date)
        console.print(f"  市场信号: {sig['verdict']} (总分{sig['total_score']})\n")

        # Coarse filter
        symbols = coarse_filter_a_long(top_n=500, as_of_date=report_date)
        console.print(f"  [dim]粗筛: {len(symbols)}只[/dim]")

        # TRL picks
        picks = run_trl_track(report_date=report_date, dry_run=True)
        if picks:
            table = Table(title=f"荐股 TOP{min(len(picks), top_n)}")
            table.add_column("#", style="dim")
            table.add_column("代码", style="cyan bold")
            table.add_column("名称")
            table.add_column("得分", style="green")
            for i, p in enumerate(picks[:top_n], 1):
                table.add_row(str(i), str(p.get("symbol", "")), str(p.get("name", "")), f"{p.get('score', 0):.1f}")
            console.print(table)
        else:
            console.print(f"  [yellow]TRL: 今日无推荐[/yellow]")
            console.print(f"\n  [dim]粗筛TOP{top_n}:[/dim]")
            placeholders = ",".join("?" * len(symbols[:top_n]))
            names = {}
            for row in conn.execute(
                f"SELECT symbol, name FROM stock_basic WHERE symbol IN ({placeholders})",
                symbols[:top_n]
            ).fetchall():
                names[row[0]] = row[1]
            for s in symbols[:top_n]:
                name = names.get(s, "")
                console.print(f"    {s}  {name}")

        console.print(f"\n  [dim]耗时: {time.time()-t0:.1f}s[/dim]")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Data commands
# ═══════════════════════════════════════════════════════════════════════

@app.command()
def data(
    action: str = typer.Argument(
        "status",
        help="status|health|freshness|assert|update|list|chain",
    ),
    source: str = typer.Option("all", "--source", "-s", help="数据源: all|daily|fundamental|index|margin|hsgt|lhb|fund-flow|futures|sentiment|industry|global-index"),
    domain: str = typer.Option("all", "--domain", "-d", help="assert 域: micro|macro|capital|factor|recommend|all"),
    consumer: str = typer.Option("all", "--consumer", "-c", help="assert 消费者: recommend|trl|review|backtest|all"),
    chain: str = typer.Option(
        "status",
        "--chain",
        help="chain 子命令: status|post-close|morning|S1|S2|S3|S4|S5",
    ),
):
    """数据管线管理."""
    if action == "status":
        _data_status()
    elif action == "health":
        _data_health()
    elif action == "freshness":
        _data_freshness()
    elif action == "assert":
        _data_assert(domain=domain, consumer=consumer)
    elif action == "update":
        _data_update(source)
    elif action == "list":
        _data_update("list")
    elif action == "chain":
        _data_chain(chain)
    else:
        console.print(
            "[yellow]未知操作: "
            f"{action}. 可用: status, health, freshness, assert, update, list, chain[/yellow]"
        )


def _data_chain(action: str = "status"):
    """Provider DAG: Features→Factors→Assert→Consume→Observe."""
    from nous.data.quality import pipeline_dag as dag

    console.print(Panel.fit("[bold cyan]Provider DAG[/bold cyan]", border_style="cyan"))
    action = (action or "status").strip()
    if action == "status":
        st = dag.read_status()
        if not st:
            console.print("  [yellow]无 chain_status.json[/yellow]")
            raise typer.Exit(1)
        color = "green" if st.get("ok") else "red"
        console.print(
            f"  链: {st.get('chain')}  裁决: [{color}]"
            f"{'通过' if st.get('ok') else '未通过'}[/{color}]  "
            f"耗时={st.get('elapsed_s')}s"
        )
        for s in st.get("stages") or []:
            mark = "✓" if s.get("ok") else "✗"
            c = "green" if s.get("ok") else "red"
            console.print(
                f"  [{c}]{mark}[/{c}] {s.get('name')}: {s.get('message', '')} "
                f"({s.get('elapsed_s')}s)"
            )
        if not st.get("ok"):
            raise typer.Exit(1)
        return

    if action == "post-close":
        result = dag.run_post_close()
    elif action == "morning":
        result = dag.run_morning()
    elif action.upper() in dag.STAGES:
        result = dag.run_stage(action.upper())
    else:
        console.print(
            f"[yellow]未知 chain 动作: {action}. "
            "可用: status|post-close|morning|S1|S2|S3|S4|S5[/yellow]"
        )
        raise typer.Exit(2)

    color = "green" if result.ok else "red"
    console.print(
        f"  裁决: [{color}]{'通过' if result.ok else '未通过'}[/{color}]  "
        f"链={result.chain}  耗时={result.elapsed_s}s"
    )
    for s in result.stages:
        mark = "✓" if s.ok else "✗"
        c = "green" if s.ok else "red"
        console.print(f"  [{c}]{mark}[/{c}] {s.name}: {s.message} ({s.elapsed_s}s)")
    console.print(f"  [dim]状态: {dag.STATUS_PATH}[/dim]")
    if not result.ok:
        raise typer.Exit(1)


def _data_assert(domain: str = "all", consumer: str = "all"):
    """Freshness + Integrity assert (Qlib check_data_health 子集)."""
    from nous.data.quality.data_assert import run_assert, write_report

    console.print(Panel.fit("[bold cyan]数据鲜度断言[/bold cyan]", border_style="cyan"))
    report = run_assert(domain=domain, consumer=consumer)
    path = write_report(report)
    color = "green" if report.ok else "red"
    console.print(
        f"  裁决: [{color}]{'通过' if report.ok else '未通过'}[/{color}]  "
        f"P0={'✓' if report.p0_ok else '✗'}  P1={'✓' if report.p1_ok else '✗'}  "
        f"上一交易日={report.last_trade_date}"
    )
    if report.degraded:
        console.print(f"  [yellow]DEGRADED: {', '.join(report.degraded)}[/yellow]")
    for c in report.checks:
        if c.ok:
            continue
        mark = "降级" if c.soft_fail else "失败"
        console.print(f"  [red]✗[/red] [{c.priority}] {c.label}: {c.detail} ({mark})")
    console.print(f"  [dim]报告: {path} ({report.elapsed_s}s)[/dim]")
    if not report.ok:
        raise typer.Exit(1)

def _data_status():
    from nous.data.storage import get_db
    console.print(Panel.fit("[bold cyan]数据状态[/bold cyan]", border_style="cyan"))
    try:
        conn = get_db(write=False)
        latest = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
        count = conn.execute("SELECT COUNT(DISTINCT symbol) FROM stock_daily WHERE trade_date=?", (latest,)).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0]
        a_count = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE market='a'").fetchone()[0]
        hk_count = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE market='hk'").fetchone()[0]
        tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        db_size = Path(conn.execute("PRAGMA database_list").fetchone()[2]).stat().st_size / 1024 / 1024

        console.print(f"  最新交易日:  {latest}")
        console.print(f"  当日覆盖:    {count}/{total} ([green]{100*count//total}%[/green])")
        console.print(f"  市场分布:    A股{a_count} + 港股{hk_count}")
        console.print(f"  数据表:      {tables} 张")
        console.print(f"  数据库大小:  {db_size:.0f} MB")
        conn.close()
    except Exception as e:
        console.print(f"  [red]连接失败: {e}[/red]")


def _data_health():
    """Align with sla_registry — thin wrapper over data_assert."""
    from nous.data.quality.data_assert import run_assert

    console.print(Panel.fit("[bold cyan]数据质量检查[/bold cyan]", border_style="cyan"))
    report = run_assert(domain="all", consumer="all")
    for c in report.checks:
        color = "green" if c.ok else ("yellow" if c.soft_fail or c.priority in ("P2", "P3") else "red")
        mark = "✓" if c.ok else ("⚠" if c.soft_fail else "✗")
        console.print(f"  [{color}]{mark}[/{color}] [{c.priority}] {c.label:<16} {c.detail}")
    color = "green" if report.ok else "red"
    console.print(f"\n  裁决: [{color}]{'通过' if report.ok else '未通过'}[/{color}]  耗时 {report.elapsed_s}s")
    if not report.ok:
        raise typer.Exit(1)

def _data_freshness():
    """Comprehensive data freshness audit."""
    from nous.data.storage import get_db
    from datetime import date, datetime

    console.print(Panel.fit("[bold cyan]数据新鲜度审计[/bold cyan]", border_style="cyan"))
    today = date.today()
    conn = get_db(write=False)

    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    results = []
    for (tname,) in tables:
        cols = conn.execute(f"PRAGMA table_info({tname})").fetchall()
        date_cols = [c[1] for c in cols if 'date' in c[1].lower() or c[1] in ('trade_date','snapshot_date','entry_date','exit_date')]
        if not date_cols or tname.startswith('sqlite_') or 'stock_daily_' in tname:
            continue
        for col in date_cols:
            try:
                r = conn.execute(f"SELECT MAX({col}), COUNT(*) FROM [{tname}]").fetchone()
                if r and r[1] and r[1] > 0:
                    lag = (today - date.fromisoformat(r[0])).days if r[0] else 999
                    results.append((tname, r[0] or 'N/A', r[1], lag))
                break
            except:
                pass

    results.sort(key=lambda x: -x[3])

    for tname, latest, rows, lag in results:
        if lag > 30:
            status, color = "🔴", "red"
        elif lag > 7:
            status, color = "🟡", "yellow"
        elif lag > 2:
            status, color = "🟢", "green"
        else:
            status, color = "✅", "bright_green"
        console.print(f"  {status} [{color}]{tname:<32}[/{color}] {str(latest):<14} {rows:>8,}行  {lag:>4}d滞后")

    stale = sum(1 for _, _, _, lag in results if lag > 7)
    console.print(f"\n  [dim]{len(results)}数据源, {stale}个滞后(>7d)[/dim]")
    conn.close()


def _data_update(source: str):
    """Run data collectors."""
    from nous.data.collectors.unified import ALL_COLLECTORS, collect_all

    console.print(Panel.fit("[bold cyan]数据更新[/bold cyan]", border_style="cyan"))

    if source == "all":
        console.print(f"  执行全部 {len(ALL_COLLECTORS)} 个采集器...\n")
        results = collect_all()
    elif source == "list":
        for name, (label, _) in ALL_COLLECTORS.items():
            console.print(f"  {name:<16} {label}")
        return
    elif source in ALL_COLLECTORS:
        label, func = ALL_COLLECTORS[source]
        console.print(f"  [{source}] {label}...\n")
        try:
            r = func()
            console.print(f"  {r['status']}: {r['count']} — {r.get('message','')}")
        except Exception as e:
            console.print(f"  [red]ERROR: {e}[/red]")
    else:
        console.print(f"  [red]未知数据源: {source}[/red]")
        console.print(f"  可用: all, list, {', '.join(ALL_COLLECTORS.keys())}")


# ═══════════════════════════════════════════════════════════════════════
# Trade commands
# ═══════════════════════════════════════════════════════════════════════

@app.command()
def trade(
    action: str = typer.Argument("check", help="check|position"),
):
    """交易与持仓管理."""
    if action == "check":
        _trade_check()
    elif action == "position":
        _trade_position()
    else:
        console.print(f"[yellow]未知操作: {action}[/yellow]")


def _trade_check():
    """Show backtest framework status and available strategies."""
    from rich.table import Table
    console.print(Panel.fit("[bold cyan]回测系统 (因子驱动 WF)[/bold cyan]", border_style="cyan"))
    
    try:
        from nous.engine.backtest.strategies import list_strategies, get_strategy
        from nous.engine.backtest.data_handler import PointInTimeDataHandler
        
        dh = PointInTimeDataHandler("2026-07-10")
        days = dh.get_trading_days("2025-07-01", "2026-07-10")
        universe = dh.get_universe_count("a")
        dh.close()
        
        console.print(f"\n  [bold]系统架构[/bold]")
        console.print(f"  因子管道 → LightGBM模型 → 组合优化 → 真实成本 → WF验证")
        console.print(f"  数据: {days[0]}→{days[-1]} ({len(days)}天), {universe}只(survivorship-free)")
        console.print(f"  成本: 印花税0.05% + 佣金0.025% + 滑点0.1%")
        console.print(f"  做空: IF/IC/IM股指期货对冲(非虚构融券)")
        
        console.print(f"\n  [bold]可用策略[/bold]")
        table = Table()
        table.add_column("策略", style="cyan")
        table.add_column("因子组", style="dim")
        table.add_column("换仓", justify="center")
        table.add_column("组合方式")
        table.add_column("特点")
        
        for name in list_strategies():
            s = get_strategy(name)
            groups = "+".join(s.factors.groups)
            freq = f"{s.rebalance_freq}天"
            method = s.portfolio.method
            if s.portfolio.hedge_beta_target == 0:
                method += "+对冲"
            table.add_row(name, groups, freq, method, s.description[:40])
        
        console.print(table)
        console.print(f"\n  [dim]运行: nous backtest --strategy 海鹰F3[/dim]")
        console.print(f"  [dim]      nous backtest --strategy 龙脉TRL[/dim]")
        
    except Exception as e:
        console.print(f"  [yellow]状态查询受限: {e}[/yellow]")


def _trade_position():
    from nous.data.storage import get_db
    console.print(Panel.fit("[bold cyan]当前持仓[/bold cyan]", border_style="cyan"))
    try:
        conn = get_db(write=False)
        positions = conn.execute(
            "SELECT symbol, name, shares, entry_price, current_price, pnl_pct FROM sim_position WHERE shares > 0"
        ).fetchall()
        if positions:
            table = Table()
            table.add_column("代码", style="cyan")
            table.add_column("名称")
            table.add_column("股数", justify="right")
            table.add_column("成本", justify="right")
            table.add_column("现价", justify="right")
            table.add_column("盈亏%", justify="right")
            for p in positions:
                pnl = p["pnl_pct"] or 0
                color = "green" if pnl > 0 else "red"
                table.add_row(p["symbol"], p["name"], str(p["shares"]),
                              f"{p['entry_price']:.2f}", f"{p['current_price']:.2f}",
                              f"[{color}]{pnl:+.1f}%[/{color}]")
            console.print(table)
        else:
            console.print("  [dim]无持仓[/dim]")
        conn.close()
    except Exception as e:
        console.print(f"  [yellow]查询受限: {e}[/yellow]")


# ═══════════════════════════════════════════════════════════════════════
# Model commands
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# Backtest commands
# ═══════════════════════════════════════════════════════════════════════

@app.command()
def backtest(
    strategy: str = typer.Option("", "--strategy", "-s", help="策略名: 海鹰F3/龙脉TRL/鳄鱼派/市场中性/指数增强/多因子综合"),
    start: str = typer.Option("2022-01-01", "--start", help="开始日期"),
    end: str = typer.Option("2026-07-01", "--end", "-e", help="结束日期"),
    folds: int = typer.Option(5, "--folds", "-f", help="Walk-Forward折数"),
    capital: float = typer.Option(1_000_000, "--capital", "-c", help="初始资金"),
    market: str = typer.Option("a", "--market", "-m", help="市场: a/hk"),
    validate: bool = typer.Option(True, "--validate/--no-validate", help="统计验证"),
    batch: bool = typer.Option(False, "--batch", "-b", help="批量对比全部策略"),
):
    """回测系统 — 因子驱动 Walk-Forward + 统计验证."""
    from nous.engine.backtest.strategies import list_strategies, get_strategy
    from nous.engine.backtest.engine import BacktestEngine
    from nous.engine.backtest.batch_runner import print_summary
    from rich.table import Table
    import time as tmod

    console.print(Panel.fit("[bold cyan]回测系统 (因子驱动 WF)[/bold cyan]", border_style="cyan"))

    if batch:
        all_strategies = list_strategies()
        console.print(f"  批量对比: {len(all_strategies)}个策略")
        console.print(f"  参数: {start}→{end}, {folds}折, 初始{capital:,.0f}\n")
        results = {}
        for name in all_strategies:
            t0 = tmod.time()
            engine = BacktestEngine(
                strategy=name, start_date=start, end_date=end,
                initial_capital=capital, wf_folds=folds, market=market,
                do_walk_forward=True,
            )
            try:
                r = engine.run()
                results[name] = r
                console.print(f"  {name:<12} SR={r.sharpe_ratio:.3f} "
                            f"Ret={r.total_return*100:+.1f}% DD={r.max_drawdown*100:.1f}% "
                            f"[dim]({tmod.time()-t0:.0f}s)[/dim]")
            except Exception as e:
                console.print(f"  {name:<12} [red]FAILED: {e}[/red]")
            finally:
                engine.close()
        if results:
            print_summary(results)
        return

    if not strategy:
        console.print(f"  可用策略: {', '.join(list_strategies())}")
        console.print(f"  [dim]示例: nous backtest --strategy 海鹰F3 --start 2025-11-01 --end 2026-07-10[/dim]")
        console.print(f"  [dim]      nous backtest --strategy 龙脉TRL --start 2025-11-01 --end 2026-07-10[/dim]")
        console.print(f"  [dim]批量: nous backtest --batch[/dim]")
        console.print(f"  [dim]验收: nous accept  （含海鹰F3 + 龙脉TRL）[/dim]")
        return

    if strategy not in list_strategies():
        console.print(f"  [red]未知策略: {strategy}[/red]")
        console.print(f"  可用: {', '.join(list_strategies())}")
        return

    s = get_strategy(strategy)
    console.print(f"\n  [bold]{strategy}[/bold]: {s.description}")
    console.print(f"  因子组: {'+'.join(s.factors.groups)} | 换仓: {s.rebalance_freq}天 | 组合: {s.portfolio.method}")
    console.print(f"  参数: {start}→{end}, {folds}折WF, 初始{capital:,.0f}")
    console.print("  [dim]说明: 同参数同数据下结果可复现（确定性回测）；两次完全一致是正常现象[/dim]\n")

    t0 = tmod.time()
    engine = BacktestEngine(
        strategy=strategy, start_date=start, end_date=end,
        initial_capital=capital, wf_folds=folds, market=market,
        do_walk_forward=True,
    )
    try:
        result = engine.run()
    finally:
        engine.close()

    # Results table
    table = Table(title=f"回测结果: {strategy}")
    table.add_column("指标", style="cyan")
    table.add_column("数值", justify="right")
    
    color_ret = "green" if result.total_return > 0 else "red"
    table.add_row("总收益", f"[{color_ret}]{result.total_return*100:+.2f}%[/{color_ret}]")
    table.add_row("年化收益", f"[{color_ret}]{result.annual_return*100:+.2f}%[/{color_ret}]")
    table.add_row("夏普比率", f"{result.sharpe_ratio:.3f}")
    if getattr(result, "sharpe_winsorized", None):
        table.add_row("截尾夏普", f"{result.sharpe_winsorized:.3f}")
    if getattr(result, "sortino_ratio", None):
        table.add_row("索提诺比率", f"{result.sortino_ratio:.3f}")
    table.add_row("最大回撤", f"[red]{result.max_drawdown*100:.1f}%[/red]")
    table.add_row("胜率", f"{result.win_rate*100:.0f}%")
    table.add_row("盈亏比", f"{result.profit_factor:.2f}")
    table.add_row("交易笔数", str(result.total_trades))
    if getattr(result, "min_daily_return", None) is not None:
        table.add_row(
            "单日收益极值",
            f"{result.min_daily_return*100:.2f}% / {result.max_daily_return*100:.2f}%",
        )
    if getattr(result, "n_return_spikes", None) is not None:
        table.add_row("收益尖刺(|r|>10%)", str(result.n_return_spikes))
    flags = getattr(result, "integrity_flags", None) or {}
    trusted = flags.get("TRUSTED")
    if trusted is not None:
        cred = "可信" if trusted and not flags.get("SUSPICIOUS") else (
            "基本可信(存疑)" if trusted else "不可信"
        )
        color = "green" if trusted else "red"
        table.add_row("净值可信度", f"[{color}]{cred}[/{color}]")
    console.print(table)

    # Fold details
    if result.fold_details:
        console.print(f"\n  [bold]WF折详情:[/bold]")
        for fd in result.fold_details:
            status = "✓" if fd["return"] > 0 else "✗"
            console.print(f"    Fold{fd['fold']}: {fd['start']}→{fd['end']} "
                        f"ret={fd['return']*100:+.1f}% sr={fd['sharpe']:.3f} "
                        f"model={'✓' if fd['model_trained'] else '✗'} {status}")

    # Validation
    if validate and result.sharpe_ratio > 0:
        from nous.engine.backtest.validator import BacktestValidator
        v = BacktestValidator()
        n_trials = folds * 3  # estimate
        dsr = v.deflated_sharpe_ratio(result.sharpe_ratio, n_trials=n_trials)
        console.print(f"\n  [bold]统计验证:[/bold]")
        console.print(f"  Deflated SR: {dsr:.4f} {'✓' if dsr < 0.05 else '✗'}")
        console.print(f"  观测SR: {result.sharpe_ratio:.3f} (需折扣至{result.sharpe_ratio * (1 - min(dsr*5, 0.5)):.3f})")

    console.print(f"  [dim]耗时: {tmod.time()-t0:.1f}s[/dim]")


def _backtest_validate():
    """Run standalone validation on existing WF backtest results."""
    console.print("[yellow]validate mode 已集成到 backtest 命令. 使用 --validate[/yellow]")
    console.print("[dim]示例: nous backtest --strategy 海鹰F3 --validate[/dim]")
    console.print("[dim]      nous backtest --strategy 龙脉TRL --validate[/dim]")


@app.command()
def model(
    action: str = typer.Argument("status", help="status|health"),
):
    """ML 模型管理."""
    if action in ("status", "health"):
        _model_status()
    else:
        console.print(f"[yellow]未知操作: {action}[/yellow]")


def _model_status():
    from nous.data.storage import get_db
    console.print(Panel.fit("[bold cyan]模型状态[/bold cyan]", border_style="cyan"))
    try:
        conn = get_db(write=False)
        # Model registry
        models = conn.execute("SELECT * FROM model_registry ORDER BY created_at DESC LIMIT 5").fetchall()
        if models:
            console.print(f"  已注册模型: {len(models)}")
            for m in models:
                d = dict(m)
                console.print(f"    {d.get('model_name','?')}  {d.get('engine','?')}  IC={d.get('ic_mean',0):.3f}")
        else:
            console.print("  [dim]无已注册模型[/dim]")

        # Health log
        health = conn.execute("SELECT * FROM model_health_log ORDER BY check_date DESC LIMIT 3").fetchall()
        if health:
            console.print(f"\n  最近健康检查:")
            for h in health:
                d = dict(h)
                console.print(f"    {d.get('check_date','?')}  IC={d.get('rank_ic_20d',0):.3f}  drift={d.get('feature_drift_count',0)}")

        # Factor importance
        factors = conn.execute("SELECT * FROM quant_factor_importance ORDER BY importance_enc DESC LIMIT 8").fetchall()
        if factors:
            console.print(f"\n  TOP因子重要性:")
            for f in factors:
                d = dict(f)
                console.print(f"    {d.get('factor_name','?'):<30} {d.get('importance',0):.4f}")

        conn.close()
    except Exception as e:
        console.print(f"  [yellow]查询受限: {e}[/yellow]")


# ═══════════════════════════════════════════════════════════════════════
# Serve
# ═══════════════════════════════════════════════════════════════════════

@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h"),
    port: int = typer.Option(8000, "--port", "-p"),
):
    """启动 API 服务."""
    try:
        import uvicorn
        console.print(f"[bold cyan]Nous API[/bold cyan] → http://{host}:{port}")
        uvicorn.run("nous.api.main:app", host=host, port=port, log_level="info")
    except ImportError:
        console.print("[red]uvicorn 未安装. pip install uvicorn[/red]")


# ═══════════════════════════════════════════════════════════════════════
# Cron / Scheduler
# ═══════════════════════════════════════════════════════════════════════

@app.command()
def cron(
    action: str = typer.Argument("list", help="list|run"),
    job_name: str = typer.Option("", "--job", "-j", help="任务名称"),
):
    """定时任务管理."""
    import nous.scheduler as sched

    if action == "list":
        console.print(Panel.fit("[bold cyan]调度任务[/bold cyan]", border_style="cyan"))
        jobs = sched.JOBS
        table = Table()
        table.add_column("名称", style="cyan")
        table.add_column("调度", style="dim")
        table.add_column("说明")
        table.add_column("超时", justify="right")
        for name, schedule, cmd, desc, timeout in jobs:
            table.add_row(name, schedule, desc, f"{timeout}s")
        console.print(table)
        console.print(f"\n  [dim]{len(jobs)} jobs registered[/dim]")

    elif action == "run":
        if not job_name:
            console.print("[red]需要 -j JOB_NAME[/red]")
            return
        console.print(f"[bold cyan]执行: {job_name}[/bold cyan]")
        sched.run_job(job_name)

    else:
        console.print(f"[yellow]未知操作: {action}. 可用: list, run[/yellow]")


# ═══════════════════════════════════════════════════════════════════════
# Acceptance (V2 投研门禁)
# ═══════════════════════════════════════════════════════════════════════

@app.command("accept")
def accept():
    """V2 投研验收：引擎回归 + 海鹰F3/龙脉TRL 双引擎 WF 可信度 + 荐股/仓位门禁."""
    import runpy
    from pathlib import Path

    # Prefer installed package location → repo scripts/
    candidates = [
        Path(__file__).resolve().parents[2] / "scripts" / "run_acceptance_v2.py",  # repo root from src/nous/cli.py
        Path.home() / "code" / "nous" / "scripts" / "run_acceptance_v2.py",
        Path.cwd() / "scripts" / "run_acceptance_v2.py",
    ]
    script = next((p for p in candidates if p.exists()), None)
    if script is None:
        console.print("[red]未找到 scripts/run_acceptance_v2.py[/red]")
        raise typer.Exit(1)

    console.print(Panel.fit("[bold cyan]Nous V2 投研验收[/bold cyan]", border_style="cyan"))
    console.print(f"  脚本: {script}\n")
    # runpy 以 __main__ 执行，沿用脚本自身 exit code
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if code == 0:
            console.print("\n[green]验收通过[/green] — 报告见 docs/acceptance/<日期>/ACCEPTANCE_REPORT.md")
        else:
            console.print("\n[red]验收未通过[/red] — 请查看同目录报告")
        raise typer.Exit(code)


# ═══════════════════════════════════════════════════════════════════════
# Version
# ═══════════════════════════════════════════════════════════════════════

@app.command()
def version():
    """版本信息."""
    from nous import __version__
    console.print(f"Nous v{__version__}")
