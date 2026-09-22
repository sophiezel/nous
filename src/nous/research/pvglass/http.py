"""采集基础设施 — 统一 UA / 超时 / 重试 / 编码 + 可切换传输层。

设计原则:
    * 所有采集器都用同一个 Fetcher（便于统一限速、日志、容错）
    * 网络失败永不上抛到 CLI：由采集器转成 FetchResult(status='error')
    * 保留原始 url，写库时落 source_url，保证可审计

传输层（实测收益）
------------------
默认 requests；对部分中文行业站用 **curl_cffi 伪浏览器 TLS 指纹**，
因为普通 requests 会被 JS 壳/指纹识别拦截：

    SMM 排产列表页  普通 requests 只抽出 1 个链接  → curl_cffi 抽出 20 个
    隆众首页        普通 requests 不通            → curl_cffi 200 / 155KB

环境变量：
    NOUS_PVGLASS_TLS=auto|off|always   传输策略（默认 auto=仅白名单站点伪装）
    NOUS_PVGLASS_PROXY=http://127.0.0.1:7897   可选代理（curl_cffi 路径生效）
"""

from __future__ import annotations

import html as _html
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_SCRIPT_STYLE = re.compile(r"(?is)<(script|style|noscript)\b.*?</\1>")

#: 需要 TLS 指纹伪装的站点（实测：普通 requests 拿不到内容）
IMPERSONATE_HOSTS: tuple[str, ...] = (
    "smm.cn",
    "oilchem.net",
    "sci99.com",
    "bjx.com.cn",
    "databm.com",
    "mysteel.com",
    "pvmeng.com",
    "energytrend.cn",
    "trendforce.cn",
    "stats.gov.cn",
    "nea.gov.cn",
    "100ppi.com",
)

TLS_ENV = "NOUS_PVGLASS_TLS"  # auto(默认) | off | always
PROXY_ENV = "NOUS_PVGLASS_PROXY"  # 可选：http://127.0.0.1:7897

_curl_session_cache: Any = None


class FetchError(RuntimeError):
    """网络层失败（采集器应捕获并降级）。"""


def should_impersonate(url: str) -> bool:
    """是否对该 URL 使用 curl_cffi 浏览器指纹伪装（按白名单 + 环境变量）。"""
    mode = os.environ.get(TLS_ENV, "auto").strip().lower()
    if mode in ("off", "0", "false", "no"):
        return False
    if mode in ("always", "on", "1", "true", "yes"):
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in IMPERSONATE_HOSTS)


def _curl_session(impersonate: str) -> Any:
    """懒建 curl_cffi 会话；缺库时抛 RuntimeError，由调用方回退到 requests。"""
    global _curl_session_cache
    if _curl_session_cache is None:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:  # pragma: no cover - curl_cffi 已在依赖里
            raise RuntimeError("curl_cffi 未安装，无法做 TLS 伪装") from exc
        session = curl_requests.Session(impersonate=impersonate)
        session.headers.update(
            {"User-Agent": DEFAULT_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        )
        _curl_session_cache = session
    return _curl_session_cache


def _curl_get(
    url: str,
    *,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
    timeout: int,
    impersonate: str,
) -> Any:
    session = _curl_session(impersonate)
    kwargs: dict[str, Any] = {"params": params, "headers": headers, "timeout": timeout}
    proxy = os.environ.get(PROXY_ENV, "").strip()
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return session.get(url, **kwargs)


@dataclass
class Fetcher:
    """带重试 + 可切换传输层（requests / curl_cffi TLS 伪装）的 HTTP 客户端。"""

    timeout: int = 20
    retries: int = 2
    backoff: float = 1.5
    headers: dict[str, str] = field(default_factory=dict)
    session: requests.Session = field(default_factory=requests.Session)
    impersonate: str = "chrome"
    last_transport: str = ""  # 最近一次成功的传输层（诊断/测试用）

    def __post_init__(self) -> None:
        base = {"User-Agent": DEFAULT_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
        base.update(self.headers)
        self.session.headers.update(base)

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        encoding: str | None = None,
    ) -> str:
        """按站点策略决定传输顺序；任一传输成功即返回，全失败才重试整轮。"""
        plan = (
            ("curl_cffi", "requests") if should_impersonate(url) else ("requests", "curl_cffi")
        )
        errors: list[str] = []
        for attempt in range(self.retries + 1):
            errors = []
            for transport in plan:
                try:
                    return self._get_via(
                        transport, url, params=params, headers=headers, encoding=encoding
                    )
                except Exception as exc:  # noqa: BLE001 - 统一转 FetchError
                    errors.append(f"{transport}:{type(exc).__name__}")
            if attempt < self.retries:
                time.sleep(self.backoff * (attempt + 1))
        raise FetchError(f"{url} 拉取失败（{', '.join(errors)}）")

    def _get_via(
        self,
        transport: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        encoding: str | None,
    ) -> str:
        if transport == "curl_cffi":
            resp = _curl_get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout,
                impersonate=self.impersonate,
            )
        else:
            resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code} for {url}")
        if encoding:
            resp.encoding = encoding
        elif not resp.encoding or str(resp.encoding).lower() == "iso-8859-1":
            # curl_cffi 没有 requests 的 apparent_encoding
            resp.encoding = getattr(resp, "apparent_encoding", None) or "utf-8"
        self.last_transport = transport
        return resp.text


def make_fetcher(**kwargs: Any) -> Fetcher:
    return Fetcher(**kwargs)


def html_to_text(raw_html: str) -> str:
    """粗粒度正文抽取：去脚本/样式/标签，保留可读文本（含中文标点）。"""
    text = _SCRIPT_STYLE.sub(" ", raw_html)
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)</(p|div|tr|li|h\d)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def duration_ms(started: datetime) -> int:
    """采集耗时（毫秒），异常时返回 0，不影响主流程。"""
    try:
        return int((datetime.now() - started).total_seconds() * 1000)
    except (TypeError, ValueError, AttributeError):
        return 0


def to_float(token: Any) -> float | None:
    """'1,176.50' / '9.5元' / 数值 / '--' → float | None。

    同时接受 int/float（SQLite/akshare 直出的数值），避免调用方踩 `.replace` 陷阱；
    NaN 与无效值统一返回 None。
    """
    if token is None:
        return None
    if isinstance(token, (int, float)):
        try:
            number = float(token)
        except (TypeError, ValueError):
            return None
        return None if number != number else number  # NaN → None
    cleaned = str(token).replace(",", "").replace("，", "").strip()
    if cleaned in {"--", "-", "", "N/A"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None
