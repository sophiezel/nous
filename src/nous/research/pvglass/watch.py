"""观察清单跃迁告警 — 只在"状态变了"时推送。

与 ``signals.py`` 的分工：

    signals.py  求值：现在是几比几、什么状态（**无记忆**）
    watch.py    比较：和上次比**变了什么**；变了才值得打扰人

为什么需要它：``nous pv weekly`` 是每周无条件推一份全量报告，
"库存进 40 天以内"这类事件如果发生在周三，要等到下周一才被看见；
反过来，若无脑改成每日推送，就会变成每天一份没人看的报告。

被观察的东西（三档，全部复用 signals 的求值，不另建一套判读）：

1. **信号状态**（``signal:<id>``）：triggered / pending / not_triggered / insufficient_data
2. **信号内部的每个条件**（``cond:<signal>@<indicator>``）——
   即使信号整体没触发，条件翻面也值得知道：这正是"库存进 40 天但价格还没站稳"
   这种"差一步"的时刻。
3. **独立的观察项**（``watch:<id>``，字典 ``watch:`` 段）——
   不进 verdict、只做提醒的阈值，如"在产日熔量降到 6.5 万吨"。

**首次运行只建基线、不推送**：否则装完当天就会把全部历史状态
（"S1 未触发""库存 44.7"）当成"刚发生的变化"推出去。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from nous.research.pvglass import signals as sig_engine
from nous.research.pvglass.registry import Registry

#: 跃迁严重度。critical = 该立刻看（有信号刚确认 / 判读转入多头）；
#: info = 值得知道（条件翻面、数据补齐、判读降级）。
CRITICAL = "critical"
INFO = "info"

_UNKNOWN = "unknown"  # 库里没有上次状态时的占位（首次运行）


@dataclass
class WatchItem:
    """一个被观察对象及其当前状态。"""

    key: str
    kind: str  # signal | condition | watch
    name: str
    status: str
    detail: str = ""


@dataclass
class WatchChange:
    item: WatchItem
    before: str
    after: str
    severity: str

    def render(self) -> str:
        icon = "🔴" if self.severity == CRITICAL else "🔵"
        return f"{icon} {self.item.name}: {self.before} → **{self.after}**  ({self.item.detail})"


@dataclass
class WatchOutcome:
    ran_at: str
    items: list[WatchItem] = field(default_factory=list)
    changes: list[WatchChange] = field(default_factory=list)
    verdict_level: str = ""
    verdict_text: str = ""
    is_baseline_run: bool = False
    persisted: int = 0

    @property
    def alerted(self) -> bool:
        return bool(self.changes)

    @property
    def critical_count(self) -> int:
        return sum(1 for c in self.changes if c.severity == CRITICAL)

    def push_title(self) -> str:
        if self.is_baseline_run:
            return "光伏玻璃观察清单 — 已建基线"
        if not self.changes:
            return "光伏玻璃观察清单 — 无变化"
        head = "🔴" if self.critical_count else "🔵"
        return f"{head} 光伏玻璃观察清单 — {len(self.changes)} 项跃迁"

    def push_body(self) -> str:
        lines: list[str] = []
        if self.is_baseline_run:
            lines.append(
                f"首次运行，已记录 {len(self.items)} 项状态作基线（**本次不告警**）。"
            )
            lines.append("下次起只在状态变化时推送。")
            lines.append("")
        elif self.changes:
            lines.append(f"**{len(self.changes)} 项状态跃迁**"
                         + (f"（{self.critical_count} 项 critical）" if self.critical_count else ""))
            lines.append("")
            for c in sorted(self.changes, key=lambda x: x.severity != CRITICAL):
                lines.append(c.render())
            lines.append("")
        else:
            lines.append("本轮无状态跃迁。")
            lines.append("")
        lines.append(f"判读: **{self.verdict_level.upper()}** — {self.verdict_text}")
        return "\n".join(lines)


def _with_note(detail: str, note: str) -> str:
    """把条件的 note 拼进 detail（口径警告等必须随告警一起到达）。"""
    if not note:
        return detail
    return f"{detail}；{note}"


def build_items(signal_results: list[sig_engine.SignalResult],
                watch_results: list[sig_engine.SignalResult]) -> list[WatchItem]:
    """把两组求值结果压成待比较的观察项列表。

    两组都必须是**求值后**的 SignalResult（watch 组同样要过 evaluate_signal），
    否则拿到的是 Condition 定义而非结果。
    """
    items: list[WatchItem] = []
    for res in signal_results:
        sid = res.signal.id
        items.append(
            WatchItem(
                key=f"signal:{sid}",
                kind="signal",
                name=f"{sid} {res.signal.name}",
                status=res.status,
                detail=f"{res.passed}/{len(res.conditions)} 条件满足",
            )
        )
        for cond in res.conditions:
            c = cond.condition
            items.append(
                WatchItem(
                    key=f"cond:{sid}@{c.indicator}:{c.op}:{c.value:g}",
                    kind="condition",
                    name=f"{sid} · {cond.indicator_name}",
                    status=cond.status,
                    # note 跟着告警一起走：口径警告不能丢在半路
                    detail=_with_note(cond.detail or cond.indicator_name, c.note),
                )
            )
    for res in watch_results:
        for cond in res.conditions:
            c = cond.condition
            items.append(
                WatchItem(
                    key=f"watch:{res.signal.id}@{c.indicator}:{c.op}:{c.value:g}",
                    kind="watch",
                    name=f"{res.signal.name}",
                    status=cond.status,
                    detail=_with_note(cond.detail or res.signal.thesis, c.note),
                )
            )
    return items


def _severity(item: WatchItem, before: str, after: str, verdict_after: str) -> str:
    """跃迁严重度。规则写死并测试固化，避免"永远 critical"导致告警疲劳。"""
    if item.kind == "signal" and after == sig_engine.TRIGGERED:
        return CRITICAL
    if item.kind == "condition" and before == sig_engine.NO_DATA and after == sig_engine.PASS:
        return CRITICAL  # 数据补齐后条件直接成立：最容易被漏掉的一类
    if item.kind == "watch" and after == sig_engine.PASS:
        return CRITICAL
    if verdict_after == "bullish" and item.kind == "verdict":
        return CRITICAL
    return INFO


def _load_state(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["key"]: row["status"]
        for row in conn.execute("SELECT key, status FROM watch_state")
    }


def _save_state(conn: sqlite3.Connection, items: list[WatchItem], now: str,
                alerted_keys: set[str]) -> int:
    existing = {
        row["key"]: row
        for row in conn.execute("SELECT key, status, changed_at FROM watch_state")
    }
    # 写前先清孤儿：字典里删掉的信号/条件/观察项不该在表里留着。
    # 留着不会误报（比较只遍历当前 items），但表会越攒越乱，
    # 且残留的旧阈值会让人误以为它还在生效。
    current_keys = {item.key for item in items}
    stale = [key for key in existing if key not in current_keys]
    if stale:
        conn.executemany("DELETE FROM watch_state WHERE key = ?", [(k,) for k in stale])
    n = 0
    for item in items:
        prev = existing.get(item.key)
        if prev is None:
            conn.execute(
                """INSERT INTO watch_state
                   (key, kind, status, detail, first_seen_at, changed_at, alerted_at)
                   VALUES (?,?,?,?,?,?,NULL)""",
                (item.key, item.kind, item.status, item.detail, now, now),
            )
        else:
            changed = prev["changed_at"] if item.status == prev["status"] else now
            conn.execute(
                """UPDATE watch_state
                   SET kind=?, status=?, detail=?, changed_at=?,
                       alerted_at=CASE WHEN ? THEN ? ELSE alerted_at END
                   WHERE key=?""",
                (item.kind, item.status, item.detail, changed, item.key in alerted_keys,
                 now, item.key),
            )
        n += 1
    conn.commit()
    return n


def check(
    conn: sqlite3.Connection,
    registry: Registry,
    *,
    as_of: str | None = None,
    persist: bool = True,
) -> WatchOutcome:
    """求值 → 与上次比较 → （可选）落库。返回本轮跃迁。"""
    now = datetime.now().isoformat(timespec="seconds")
    results = sig_engine.evaluate(conn, registry, as_of=as_of)
    level, text = sig_engine.verdict(results)
    # watch 组也要真求值（它只是不进 verdict，不是不评估）
    watch_results = [
        sig_engine.evaluate_signal(conn, registry, s, as_of=as_of)
        for s in registry.watch
    ]

    items = build_items(results, watch_results)
    if level:
        items.append(WatchItem(key="verdict:level", kind="verdict",
                               name="汇总判读", status=level, detail=text))

    prev = _load_state(conn)
    is_baseline = not prev  # 库里一条状态都没有 → 首次运行

    changes: list[WatchChange] = []
    if not is_baseline:
        for item in items:
            before = prev.get(item.key)
            if before is None or before == item.status:
                continue
            changes.append(
                WatchChange(item=item, before=before, after=item.status,
                            severity=_severity(item, before, item.status, level))
            )

    outcome = WatchOutcome(
        ran_at=now, items=items, changes=changes,
        verdict_level=level, verdict_text=text, is_baseline_run=is_baseline,
    )
    if persist:
        outcome.persisted = _save_state(
            conn, items, now, {c.item.key for c in changes},
        )
    return outcome


def reset(conn: sqlite3.Connection) -> int:
    """清空基线（下次运行会重新建基线、不告警）。"""
    row = conn.execute("SELECT COUNT(*) FROM watch_state").fetchone()
    deleted = row[0] if row else 0
    conn.execute("DELETE FROM watch_state")
    conn.commit()
    return deleted
