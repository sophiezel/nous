"""采集器注册表 — source id → collect(conn, registry) 。

延迟导入：单个采集器的依赖缺失（如 akshare/解析库）不应影响其他采集器。
"""

from __future__ import annotations

from typing import Any, Callable

from nous.research.pvglass import store
from nous.research.pvglass.registry import Registry

#: source id → 模块路径（延迟导入）
_MODULES: dict[str, str] = {
    "hkex": "nous.research.pvglass.sources.hkex",
    "mysteel": "nous.research.pvglass.sources.mysteel",
    "energytrend": "nous.research.pvglass.sources.energytrend",
    "etnet": "nous.research.pvglass.sources.consensus",
    "akshare_futures": "nous.research.pvglass.sources.market",
    "akshare_quote": "nous.research.pvglass.sources.market",
    "demand": "nous.research.pvglass.sources.demand",
    "report_pdf": "nous.research.pvglass.sources.hkex_pdf",
}

#: source id → 采集函数名（market 模块有两个入口）
_FUNCS: dict[str, str] = {
    "hkex": "collect",
    "mysteel": "collect",
    "energytrend": "collect",
    "etnet": "collect",
    "akshare_futures": "collect_futures",
    "akshare_quote": "collect_quotes",
    "demand": "collect",
    "report_pdf": "collect",
}

#: 默认抓取顺序（快且稳定的先跑；report_pdf 要下 1-3MB PDF，放最后）
DEFAULT_ORDER: tuple[str, ...] = (
    "hkex",
    "etnet",
    "energytrend",
    "demand",
    "akshare_futures",
    "akshare_quote",
    "mysteel",
    "report_pdf",
)


def available() -> list[str]:
    return list(_MODULES)


def _resolve(source: str) -> Callable[..., store.FetchResult] | None:
    module_path = _MODULES.get(source)
    if module_path is None:
        return None
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, _FUNCS[source])


def collect_one(source: str, conn: Any, registry: Registry, **kwargs: Any) -> store.FetchResult:
    """执行单个采集器，任何异常降级为 FetchResult(status='error')。"""
    try:
        fn = _resolve(source)
    except Exception as exc:  # noqa: BLE001
        return store.FetchResult(source, "error", 0, f"采集器导入失败: {type(exc).__name__}: {exc}"[:200])
    if fn is None:
        return store.FetchResult(
            source, "skipped", 0, f"没有采集器实现；可用: {', '.join(available())}"
        )
    try:
        return fn(conn, registry, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 单个数据源失败不应中断整批
        return store.FetchResult(source, "error", 0, f"{type(exc).__name__}: {exc}"[:200])
