"""信号回测 — 用历史观测复现信号，检验其前瞻收益（胜率 / 超额）。

为什么需要它
------------
`nous pv signal` 只回答"现在几条信号确认"，不回答"信号有用吗"。
本模块把每条信号在历史上**逐日复现**（点对点，不做未来信息泄漏），
统计触发后的前瞻收益，并与同期基线（无信号日）对比，给出胜率与超额。

方法
----
1. 复现时点 = 触发指标（signal 的第一个 condition）的全部观测日
2. 对时点 t：只用 obs_date <= t 的数据评估该信号；TRIGGERED 才算入场
3. 冷却期：同一次"信号事件"会连续多日确认，故触发后跳过 cooldown_days 个交易日
4. 前瞻收益 = 价格序列在 t 之后 horizon 个观测日的收益率
5. 基线 = 同期所有交易日的前瞻收益（信号应显著优于它才有价值）

数据要求
--------
* 价格历史：`nous pv backtest --bootstrap` 会从 akshare 灌入信义/福莱特历史收盘价
* 指标历史：seed / 自动采集 / `nous pv set` 累积；数据不足时本模块会明确报"样本不足"
"""

from __future__ import annotations

import statistics
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from nous.research.pvglass import signals as sig_engine
from nous.research.pvglass import store
from nous.research.pvglass.registry import Registry, Signal

DEFAULT_PRICE_INDICATOR = "xinyi_price"
DEFAULT_HORIZONS: tuple[int, ...] = (20, 60, 120)
DEFAULT_COOLDOWN = 20
HIST_SOURCE = "akshare_hist"


def _to_float(value: Any) -> float | None:
    """宽松转 float（脏数据/NaN 返回 None，不抛异常）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


@dataclass
class Trigger:
    """一次信号触发及其前瞻收益（horizon → 收益率）。"""

    obs_date: str
    price: float
    forwards: dict[int, float | None] = field(default_factory=dict)


@dataclass
class HorizonStats:
    horizon: int
    n: int
    hit_rate: float
    avg_return: float
    baseline_n: int
    baseline_hit_rate: float
    baseline_avg_return: float

    @property
    def excess(self) -> float:
        return self.avg_return - self.baseline_avg_return

    @property
    def hit_excess(self) -> float:
        return self.hit_rate - self.baseline_hit_rate


@dataclass
class BacktestResult:
    signal_id: str
    name: str
    status: str  # ok | insufficient_history
    note: str
    triggers: list[Trigger]
    stats: dict[int, HorizonStats]

    @property
    def n_triggers(self) -> int:
        return len(self.triggers)


# ── 价格序列 ───────────────────────────────────────────────────────────
def price_index(
    conn: sqlite3.Connection, indicator_id: str = DEFAULT_PRICE_INDICATOR
) -> list[tuple[str, float]]:
    """[(obs_date, close)] 升序，去掉空值。"""
    out: list[tuple[str, float]] = []
    for row in store.all_obs(conn, indicator_id):
        value = _to_float(row["value"])
        if value is None:
            continue
        out.append((str(row["obs_date"]), value))
    return out


def forward_return(
    prices: list[tuple[str, float]], start_idx: int, horizon: int
) -> float | None:
    """从 start_idx 往后 horizon 个观测日的收益率（不足则 None）。"""
    end_idx = start_idx + horizon
    if start_idx < 0 or end_idx >= len(prices):
        return None
    start_price = prices[start_idx][1]
    if start_price <= 0:
        return None
    return prices[end_idx][1] / start_price - 1.0


def _nearest_index(prices: list[tuple[str, float]], obs_date: str) -> int | None:
    """找到 obs_date 当天或之后最近的一个价格观测（价格与指标日期可能不同步）。"""
    for idx, (day, _) in enumerate(prices):
        if day >= obs_date:
            return idx
    return None


# ── 回放 ───────────────────────────────────────────────────────────────
def replay(
    conn: sqlite3.Connection,
    registry: Registry,
    signal: Signal,
    *,
    price_indicator: str = DEFAULT_PRICE_INDICATOR,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    cooldown_days: int = DEFAULT_COOLDOWN,
) -> BacktestResult:
    prices = price_index(conn, price_indicator)
    horizons = tuple(horizons)
    if len(prices) <= max(horizons):
        return BacktestResult(
            signal.id,
            signal.name,
            "insufficient_history",
            f"价格历史仅 {len(prices)} 个观测，需要 > {max(horizons)}；先跑 --bootstrap",
            [],
            {},
        )

    trigger_indicator = signal.conditions[0].indicator
    # 复现网格 = **所有条件指标**的观测日并集。
    # 只用 conditions[0] 会有个坑：该指标没历史时整条信号就报"样本不足"，
    # 即使其他条件有丰富历史（例：S3 第一条件排产只有 1 个观测，
    # 但装机已有 10 个月序列）。
    dates = sorted(
        {
            day
            for condition in signal.conditions
            for day in store.obs_dates(conn, condition.indicator)
        }
    )
    if len(dates) < 2:
        return BacktestResult(
            signal.id,
            signal.name,
            "insufficient_history",
            f"触发指标 {trigger_indicator} 仅 {len(dates)} 个观测日，无法回放",
            [],
            {},
        )

    triggers: list[Trigger] = []
    price_cursor = 0  # 冷却期用价格索引衡量，避免日期口径混乱
    for obs_date in dates:
        if obs_date < prices[0][0]:
            continue
        idx = _nearest_index(prices, obs_date)
        if idx is None:
            continue
        if idx < price_cursor:
            continue  # 冷却期内
        result = sig_engine.evaluate_signal(conn, registry, signal, as_of=obs_date)
        if result.status != sig_engine.TRIGGERED:
            continue
        price_cursor = idx + max(cooldown_days, 1)
        trigger = Trigger(obs_date=obs_date, price=prices[idx][1])
        for horizon in horizons:
            trigger.forwards[horizon] = forward_return(prices, idx, horizon)
        triggers.append(trigger)

    stats: dict[int, HorizonStats] = {}
    for horizon in horizons:
        sample = [t.forwards.get(horizon) for t in triggers]
        sample = [x for x in sample if x is not None]
        baseline = [
            value
            for i in range(len(prices))
            for value in [forward_return(prices, i, horizon)]
            if value is not None
        ]
        stats[horizon] = HorizonStats(
            horizon=horizon,
            n=len(sample),
            hit_rate=_hit_rate(sample),
            avg_return=statistics.fmean(sample) if sample else 0.0,
            baseline_n=len(baseline),
            baseline_hit_rate=_hit_rate(baseline),
            baseline_avg_return=statistics.fmean(baseline) if baseline else 0.0,
        )

    note = f"触发 {len(triggers)} 次（冷却 {cooldown_days} 日）；网格 {len(dates)} 个时点"
    if not triggers:
        note += "；历史窗口内未触发过"
    return BacktestResult(signal.id, signal.name, "ok", note, triggers, stats)


def _hit_rate(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v > 0) / len(values)


def run(
    conn: sqlite3.Connection,
    registry: Registry,
    *,
    signal_id: str | None = None,
    price_indicator: str = DEFAULT_PRICE_INDICATOR,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    cooldown_days: int = DEFAULT_COOLDOWN,
) -> list[BacktestResult]:
    targets = [s for s in registry.signals if signal_id is None or s.id == signal_id]
    return [
        replay(
            conn,
            registry,
            s,
            price_indicator=price_indicator,
            horizons=horizons,
            cooldown_days=cooldown_days,
        )
        for s in targets
    ]


# ── 价格历史灌入 ───────────────────────────────────────────────────────
#: 指标 id → (代码, 市场)
BOOTSTRAP_SYMBOLS: dict[str, tuple[str, str]] = {
    "xinyi_price": ("00968", "hk"),
    "flat_glass_price": ("601865", "a"),
}


def _fetch_history(symbol: str, market: str, start: str) -> list[tuple[str, float]]:
    """akshare 历史收盘价（新浪口径，避免东财 ProxyError）。"""
    import akshare as ak

    if market == "hk":
        df = ak.stock_hk_daily(symbol=symbol, adjust="")
        rows = [(str(r["date"])[:10], _to_float(r["close"])) for _, r in df.iterrows()]
    else:
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start.replace("-", ""),
            end_date=date.today().strftime("%Y%m%d"),
            adjust="",
        )
        rows = [(str(r["日期"])[:10], _to_float(r["收盘"])) for _, r in df.iterrows()]
    return [(day, close) for day, close in rows if day >= start and close is not None]


def bootstrap_prices(
    conn: sqlite3.Connection,
    registry: Registry,
    *,
    start: str = "2016-01-01",
    symbols: dict[str, tuple[str, str]] | None = None,
) -> dict[str, int]:
    """把历史收盘价灌入 obs（source=akshare_hist，与日常快照互不覆盖）。"""
    written: dict[str, int] = {}
    for indicator_id, (symbol, market) in (symbols or BOOTSTRAP_SYMBOLS).items():
        if indicator_id not in registry.indicators:
            continue
        try:
            rows = _fetch_history(symbol, market, start)
        except Exception:  # noqa: BLE001 - 网络失败不影响其他标的
            written[indicator_id] = 0
            continue
        unit = registry.indicators[indicator_id].unit
        n = 0
        for day, close in rows:
            store.record_obs(
                conn,
                indicator_id,
                day,
                close,
                unit=unit,
                source=HIST_SOURCE,
                confidence="eod",
                note="历史收盘价（回测用）",
            )
            n += 1
        written[indicator_id] = n
    conn.commit()
    return written


def summary_rows(results: list[BacktestResult]) -> list[dict[str, Any]]:
    """拍平成 CLI/报告用的一行一指标。"""
    out: list[dict[str, Any]] = []
    for res in results:
        for horizon, stat in sorted(res.stats.items()):
            out.append(
                {
                    "signal_id": res.signal_id,
                    "name": res.name,
                    "status": res.status,
                    "horizon": horizon,
                    "n": stat.n,
                    "hit_rate": stat.hit_rate,
                    "avg_return": stat.avg_return,
                    "baseline_hit_rate": stat.baseline_hit_rate,
                    "baseline_avg_return": stat.baseline_avg_return,
                    "excess": stat.excess,
                    "hit_excess": stat.hit_excess,
                    "note": res.note,
                }
            )
    return out
