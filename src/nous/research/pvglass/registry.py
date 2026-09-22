"""指标字典 — 加载/校验 config/pvglass_indicators.yaml。

指标字典是整套跟踪体系的单一事实来源：采集器按 ``source`` 分发，
信号引擎按 ``id`` 取数，周报按 ``group`` 排版，前端/看板按 ``bias`` 判读。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from nous.core.paths import repo_root

CONFIG_ENV = "NOUS_PVGLASS_CONFIG"
DEFAULT_FILENAME = "pvglass_indicators.yaml"

#: 分组展示顺序（周报/看板按此排列）
GROUP_ORDER: tuple[str, ...] = (
    "price",
    "supply",
    "cost_upstream",
    "demand",
    "company",
    "peers",
    "valuation",
    "policy",
)

GROUP_ORDER_FALLBACK = ("其他",)

#: 支持的信号算子
OPS = (
    "gte",
    "lte",
    "gt",
    "lt",
    "pct_chg_gte",
    "pct_chg_lte",
    "mom_streak_gte",
    "mom_streak_lte",
)


class RegistryError(ValueError):
    """指标字典结构非法。"""


@dataclass(frozen=True)
class SourceSpec:
    id: str
    name: str
    kind: str  # auto | seed | manual
    desc: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnchorSpec:
    """锚定标的（一家公司）。同一套字典里可并存多个锚定标的。"""

    id: str
    name: str
    code: str = ""
    market: str = ""
    price_indicator: str = ""
    model: str = ""
    note: str = ""


#: 未声明 anchors 时的兼容默认
DEFAULT_ANCHOR = "xinyi"


@dataclass(frozen=True)
class Indicator:
    id: str
    name: str
    group: str
    unit: str = ""
    freq: str = ""
    auto: str = "manual"  # auto | seed | manual
    source: str = "manual"
    bias: str = "neutral"  # up_bullish | up_bearish | neutral
    bull: float | None = None
    bear: float | None = None
    note: str = ""
    anchor: str = DEFAULT_ANCHOR  # 行业级指标也归到主锚定标的，便于统一渲染


@dataclass(frozen=True)
class Condition:
    indicator: str
    op: str
    value: float
    window_days: int = 30
    note: str = ""


@dataclass(frozen=True)
class Signal:
    id: str
    name: str
    thesis: str
    logic: str  # all | any
    conditions: tuple[Condition, ...]


@dataclass
class Registry:
    meta: dict[str, Any]
    sources: dict[str, SourceSpec]
    groups: dict[str, str]
    indicators: dict[str, Indicator]
    signals: tuple[Signal, ...]
    path: Path
    anchors: dict[str, AnchorSpec] = field(default_factory=dict)

    # ── 查询辅助 ───────────────────────────────────────────────────
    def get(self, indicator_id: str) -> Indicator:
        try:
            return self.indicators[indicator_id]
        except KeyError:
            raise KeyError(
                f"未知指标 {indicator_id!r}；可用: {', '.join(sorted(self.indicators))}"
            ) from None

    def group_label(self, group_id: str) -> str:
        return self.groups.get(group_id, group_id)

    def ordered_groups(self) -> list[str]:
        known = [g for g in GROUP_ORDER if g in set(i.group for i in self.indicators.values())]
        rest = sorted(
            {i.group for i in self.indicators.values()} - set(known) - set(self.groups)
        )
        return known + rest

    def by_group(self, group: str) -> list[Indicator]:
        return [i for i in self.indicators.values() if i.group == group]

    def by_source(self, source: str) -> list[Indicator]:
        return [i for i in self.indicators.values() if i.source == source]

    def by_anchor(self, anchor: str) -> list[Indicator]:
        return [i for i in self.indicators.values() if i.anchor == anchor]

    def anchor_label(self, anchor: str) -> str:
        spec = self.anchors.get(anchor)
        if spec:
            return f"{spec.name}({spec.code})" if spec.code else spec.name
        return anchor

    def anchor_ids(self) -> list[str]:
        declared = list(self.anchors)
        used = [i.anchor for i in self.indicators.values()]
        extra = [a for a in dict.fromkeys(used) if a not in declared]
        return declared + extra

    def by_auto(self, kind: str) -> list[Indicator]:
        return [i for i in self.indicators.values() if i.auto == kind]

    def stats(self) -> dict[str, int]:
        return {
            "total": len(self.indicators),
            "auto": len(self.by_auto("auto")),
            "seed": len(self.by_auto("seed")),
            "manual": len(self.by_auto("manual")),
            "signals": len(self.signals),
        }


def default_config_path() -> Path:
    """配置文件位置：环境变量 > repo/config > CWD/config。"""
    override = os.environ.get(CONFIG_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    candidates = [
        repo_root() / "config" / DEFAULT_FILENAME,
        Path.cwd() / "config" / DEFAULT_FILENAME,
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _require(raw: dict[str, Any], key: str, where: str) -> Any:
    if key not in raw:
        raise RegistryError(f"{where} 缺少必填字段 {key!r}")
    return raw[key]


def _opt_float(raw: dict[str, Any], key: str) -> float | None:
    value = raw.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"字段 {key!r} 需要数字，得到 {value!r}") from exc


def _num(value: Any, where: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"{where} 需要数字，得到 {value!r}") from exc


def _int(value: Any, where: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"{where} 需要整数，得到 {value!r}") from exc


def load_registry(path: str | Path | None = None) -> Registry:
    """读取并校验指标字典。"""
    cfg_path = Path(path).expanduser() if path else default_config_path()
    if not cfg_path.exists():
        raise RegistryError(f"指标字典不存在: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    sources: dict[str, SourceSpec] = {}
    for sid, spec in (doc.get("sources") or {}).items():
        spec = spec or {}
        sources[sid] = SourceSpec(
            id=sid,
            name=spec.get("name", sid),
            kind=spec.get("kind", "manual"),
            desc=spec.get("desc", ""),
            raw=spec,
        )

    anchors: dict[str, AnchorSpec] = {}
    for aid, spec in (doc.get("anchors") or {}).items():
        spec = spec or {}
        anchors[aid] = AnchorSpec(
            id=aid,
            name=spec.get("name", aid),
            code=spec.get("code", ""),
            market=spec.get("market", ""),
            price_indicator=spec.get("price_indicator", ""),
            model=spec.get("model", ""),
            note=spec.get("note", ""),
        )

    indicators: dict[str, Indicator] = {}
    for raw in doc.get("indicators") or []:
        iid = str(_require(raw, "id", "indicator"))
        if iid in indicators:
            raise RegistryError(f"指标 id 重复: {iid}")
        ind = Indicator(
            id=iid,
            name=raw.get("name", iid),
            group=str(raw.get("group", "其他")),
            unit=raw.get("unit", ""),
            freq=raw.get("freq", ""),
            auto=raw.get("auto", "manual"),
            source=raw.get("source", "manual"),
            bias=raw.get("bias", "neutral"),
            bull=_opt_float(raw, "bull"),
            bear=_opt_float(raw, "bear"),
            note=raw.get("note", ""),
            anchor=str(raw.get("anchor", DEFAULT_ANCHOR)),
        )
        if ind.source not in sources:
            raise RegistryError(f"指标 {iid} 引用了未声明的 source: {ind.source}")
        if anchors and ind.anchor not in anchors:
            raise RegistryError(f"指标 {iid} 引用了未声明的 anchor: {ind.anchor}")
        if ind.auto not in ("auto", "seed", "manual"):
            raise RegistryError(f"指标 {iid} 的 auto 非法: {ind.auto}")
        indicators[iid] = ind

    signals: list[Signal] = []
    for raw in doc.get("signals") or []:
        sid = str(_require(raw, "id", "signal"))
        conds: list[Condition] = []
        for c in raw.get("conditions") or []:
            op = c.get("op", "")
            if op not in OPS:
                raise RegistryError(f"信号 {sid} 使用了不支持的算子: {op!r} (可用: {', '.join(OPS)})")
            target = c.get("indicator", "")
            if target not in indicators:
                raise RegistryError(f"信号 {sid} 引用了未定义指标: {target}")
            conds.append(
                Condition(
                    indicator=target,
                    op=op,
                    value=_num(c.get("value", 0), f"信号 {sid} 的 value"),
                    window_days=_int(c.get("window_days", 30), f"信号 {sid} 的 window_days"),
                    note=c.get("note", ""),
                )
            )
        if not conds:
            raise RegistryError(f"信号 {sid} 没有条件")
        signals.append(
            Signal(
                id=sid,
                name=raw.get("name", sid),
                thesis=raw.get("thesis", ""),
                logic=raw.get("logic", "all"),
                conditions=tuple(conds),
            )
        )

    return Registry(
        meta=doc.get("meta") or {},
        sources=sources,
        groups=doc.get("groups") or {},
        indicators=indicators,
        signals=tuple(signals),
        path=cfg_path,
        anchors=anchors,
    )
