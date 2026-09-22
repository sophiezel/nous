"""行情与期货采集器（akshare）— 信义/福莱特股价 + 纯碱/玻璃期货。

拆成两个 source id:
    akshare_futures — SA0(纯碱) / FG0(浮法玻璃) 连续合约收盘
    akshare_quote   — 00968.HK / 601865.SH 收盘

注意: 东方财富系接口偶发 ProxyError，因此只使用新浪/交易所直连接口，
任何单项失败都降级为 partial，不影响其他指标。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any, Callable

from nous.research.pvglass import store
from nous.research.pvglass.http import duration_ms
from nous.research.pvglass.registry import Registry

FUTURES_SOURCE = "akshare_futures"
QUOTE_SOURCE = "akshare_quote"

#: 指标 id → (akshare 调用, 参数, 取哪个字段)
FUTURES: dict[str, tuple[str, dict[str, Any]]] = {
    "soda_ash_futures": ("SA0", {}),
    "float_glass_futures": ("FG0", {}),
}
QUOTES: dict[str, tuple[str, dict[str, Any]]] = {
    "xinyi_price": ("00968", {"market": "hk"}),
    "flat_glass_price": ("601865", {"market": "a"}),
    "flat_glass_price_h": ("06865", {"market": "hk"}),
    "xenergy_price": ("03868", {"market": "hk"}),
}


def _num(value: Any) -> float | None:
    """宽松转 float（akshare 各接口字段类型不统一，也可能返回 NaN）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _last_close(*, symbol: str, market: str) -> tuple[float, str] | None:
    """返回 (收盘价, 交易日)。"""
    import akshare as ak

    if market == "hk":
        df = ak.stock_hk_daily(symbol=symbol, adjust="")
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        close = _num(row["close"])
        return (close, str(row["date"])[:10]) if close is not None else None
    if market == "futures":
        df = ak.futures_zh_daily_sina(symbol=symbol)
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        close = _num(row["close"])
        return (close, str(row["date"])[:10]) if close is not None else None
    # A 股：优先新浪（东财系接口实测偶发 ProxyError），失败再退东财
    sh_symbol = symbol if symbol.startswith(("sh", "sz")) else f"sh{symbol}"
    try:
        df = ak.stock_zh_a_daily(symbol=sh_symbol, adjust="")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            close = _num(row["close"])
            return (close, str(row["date"])[:10]) if close is not None else None
    except Exception:  # noqa: BLE001 - 新浪不可用则退东财
        pass
    end = date.today().strftime("%Y%m%d")
    start = (date.today().replace(day=1)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily", start_date=start, end_date=end, adjust=""
    )
    if df is None or df.empty:
        return None
    row = df.iloc[-1]
    close = _num(row["收盘"])
    return (close, str(row["日期"])[:10]) if close is not None else None


def _run(
    conn: sqlite3.Connection,
    registry: Registry,
    source: str,
    targets: dict[str, tuple[str, dict[str, Any]]],
    market_of: Callable[[dict[str, Any]], str],
    started: datetime,
) -> store.FetchResult:
    ok, failed = 0, []
    for indicator_id, (symbol, extra) in targets.items():
        try:
            result = _last_close(symbol=symbol, market=market_of(extra))
        except Exception as exc:  # noqa: BLE001 - akshare 异常类型不稳定
            failed.append(f"{indicator_id}:{type(exc).__name__}")
            continue
        if not result:
            failed.append(f"{indicator_id}:empty")
            continue
        value, obs_date = result
        unit = registry.indicators[indicator_id].unit if indicator_id in registry.indicators else ""
        store.record_obs(
            conn,
            indicator_id,
            obs_date,
            value,
            unit=unit,
            source=source,
            note=f"akshare 收盘 {value}",
        )
        ok += 1
    conn.commit()
    status = "ok" if ok and not failed else ("partial" if ok else "error")
    message = f"成功{ok}项" + (f"，失败: {', '.join(failed)}" if failed else "")
    return store.FetchResult(source, status, ok, message, duration_ms(started))


def collect_futures(conn: sqlite3.Connection, registry: Registry, **_: Any) -> store.FetchResult:
    started = datetime.now()
    return _run(conn, registry, FUTURES_SOURCE, FUTURES, lambda _: "futures", started)


def collect_quotes(conn: sqlite3.Connection, registry: Registry, **_: Any) -> store.FetchResult:
    started = datetime.now()
    return _run(
        conn, registry, QUOTE_SOURCE, QUOTES, lambda extra: extra.get("market", "a"), started
    )
