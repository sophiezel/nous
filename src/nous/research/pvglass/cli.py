"""``nous pv`` — 光伏玻璃产业链跟踪命令组。

    nous pv sources                     数据源与自动化覆盖
    nous pv fetch [--source etnet]      抓取（默认全部数据源）
    nous pv show [--group price]        指标仪表盘 / 单指标时序
    nous pv signal                      拐点信号看板
    nous pv watch [--push]              观察清单跃迁告警（只在变化时推）
    nous pv xinyi [--sens]              信义光能盈利模型 + 敏感性
    nous pv set <id> <value>            人工录入指标
    nous pv digest [--out PATH]         生成周报 Markdown
    nous pv log                         采集审计
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import yaml

from nous.core import notify
from nous.core.db import get_db
from nous.core.paths import repo_root
from nous.research.pvglass import backtest as bt
from nous.research.pvglass import digest as digest_mod
from nous.research.pvglass import pipeline
from nous.research.pvglass import signals as sig_engine
from nous.research.pvglass import store, watch, xinyi
from nous.research.pvglass.registry import Indicator, Registry, load_registry
from nous.research.pvglass.sources import DEFAULT_ORDER, available, collect_one

pv_app = typer.Typer(
    name="pv",
    help="光伏玻璃产业链跟踪 — 指标 / 采集 / 信号 / 盈利模型 / 周报",
    no_args_is_help=True,
)
console = Console()

_STATUS_STYLE = {
    "ok": "green",
    "partial": "yellow",
    "error": "red",
    "skipped": "dim",
}

_SIGNAL_STYLE = {
    sig_engine.TRIGGERED: "green",
    sig_engine.NOT_TRIGGERED: "red",
    sig_engine.PENDING: "yellow",
    sig_engine.INSUFFICIENT: "dim",
}

#: 同业横比口径：显示名 → 指标 id 后缀（跨锚定标的对齐）
COMPARE_METRICS: tuple[tuple[str, str], ...] = (
    ("营收", "_revenue"),
    ("归母净利", "_attributable_profit"),
    ("光伏玻璃毛利率", "_glass_gm"),
    ("在产日熔量", "_glass_capacity"),
    ("并网容量", "_capacity_mw"),
    ("海外产能占比", "_overseas_ratio"),
    ("净负债率", "_net_gearing"),
    ("股价", "_price"),
)


def _pick_compare_indicator(reg: Registry, anchor: str, suffix: str) -> str | None:
    """按后缀在该锚定标的的指标里选一个；股价优先用 anchors 声明的 price_indicator。"""
    if suffix == "_price":
        spec = reg.anchors.get(anchor)
        if spec and spec.price_indicator and spec.price_indicator in reg.indicators:
            return spec.price_indicator
    candidates = [i.id for i in reg.by_anchor(anchor) if i.id.endswith(suffix)]
    return candidates[0] if candidates else None


def _reg() -> Registry:
    try:
        return load_registry()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]指标字典加载失败: {exc}[/red]")
        raise typer.Exit(1) from exc


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.{digits}f}"


def _flag(indicator: Indicator, value: float | None) -> str:
    """阈值标记；方向由 bias 决定（up_bearish = 越低越好）。"""
    if value is None:
        return ""
    if indicator.bias == "up_bearish":
        if indicator.bull is not None and value <= indicator.bull:
            return f"[green]≤{indicator.bull:g}[/green]"
        if indicator.bear is not None and value >= indicator.bear:
            return f"[red]≥{indicator.bear:g}[/red]"
        return ""
    if indicator.bull is not None and value >= indicator.bull:
        return f"[green]≥{indicator.bull:g}[/green]"
    if indicator.bear is not None and value <= indicator.bear:
        return f"[red]≤{indicator.bear:g}[/red]"
    return ""


# ═══════════════════════════════════════════════════════════════════════
# sources
# ═══════════════════════════════════════════════════════════════════════
@pv_app.command("sources")
def sources_cmd():
    """列出数据源、自动化状态与覆盖的指标数。"""
    reg = _reg()
    table = Table(title="光伏玻璃跟踪 — 数据源")
    table.add_column("source", style="cyan")
    table.add_column("名称")
    table.add_column("类型")
    table.add_column("指标数", justify="right")
    table.add_column("说明", style="dim")
    for sid, spec in reg.sources.items():
        n = len(reg.by_source(sid))
        table.add_row(sid, spec.name, spec.kind, str(n), spec.desc[:46])
    console.print(table)

    stats = reg.stats()
    console.print(
        f"\n  指标 {stats['total']} 个：自动 {stats['auto']} / 半自动 {stats['seed']} / "
        f"人工 {stats['manual']}｜信号 {stats['signals']} 条"
    )
    console.print(f"  采集器实现: {', '.join(available())}")
    console.print(f"  默认顺序: {' → '.join(DEFAULT_ORDER)}")
    manual = [i for i in reg.indicators.values() if i.auto == "manual"]
    if manual:
        console.print(f"\n  [dim]人工指标 {len(manual)} 个（用 nous pv set 录入）: "
                      f"{', '.join(i.id for i in manual[:8])}…[/dim]")

    # 外部依赖预检：report_pdf 靠 poppler 的 pdftotext（换机器/上 ECS 最容易漏）
    from nous.research.pvglass.sources.hkex_pdf import pdftotext_path

    binary = pdftotext_path()
    if binary is None:
        console.print(
            "\n  [yellow]⚠ report_pdf 不可用：缺 pdftotext"
            "（macOS: brew install poppler / Linux: apt-get install poppler-utils）[/yellow]"
            "\n    [dim]缺依赖时该源降级为 skipped，不会崩也不会静默写错数[/dim]"
        )
    else:
        console.print(f"\n  [dim]report_pdf 依赖就绪: {binary}[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# fetch
# ═══════════════════════════════════════════════════════════════════════
@pv_app.command("fetch")
def fetch_cmd(
    source: str = typer.Option("", "--source", "-s", help="只抓某个数据源；默认全部"),
    days: int = typer.Option(120, "--days", "-d", help="回看天数（hkex 公告）"),
    url: list[str] = typer.Option(None, "--url", "-u", help="手工指定文章URL（mysteel）"),
    limit: int = typer.Option(5, "--limit", "-n", help="文章扫描上限（mysteel）"),
):
    """抓取指标并写入 ~/nous-data/pvglass.db。"""
    reg = _reg()
    targets = [source] if source else list(DEFAULT_ORDER)
    store.init_db()

    table = Table(title="采集结果")
    table.add_column("数据源", style="cyan")
    table.add_column("状态")
    table.add_column("条数", justify="right")
    table.add_column("耗时", justify="right", style="dim")
    table.add_column("说明")

    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        for sid in targets:
            kwargs: dict[str, object] = {"days": days}
            if sid == "mysteel":
                kwargs = {"limit": limit}
                if url:
                    kwargs["urls"] = list(url)
            result = collect_one(sid, conn, reg, **kwargs)
            store.log_fetch(conn, result)
            conn.commit()
            style = _STATUS_STYLE.get(result.status, "white")
            table.add_row(
                sid,
                f"[{style}]{result.status}[/{style}]",
                str(result.items),
                f"{result.duration_ms}ms",
                result.message[:60],
            )
    console.print(table)


# ═══════════════════════════════════════════════════════════════════════
# show
# ═══════════════════════════════════════════════════════════════════════
@pv_app.command("show")
def show_cmd(
    indicator: str = typer.Argument("", help="指标 id；留空看全部"),
    group: str = typer.Option("", "--group", "-g", help="只看某分组"),
    anchor: str = typer.Option("", "--anchor", "-a", help="只看某锚定标的: xinyi|flat|xenergy"),
    days: int = typer.Option(120, "--days", "-d", help="时序回看天数"),
    stale: bool = typer.Option(False, "--stale", help="只列出缺数据的指标"),
):
    """指标仪表盘；指定指标时打印其时间序列。"""
    reg = _reg()
    store.init_db()
    with get_db(store.DB_NAME) as conn:
        store.ensure_schema(conn)
        if indicator:
            _print_series(conn, reg, indicator, days)
            return
        latest = store.latest_map(conn)
        groups = [group] if group else reg.ordered_groups()
        for gid in groups:
            items = reg.by_group(gid)
            if anchor:
                items = [i for i in items if i.anchor == anchor]
            if not items:
                continue
            title = f"{reg.group_label(gid)} ({gid})"
            if anchor:
                title += f" — {reg.anchor_label(anchor)}"
            table = Table(title=title)
            table.add_column("指标", style="cyan")
            table.add_column("最新", justify="right")
            table.add_column("单", style="dim")
            table.add_column("日期", style="dim")
            table.add_column("变化/备注", justify="right")
            table.add_column("阈值")
            table.add_column("自动化", style="dim")
            rows = 0
            for ind in items:
                row = latest.get(ind.id)
                if row is None:
                    if stale:
                        table.add_row(ind.name, "—", ind.unit, "—", "—", "无数据", ind.auto)
                        rows += 1
                    continue
                prev = store.previous(conn, ind.id)
                delta = "—"
                if prev and prev["value"] not in (None, 0) and row["value"] is not None:
                    delta = f"{(row['value'] - prev['value']) / abs(prev['value']) * 100:+.1f}%"
                elif row["value_text"]:
                    delta = str(row["value_text"])[:22]
                table.add_row(
                    ind.name,
                    _fmt(row["value"]),
                    ind.unit or row["unit"] or "",
                    row["obs_date"],
                    delta,
                    _flag(ind, row["value"]),
                    ind.auto,
                )
                rows += 1
            if rows:
                console.print(table)


def _print_series(conn, reg: Registry, indicator_id: str, days: int) -> None:
    try:
        ind = reg.get(indicator_id)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    rows = store.series(conn, indicator_id, days=days)
    if not rows:
        console.print(f"[yellow]{ind.name}: 近{days}天无数据[/yellow]")
        if ind.note:
            console.print(f"[dim]说明: {ind.note}[/dim]")
        return
    table = Table(title=f"{ind.name} ({indicator_id})  {ind.unit}")
    table.add_column("日期", style="dim")
    table.add_column("数值", justify="right")
    table.add_column("来源", style="dim")
    table.add_column("原文/备注", style="dim")
    for r in rows:
        table.add_row(
            r["obs_date"], _fmt(r["value"]), r["source"] or "", (r["note"] or "")[:46]
        )
    console.print(table)


# ═══════════════════════════════════════════════════════════════════════
# signal
# ═══════════════════════════════════════════════════════════════════════
@pv_app.command("signal")
def signal_cmd(
    signal_id: str = typer.Option("", "--id", help="只看某条信号，如 S1_glass_price"),
):
    """拐点信号看板（自动读库判定）。"""
    reg = _reg()
    store.init_db()
    with get_db(store.DB_NAME) as conn:
        store.ensure_schema(conn)
        results = sig_engine.evaluate(conn, reg, signal_id or None)
        level, conclusion = sig_engine.verdict(results)
        color = {"bullish": "green", "watch": "yellow", "pending": "cyan"}.get(level, "red")
        console.print(Panel.fit(f"[bold {color}]{level.upper()}[/bold {color}] — {conclusion}"))

        table = Table()
        table.add_column("信号", style="cyan")
        table.add_column("状态")
        table.add_column("通过", justify="right")
        table.add_column("依据", style="dim")
        for r in results:
            details = []
            for c in r.conditions:
                if c.status == sig_engine.NO_DATA:
                    details.append(f"— {c.indicator_name}: {c.detail}")
                else:
                    mark = "✓" if c.status == sig_engine.PASS else "✗"
                    details.append(f"{mark} {c.indicator_name} {c.detail}")
            style = _SIGNAL_STYLE.get(r.status, "white")
            table.add_row(
                f"{r.signal.id}\n{r.signal.name}",
                f"[{style}]{r.status}[/{style}]",
                f"{r.passed}/{len(r.conditions)}",
                "\n".join(details),
            )
        console.print(table)
        console.print("\n[dim]信号定义在 config/pvglass_indicators.yaml → signals[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# watch — 观察清单跃迁告警
# ═══════════════════════════════════════════════════════════════════════
@pv_app.command("watch")
def watch_cmd(
    push: bool = typer.Option(False, "--push", help="有跃迁时推送到配置的通道"),
    force_push: bool = typer.Option(False, "--force-push", help="即使无跃迁也推（验证通道用）"),
    reset: bool = typer.Option(False, "--reset-baseline", help="清空基线（下次重建且不告警）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="比较但不写库，便于反复试算"),
    json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
):
    """观察清单跃迁告警 — 只在状态变化时推送。

    与 `nous pv signal` 的区别：signal 回答"现在什么状态"（无记忆），
    watch 回答"和上次比变了什么"——所以它可以每天跑而不会刷屏。

    观察三档：信号状态 / 信号内部每个条件 / 字典 watch: 段的独立阈值。
    首次运行只建基线、**不告警**；用 --reset-baseline 可重建。
    """
    reg = _reg()
    store.init_db()
    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        if reset:
            n = watch.reset(conn)
            console.print(f"[yellow]已清空 {n} 条基线状态[/yellow]（下次运行会重建且不告警）")
            if not dry_run:
                return
        outcome = watch.check(conn, reg, persist=not dry_run)

    if json_out:
        console.print_json(data={
            "ran_at": outcome.ran_at,
            "is_baseline_run": outcome.is_baseline_run,
            "verdict": outcome.verdict_level,
            "verdict_text": outcome.verdict_text,
            "persisted": outcome.persisted,
            "watched": len(outcome.items),
            "changes": [
                {"key": c.item.key, "kind": c.item.kind, "name": c.item.name,
                 "before": c.before, "after": c.after, "severity": c.severity,
                 "detail": c.item.detail}
                for c in outcome.changes
            ],
        })
    else:
        if outcome.is_baseline_run:
            console.print(Panel.fit(
                f"[cyan]已建立基线[/cyan] — 记录 {len(outcome.items)} 项状态，"
                "[bold]本次不告警[/bold]\n下次起只在状态跃迁时推送"))
        elif outcome.changes:
            head = "red" if outcome.critical_count else "yellow"
            console.print(Panel.fit(
                f"[bold {head}]{len(outcome.changes)} 项状态跃迁[/bold {head}]"
                + (f"（{outcome.critical_count} 项 critical）" if outcome.critical_count else "")))
            table = Table()
            table.add_column("对象", style="cyan")
            table.add_column("变化")
            table.add_column("依据", style="dim")
            for c in sorted(outcome.changes, key=lambda x: x.severity != watch.CRITICAL):
                mark = "🔴" if c.severity == watch.CRITICAL else "🔵"
                style = "red" if c.severity == watch.CRITICAL else "yellow"
                table.add_row(
                    f"{mark} {c.item.name}",
                    f"{c.before} → [{style}]{c.after}[/{style}]",
                    c.item.detail[:78],
                )
            console.print(table)
        else:
            console.print(
                f"[dim]无状态跃迁（已观察 {len(outcome.items)} 项）[/dim]"
            )
        console.print(f"  判读: [bold]{outcome.verdict_level.upper()}[/bold] — {outcome.verdict_text}")
        if dry_run:
            console.print("  [dim]--dry-run：未写库[/dim]")

    should_push = push and (outcome.alerted or force_push) and not dry_run
    if should_push:
        results = notify.send(outcome.push_title(), outcome.push_body())
        for res in results:
            style = {"ok": "green", "error": "red"}.get(res.status, "dim")
            console.print(f"  推送 {res.channel}: [{style}]{res.status}[/{style}] {res.detail[:60]}")
    elif push:
        console.print("  [dim]无跃迁 → 不推送（不刷屏）[/dim]")
    elif outcome.alerted:
        console.print("  [dim]加 --push 可推送到配置的通道[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# xinyi
# ═══════════════════════════════════════════════════════════════════════
@pv_app.command("xinyi")
def xinyi_cmd(
    scenario: str = typer.Option("", "--scenario", "-s", help="bear|base|bull，默认全部"),
    sens: bool = typer.Option(False, "--sens", help="打印Q4均价敏感性"),
    model: str = typer.Option("", "--model", help="模型文件路径"),
):
    """信义光能盈利模型：情景 + 敏感性 + 一致预期对照。"""
    store.init_db()
    cfg = xinyi.load_model(model or None)
    names = [scenario] if scenario else list((cfg.get("scenarios") or {}).keys())
    results = [xinyi.run(cfg, n) for n in names]

    table = Table(title="信义光能 H2/FY2026 情景测算（百万元人民币）")
    for col in ("情景", "Q4实现价", "减值", "H2玻璃收入", "玻璃毛利率", "H2归母", "FY2026归母", "EPS(分)", "PE"):
        table.add_column(col, justify="right" if col not in ("情景",) else "left")
    for r in results:
        table.add_row(
            f"{r.name}\n[dim]{r.label}[/dim]",
            f"{r.asp_q4:.2f}",
            _fmt(r.impairment),
            _fmt(r.h2_glass_revenue),
            f"{r.h2_glass_gm:.1f}%",
            _fmt(r.h2_attributable),
            f"[bold]{_fmt(r.fy_attributable)}[/bold]",
            f"{r.fy_eps_fen:.2f}",
            "亏损" if r.pe is None else f"{r.pe:.0f}x",
        )
    console.print(table)

    ref = cfg.get("consensus_ref") or {}
    console.print(
        f"\n  一致预期(FY2026, etnet 8家): 中位数 [bold]{_fmt(ref.get('fy2026_median'))}[/bold]"
        f"，区间 [{_fmt(ref.get('fy2026_min'))}, {_fmt(ref.get('fy2026_max'))}]"
        f"；FY2027 中位 {_fmt(ref.get('fy2027_median'))}；FY2028 中位 {_fmt(ref.get('fy2028_median'))}"
    )

    with get_db(store.DB_NAME) as conn:
        store.ensure_schema(conn)
        for fy, ind_id in (("2026", "consensus_np_fy2026"), ("2027", "consensus_np_fy2027")):
            row = store.latest(conn, ind_id)
            if row and row["value"] is not None:
                console.print(
                    f"  [dim]库内 FY{fy} 一致预期中位数: {_fmt(row['value'])} ({row['obs_date']})[/dim]"
                )

    if sens:
        srows = xinyi.sensitivity(cfg, asp_min=9.2, asp_max=11.2, step=0.2)
        stable = Table(title="敏感性：Q4 实现均价 → FY2026 归母")
        stable.add_column("Q4实现价(元/㎡)", justify="right")
        stable.add_column("H2玻璃毛利率", justify="right")
        stable.add_column("FY2026归母", justify="right")
        stable.add_column("PE", justify="right")
        for row in srows:
            color = "green" if row["fy_attributable"] > 0 else "red"
            stable.add_row(
                f"{row['asp_q4']:.2f}",
                f"{row['glass_gm']:.1f}%",
                f"[{color}]{row['fy_attributable']:.0f}[/{color}]",
                "亏损" if row["pe"] == 0 else f"{row['pe']:.0f}x",
            )
        console.print(stable)
        console.print(
            "\n  [dim]读法: 每 0.1 元/㎡ ≈ 单季毛利 0.37 亿元。"
            "bull 情景≈一致预期中位数，bear 情景≈高盛最低值 —— 分歧全在「Q4实现价 + 年末减值」。[/dim]"
        )


# ═══════════════════════════════════════════════════════════════════════
# set / digest / log
# ═══════════════════════════════════════════════════════════════════════
@pv_app.command("set")
def set_cmd(
    indicator_id: str = typer.Argument(..., help="指标 id，如 pv_install_cn_monthly"),
    value: float = typer.Argument(..., help="数值"),
    obs_date: str = typer.Option("", "--date", "-d", help="日期 YYYY-MM-DD，默认今天"),
    note: str = typer.Option("", "--note", help="备注/口径"),
):
    """人工录入指标（用于未自动化的指标：装机、排产、招标、分部毛利率等）。"""
    reg = _reg()
    store.init_db()
    try:
        ind = reg.get(indicator_id)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    day = obs_date or date.today().isoformat()
    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        store.record_obs(
            conn,
            indicator_id,
            day,
            value,
            unit=ind.unit,
            source="manual",
            confidence="manual",
            note=note,
        )
        conn.commit()
    console.print(f"[green]已录入[/green] {ind.name} = {value} {ind.unit} @ {day}")


@pv_app.command("compare")
def compare_cmd(
    anchor: list[str] = typer.Option(None, "--anchor", "-a", help="默认全部锚定标的"),
    days: int = typer.Option(180, "--days", "-d"),
):
    """同业横比：把各锚定标的的关键指标并排（公司+同业分组）。"""
    reg = _reg()
    store.init_db()
    targets = list(anchor) if anchor else reg.anchor_ids()
    with get_db(store.DB_NAME) as conn:
        store.ensure_schema(conn)
        latest = store.latest_map(conn)
        console.print(Panel.fit("[bold cyan]同业横比[/bold cyan]  " + " / ".join(reg.anchor_label(a) for a in targets)))
        table = Table()
        table.add_column("指标", style="cyan")
        for a in targets:
            table.add_column(reg.anchor_label(a), justify="right")

        rendered = 0
        for label, suffix in COMPARE_METRICS:
            cells: list[str] = []
            for a in targets:
                iid = _pick_compare_indicator(reg, a, suffix)
                row = latest.get(iid) if iid else None
                if not row or row["value"] is None:
                    cells.append("—")
                    continue
                unit = (reg.indicators[iid].unit if iid in reg.indicators else "") or ""
                cells.append(f"{_fmt(row['value'])} {unit}\n[dim]{row['obs_date']}[/dim]")
            if all(cell == "—" for cell in cells):
                continue  # 全空的行不展示
            table.add_row(label, *cells)
            rendered += 1
        if rendered == 0:
            console.print("[yellow]没有可比的指标，先用 nous pv seed / set 入库[/yellow]")
            return
        console.print(table)
        console.print(
            "\n  [dim]说明: 每个单元格上方为最新值+单位，下方为观测日；"
            "口径不同（A股/H股、半年度/季度）时以观测日为准；"
            "缺数据用 nous pv set <id> <值> 补齐[/dim]"
        )


@pv_app.command("backtest")
def backtest_cmd(
    signal_id: str = typer.Option("", "--id", help="只回测某条信号"),
    horizons: str = typer.Option("20,60,120", "--horizons", help="前瞻交易日，逗号分隔"),
    cooldown: int = typer.Option(bt.DEFAULT_COOLDOWN, "--cooldown", help="触发后冷却交易日"),
    price: str = typer.Option(bt.DEFAULT_PRICE_INDICATOR, "--price", help="用作收益基准的价格指标"),
    bootstrap: bool = typer.Option(False, "--bootstrap", help="先灌入历史收盘价(akshare)"),
    start: str = typer.Option("2016-01-01", "--start", help="bootstrap 起始日期"),
):
    """信号回测：历史复现信号 → 前瞻收益/胜率 vs 基线。"""
    reg = _reg()
    store.init_db()
    try:
        hs = tuple(int(x) for x in horizons.replace(" ", "").split(",") if x)
    except ValueError:
        console.print(f"[red]horizons 需要形如 20,60,120，收到: {horizons}[/red]")
        raise typer.Exit(1) from None

    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        if bootstrap:
            wrote = bt.bootstrap_prices(conn, reg, start=start)
            console.print(
                "[green]历史价格已灌入[/green] "
                + "，".join(f"{k}:{v}条" for k, v in wrote.items())
            )
        results = bt.run(
            conn,
            reg,
            signal_id=signal_id or None,
            price_indicator=price,
            horizons=hs,
            cooldown_days=cooldown,
        )

    table = Table(title=f"信号回测（价格基准 {price}）")
    table.add_column("信号", style="cyan")
    table.add_column("状态", style="dim")
    table.add_column("前瞻", justify="right")
    table.add_column("触发", justify="right")
    table.add_column("胜率", justify="right")
    table.add_column("平均收益", justify="right")
    table.add_column("基线胜率", justify="right", style="dim")
    table.add_column("基线收益", justify="right", style="dim")
    table.add_column("超额", justify="right")
    for res in results:
        if res.status != "ok" or not res.stats:
            table.add_row(
                f"{res.signal_id}\n[dim]{res.name}[/dim]",
                res.status,
                "—",
                "—",
                "—",
                "—",
                "—",
                "—",
                f"[yellow]{res.note[:40]}[/yellow]",
            )
            continue
        for horizon, stat in sorted(res.stats.items()):
            style = "green" if stat.excess > 0 else "red"
            table.add_row(
                f"{res.signal_id}\n[dim]{res.name}[/dim]" if horizon == min(res.stats) else "",
                res.status,
                f"{horizon}d",
                str(stat.n),
                f"{stat.hit_rate:.0%}",
                f"{stat.avg_return:+.1%}",
                f"{stat.baseline_hit_rate:.0%}",
                f"{stat.baseline_avg_return:+.1%}",
                f"[{style}]{stat.excess:+.1%}[/{style}]",
            )
    console.print(table)
    console.print(
        "\n  [dim]读法: 超额 = 信号触发后的前瞻收益 − 同期全样本基线；"
        "样本少(触发<5)时统计意义有限，先累积历史。数据不足就 --bootstrap 灌价格。[/dim]"
    )


@pv_app.command("weekly")
def weekly_cmd(
    days: int = typer.Option(180, "--days", "-d", help="公告/指标回看天数"),
    source: list[str] = typer.Option(None, "--source", "-s", help="只跑部分数据源"),
    no_fetch: bool = typer.Option(False, "--no-fetch", help="跳过采集"),
    no_seed: bool = typer.Option(False, "--no-seed", help="跳过基线导入"),
    no_digest: bool = typer.Option(False, "--no-digest", help="不生成周报"),
    out: str = typer.Option("", "--out", "-o", help="周报输出路径"),
    model: str = typer.Option("", "--model", help="盈利模型路径"),
    push: bool = typer.Option(
        None, "--push/--no-push", help="推送消息（默认：配置了真实通道就推）"
    ),
    channel: list[str] = typer.Option(None, "--channel", help="指定推送通道，可重复"),
):
    """周度例行：采集 → 基线 → 信号 → 周报（与调度器同一实现）。"""
    outcome = pipeline.run_weekly(
        days=days,
        sources=list(source) if source else None,
        seed_path=None,
        out=out or None,
        model_path=model or None,
        skip_fetch=no_fetch,
        skip_seed=no_seed,
        skip_digest=no_digest,
        push=push,
        push_channels=list(channel) if channel else None,
    )
    table = Table(title="周度例行结果")
    table.add_column("数据源", style="cyan")
    table.add_column("状态")
    table.add_column("条数", justify="right")
    table.add_column("说明", style="dim")
    for f in outcome.fetches:
        style = _STATUS_STYLE.get(f.status, "white")
        table.add_row(f.source, f"[{style}]{f.status}[/{style}]", str(f.items), f.message[:56])
    console.print(table)

    color = {
        "bullish": "green",
        "watch": "yellow",
        "pending": "cyan",
    }.get(outcome.verdict_level, "red")
    console.print(
        Panel.fit(
            f"[bold {color}]{outcome.verdict_level.upper()}[/bold {color}] — {outcome.verdict_text}\n"
            f"基线 {outcome.seed_observations} 观测 / {outcome.seed_events} 事件"
        )
    )
    if outcome.report_path:
        console.print(f"[green]周报已生成[/green] {outcome.report_path}")
    if outcome.notifications:
        table = Table(title="推送")
        table.add_column("通道", style="cyan")
        table.add_column("状态")
        table.add_column("说明", style="dim")
        for note in outcome.notifications:
            style = {"ok": "green", "error": "red"}.get(note.status, "dim")
            table.add_row(note.channel, f"[{style}]{note.status}[/{style}]", note.detail[:60])
        console.print(table)
    if outcome.seed_observations:
        console.print("[dim]提示: 人工指标仍用 nous pv set 补（见周报的数据缺口章节）[/dim]")


@pv_app.command("notify")
def notify_cmd(
    test: bool = typer.Option(False, "--test", help="发一条测试消息"),
    channel: list[str] = typer.Option(None, "--channel", help="临时指定通道，可重复"),
    body: str = typer.Option("", "--body", help="测试消息正文"),
):
    """查看推送通道配置，或发测试消息。

    配置全部走环境变量（密钥不进仓库）:
      NOUS_NOTIFY_CHANNELS=wecom,stdout
      NOUS_PUSH_WECOM_KEY / NOUS_PUSH_WEBHOOK / NOUS_PUSH_BARK_URL / NOUS_PUSH_SC_KEY
    """
    console.print(Panel.fit("[bold cyan]推送通道[/bold cyan]"))
    console.print(notify.describe())
    if not test:
        console.print(
            "\n  [dim]发送测试: nous pv notify --test [--channel wecom]"
            "\n  周度自动推送: 配好通道后 nous pv weekly 会自动推（也可 --no-push 关闭）[/dim]"
        )
        return
    results = notify.send(
        "nous 推送测试",
        body or "收到这条说明通道配置正确。（nous pv notify --test）",
        channels=list(channel) if channel else None,
    )
    table = Table(title="测试发送结果")
    table.add_column("通道", style="cyan")
    table.add_column("状态")
    table.add_column("说明", style="dim")
    for res in results:
        style = {"ok": "green", "error": "red"}.get(res.status, "dim")
        table.add_row(res.channel, f"[{style}]{res.status}[/{style}]", res.detail[:70])
    console.print(table)


@pv_app.command("digest")
def digest_cmd(
    out: str = typer.Option("", "--out", "-o", help="输出路径，默认 wiki/finance/concepts/"),
    stdout: bool = typer.Option(False, "--stdout", help="打印到终端而不写文件"),
    days: int = typer.Option(90, "--days", "-d", help="公告回看天数"),
    model: str = typer.Option("", "--model", help="盈利模型路径"),
):
    """生成周报 Markdown（指标仪表盘 + 信号 + 公告 + 盈利模型 + 数据缺口）。"""
    reg = _reg()
    store.init_db()
    with get_db(store.DB_NAME) as conn:
        store.ensure_schema(conn)
        if stdout:
            console.print(digest_mod.render(conn, reg, days=days, model_path=model or None))
            return
        path = digest_mod.write_report(conn, reg, out=out or None, days=days, model_path=model or None)
    console.print(f"[green]周报已生成[/green] {path}")
    console.print("[dim]提示: 先跑 nous pv fetch 拉最新数据，再生成周报[/dim]")


@pv_app.command("backfill")
def backfill_cmd(
    source: list[str] = typer.Option(None, "--source", "-s", help="install|stats|tender|prices"),
    months: int = typer.Option(18, "--months", help="装机：扫描最近 N 个月的归档页"),
    pages: int = typer.Option(18, "--pages", help="电池产量：统计局翻页数（≈月数）"),
    limit: int = typer.Option(8, "--limit", help="每个 taxonomy term 回填的文章数"),
    sleep_s: float = typer.Option(0.6, "--sleep", help="请求间隔秒（普通站点限速）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只枚举不写库"),
    reset_first: bool = typer.Option(
        False, "--reset", help="先清掉这些来源此前回填的行（source=backfill）"
    ),
):
    """历史回填：遍历归档页批量重放（source=backfill，与增量采集互不覆盖）。

    可回填：招标(1请求/12-13月) / 产业链价格(7请求/12-14月) / 装机(~30请求/18-24月)
            / 电池产量(~24请求/≥18月)。
    不可回填：玻璃2.0mm价格——四家源全在付费墙/JS 后（见 docs/research/pvglass-backfill-sources.md）。
    """
    from nous.research.pvglass import backfill as bf

    reg = _reg()
    store.init_db()
    targets = list(source) if source else ["tender", "prices", "install", "stats"]
    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        results = bf.run(
            conn,
            reg,
            sources=targets,
            sleep=sleep_s,
            months=months,
            pages=pages,
            limit=limit,
            dry_run=dry_run,
            reset_first=reset_first,
        )

    table = Table(title="历史回填" + ("（dry-run）" if dry_run else ""))
    table.add_column("来源", style="cyan")
    table.add_column("状态")
    table.add_column("写入", justify="right")
    table.add_column("请求", justify="right", style="dim")
    table.add_column("说明", style="dim")
    for res in results:
        style = {"ok": "green", "partial": "yellow", "skipped": "dim"}.get(res.status, "red")
        table.add_row(
            res.source,
            f"[{style}]{res.status}[/{style}]",
            str(res.written),
            str(res.requests),
            "；".join(res.notes)[:70],
        )
    console.print(table)
    console.print(
        "\n  [dim]回填写入 source=backfill，与日常增量并存；"
        "回填后跑 nous pv backtest 看真样本结果[/dim]"
    )


@pv_app.command("log")
def log_cmd(limit: int = typer.Option(15, "--limit", "-n")):
    """采集审计日志。"""
    store.init_db()
    with get_db(store.DB_NAME) as conn:
        store.ensure_schema(conn)
        rows = store.last_fetch_log(conn, limit=limit)
    table = Table(title="采集审计")
    table.add_column("时间", style="dim")
    table.add_column("数据源", style="cyan")
    table.add_column("状态")
    table.add_column("条数", justify="right")
    table.add_column("说明", style="dim")
    for r in rows:
        style = _STATUS_STYLE.get(r["status"], "white")
        table.add_row(
            r["ts"], r["source"], f"[{style}]{r['status']}[/{style}]", str(r["items"]),
            (r["message"] or "")[:60],
        )
    console.print(table)


@pv_app.command("seed")
def seed_cmd(
    file: str = typer.Option("", "--file", "-f", help="事实基线文件，默认 config/pvglass_seed.yaml"),
    prune: bool = typer.Option(
        True, "--prune/--no-prune", help="先清空全部 seed 行再写入（seed 文件即该层唯一真相）"
    ),
):
    """导入已知事实基线（带日期与来源），让信号引擎立即可用。

    幂等语义：默认先清空全部 source='seed' 行再写入，即“seed 文件是该层唯一真相”：
    在 YAML 里删掉/修正一条后重跑即可同步；自动采集的行不受影响。
    多文件分片维护时用 --no-prune。
    """
    reg = _reg()
    store.init_db()
    with get_db(store.DB_NAME, write=True) as conn:
        store.ensure_schema(conn)
        n_obs, n_evt, warnings = pipeline.import_seed(conn, reg, file or None, prune=prune)
    for warning in warnings:
        console.print(f"[yellow]{warning}[/yellow]")
    console.print(f"[green]已导入基线[/green] {n_obs} 条观测 + {n_evt} 条事件")
    console.print("[dim]下一步: nous pv signal 看拐点判定；nous pv digest 出周报[/dim]")
