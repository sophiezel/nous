"""周报生成 — 把时序库渲染成可归档的 Markdown（默认写 wiki/finance/concepts/）。"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from nous.core.paths import repo_root
from nous.research.pvglass import store
from nous.research.pvglass.registry import Registry
from nous.research.pvglass.signals import NO_DATA, evaluate, verdict
from nous.research.pvglass.xinyi import load_model, run_all, sensitivity

DEFAULT_OUT = Path("wiki/finance/concepts/光伏玻璃产业链-跟踪.md")

_STATUS_ICON = {
    "triggered": "✅ 确认",
    "not_triggered": "❌ 未触发",
    "pending": "🟡 待确认",
    "insufficient_data": "⚪ 数据不足",
}


def _cell(text: object) -> str:
    """Markdown 表格单元格：竖线会截断列，统一替换。"""
    return str(text).replace("|", "／").replace("\n", " ")


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.{digits}f}"


def _threshold_note(indicator, value: float | None) -> str:
    """按 bias 判读阈值：up_bullish=越高越好；up_bearish=越低越好。"""
    if value is None:
        return ""
    if indicator.bias == "up_bearish":
        if indicator.bull is not None and value <= indicator.bull:
            return f"✅≤{indicator.bull:g}"
        if indicator.bear is not None and value >= indicator.bear:
            return f"🔴≥{indicator.bear:g}"
        return ""
    if indicator.bull is not None and value >= indicator.bull:
        return f"✅≥{indicator.bull:g}"
    if indicator.bear is not None and value <= indicator.bear:
        return f"🔴≤{indicator.bear:g}"
    return ""


def render_dashboard(conn: sqlite3.Connection, registry: Registry) -> str:
    latest = store.latest_map(conn)
    lines: list[str] = ["## 一、核心指标仪表盘", ""]
    for group in registry.ordered_groups():
        items = registry.by_group(group)
        if not items:
            continue
        lines.append(f"### {registry.group_label(group)}")
        lines.append("")
        lines.append("| 指标 | 最新 | 日期 | 前值 | 变化 | 阈值 | 自动化 |")
        lines.append("|---|---|---|---|---|---|---|")
        for ind in items:
            row = latest.get(ind.id)
            if row is None:
                lines.append(f"| {ind.name} | — | — | — | — | — | {ind.auto} |")
                continue
            value = row["value"]
            prev = store.previous(conn, ind.id)
            prev_value = prev["value"] if prev else None
            delta = ""
            if value is not None and prev_value not in (None, 0):
                delta = f"{(value - prev_value) / abs(prev_value) * 100:+.1f}%"
            elif row["value_text"]:
                delta = _cell(row["value_text"])[:28]
            unit = ind.unit or row["unit"] or ""
            lines.append(
                f"| {ind.name} | {_fmt(value)} {unit} | {row['obs_date']} | "
                f"{_fmt(prev_value)} | {delta} | {_threshold_note(ind, value)} | {ind.auto} |"
            )
        lines.append("")
    return "\n".join(lines)


def render_signals(conn: sqlite3.Connection, registry: Registry) -> str:
    results = evaluate(conn, registry)
    level, conclusion = verdict(results)
    lines = [
        "## 二、拐点信号看板",
        "",
        f"> 判读：**{level.upper()}** — {conclusion}",
        "",
        "| 信号 | 状态 | 通过 | 依据 |",
        "|---|---|---|---|",
    ]
    for r in results:
        detail = "；".join(
            f"{c.indicator_name} {c.detail}" if c.status != NO_DATA else f"{c.indicator_name}: 缺数据"
            for c in r.conditions
        )
        lines.append(
            f"| {r.signal.id} {r.signal.name} | {_STATUS_ICON.get(r.status, r.status)} | "
            f"{r.passed}/{len(r.conditions)} | {_cell(detail)} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_announcements(conn: sqlite3.Connection, days: int = 90) -> str:
    """所有跟踪个股的公告（按代码分组）。"""
    from nous.research.pvglass.sources.hkex import STOCKS

    rows = store.recent_announcements(conn, days=days)
    lines = [f"## 三、公司公告（近{days}天）", ""]
    if not rows:
        lines += ["_无记录，先跑 `nous pv fetch --source hkex`_", ""]
        return "\n".join(lines)

    by_code: dict[str, list] = {}
    for row in rows:
        by_code.setdefault(str(row["stock_code"]), []).append(row)
    for code, items in by_code.items():
        label = STOCKS.get(code, "")
        lines.append(f"### {code} {label}（{len(items)} 条）")
        lines.append("")
        lines.append("| 日期 | 分类 | 标题 |")
        lines.append("|---|---|---|")
        for r in items[:20]:
            title = _cell(r["title"])[:60]
            lines.append(f"| {r['ann_date']} | {r['category']} | [{title}]({r['url']}) |")
        lines.append("")
    return "\n".join(lines)


def render_model(model_path: str | Path | None = None) -> str:
    model = load_model(model_path)
    results = run_all(model)
    steps = sensitivity(model, asp_min=9.4, asp_max=11.0, step=0.2)
    lines = [
        "## 四、盈利模型 vs 一致预期",
        "",
        f"_模型基准: {model.get('meta', {}).get('basis', '')}_",
        "",
        "| 情景 | Q4实现价 | 减值 | H2玻璃收入 | H2玻璃毛利率 | H2归母 | **FY2026归母** | EPS(分) | PE |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        pe = "亏损" if r.pe is None else f"{r.pe:.0f}x"
        lines.append(
            f"| {r.name} | {r.asp_q4:.2f} | {_fmt(r.impairment)} | {_fmt(r.h2_glass_revenue)} | "
            f"{r.h2_glass_gm:.1f}% | {_fmt(r.h2_attributable)} | **{_fmt(r.fy_attributable)}** | "
            f"{r.fy_eps_fen:.2f} | {pe} |"
        )
    ref = model.get("consensus_ref") or {}
    lines += [
        "",
        f"> etnet 一致预期（8家）：FY2026 中位数 {_fmt(ref.get('fy2026_median'))}、"
        f"区间 [{_fmt(ref.get('fy2026_min'))}, {_fmt(ref.get('fy2026_max'))}]；"
        f"FY2027 中位数 {_fmt(ref.get('fy2027_median'))}；FY2028 中位数 {_fmt(ref.get('fy2028_median'))}",
        "",
        "**敏感性：Q4 实现均价 → FY2026 归母（百万元）**",
        "",
        "| Q4 实现价(元/㎡) | " + " | ".join(f"{row['asp_q4']:.1f}" for row in steps) + " |",
        "|---" * (1 + len(steps)) + "|",
        "| FY归母 | " + " | ".join(f"{row['fy_attributable']:.0f}" for row in steps) + " |",
        "",
    ]
    return "\n".join(lines)


def render_gaps(conn: sqlite3.Connection, registry: Registry, days: int = 45) -> str:
    rows = conn.execute(
        "SELECT indicator_id, MAX(obs_date) AS last FROM obs GROUP BY indicator_id"
    ).fetchall()
    last_map = {r["indicator_id"]: r["last"] for r in rows}
    missing = []
    for ind in registry.indicators.values():
        last = last_map.get(ind.id)
        if last is None:
            missing.append((ind, "从未采集"))
            continue
        try:
            age = (date.today() - date.fromisoformat(last)).days
        except ValueError:
            continue
        if age > days:
            missing.append((ind, f"已 {age} 天未更新"))
    lines = ["## 五、数据缺口（需人工/补源）", ""]
    if not missing:
        lines += ["_全部指标在窗口内都有数据_", ""]
        return "\n".join(lines)
    lines += ["| 指标 | 分组 | 自动化 | 说明 |", "|---|---|---|---|"]
    for ind, why in missing:
        lines.append(f"| {ind.name} | {registry.group_label(ind.group)} | {ind.auto} | {why} |")
    lines.append("")
    return "\n".join(lines)


def render_fetch_log(conn: sqlite3.Connection, limit: int = 10) -> str:
    rows = store.last_fetch_log(conn, limit=limit)
    lines = ["## 六、采集审计", ""]
    if not rows:
        lines += ["_无采集记录_", ""]
        return "\n".join(lines)
    lines += ["| 时间 | 数据源 | 状态 | 条数 | 说明 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['ts']} | {r['source']} | {r['status']} | {r['items']} | "
            f"{(r['message'] or '')[:70]} |"
        )
    lines.append("")
    return "\n".join(lines)


def render(
    conn: sqlite3.Connection,
    registry: Registry,
    *,
    days: int = 90,
    model_path: str | Path | None = None,
) -> str:
    today = date.today().isoformat()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    stats = registry.stats()
    anchors = " / ".join(
        f"{registry.anchor_label(a)}" for a in registry.anchor_ids()
    )
    header = [
        "# 光伏玻璃产业链 — 跟踪报告",
        "",
        f"> 生成时间: {now} | 报告日期: {today}",
        f"> 锚定标的: {anchors}",
        f"> 指标 {stats['total']} 个（自动 {stats['auto']} / 半自动 {stats['seed']} / 人工 {stats['manual']}）"
        f"｜信号 {stats['signals']} 条",
        "> 数据源: 港交所披露易 / 集邦TrendForce / Mysteel-隆众 / etnet / akshare",
        "> 口径提醒: 玻璃价格默认「2.0mm单镀面板·含税送到」；区分**报价**与**结算价**",
        "",
    ]
    body = [
        render_dashboard(conn, registry),
        render_signals(conn, registry),
        render_announcements(conn, days=min(days, 120)),
        render_model(model_path),
        render_gaps(conn, registry),
        render_fetch_log(conn),
        "---",
        "",
        "> 本报告由 `nous pv digest` 自动生成；人工指标见数据缺口表，用 "
        "`nous pv set <指标id> <数值>` 录入（如国内月度装机、组件排产）。",
        "",
    ]
    return "\n".join(header + body)


def write_report(
    conn: sqlite3.Connection,
    registry: Registry,
    *,
    out: str | Path | None = None,
    days: int = 90,
    model_path: str | Path | None = None,
) -> Path:
    text = render(conn, registry, days=days, model_path=model_path)
    target = Path(out).expanduser() if out else (repo_root() / DEFAULT_OUT)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
