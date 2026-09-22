"""采集传输层测试 — TLS 伪装策略 / 传输回退 / 编码边界（全部离线）。"""

from __future__ import annotations

import pytest

from nous.research.pvglass import http as pv_http


class _Resp:
    def __init__(self, status: int = 200, text: str = "ok", encoding: str | None = "utf-8"):
        self.status_code = status
        self.text = text
        self.encoding = encoding


# ── 站点策略 ───────────────────────────────────────────────────────────
def test_should_impersonate_whitelist(monkeypatch):
    monkeypatch.delenv(pv_http.TLS_ENV, raising=False)
    # 白名单站点命中
    assert pv_http.should_impersonate("https://hq.smm.cn/photovoltaic/list/12896")
    assert pv_http.should_impersonate("https://list1.mysteel.com/zhishi/gfbljg.html")
    assert pv_http.should_impersonate("https://www.stats.gov.cn/sj/zxfbhjd/")
    # 子域也算命中
    assert pv_http.should_impersonate("https://dc.oilchem.net/page/")
    # 非白名单
    assert not pv_http.should_impersonate("https://www1.hkexnews.hk/search/x")
    assert not pv_http.should_impersonate("https://api.github.com/repos")
    # 不能误匹配后缀（notsmm.cn 不是 smm.cn）
    assert not pv_http.should_impersonate("https://notsmm.cn/x")


def test_should_impersonate_env_modes(monkeypatch):
    monkeypatch.setenv(pv_http.TLS_ENV, "off")
    assert not pv_http.should_impersonate("https://hq.smm.cn/x")
    monkeypatch.setenv(pv_http.TLS_ENV, "always")
    assert pv_http.should_impersonate("https://www1.hkexnews.hk/x")
    monkeypatch.setenv(pv_http.TLS_ENV, "auto")
    assert not pv_http.should_impersonate("https://www1.hkexnews.hk/x")


# ── 传输顺序与回退 ─────────────────────────────────────────────────────
def _fetcher() -> pv_http.Fetcher:
    return pv_http.Fetcher(retries=0, backoff=0)


def test_curl_cffi_first_for_whitelisted_host(monkeypatch):
    f = _fetcher()
    seen: list[str] = []

    def fake_curl(url, **kwargs):
        seen.append("curl_cffi")
        return _Resp(text="via-curl")

    def fake_requests(*args, **kwargs):
        seen.append("requests")
        return _Resp(text="via-requests")

    monkeypatch.setattr(pv_http, "_curl_get", fake_curl)
    monkeypatch.setattr(f.session, "get", fake_requests)

    assert f.get("https://hq.smm.cn/photovoltaic/list/12896") == "via-curl"
    assert seen == ["curl_cffi"]  # 命中白名单就不必再试 requests
    assert f.last_transport == "curl_cffi"


def test_requests_first_for_other_hosts(monkeypatch):
    f = _fetcher()
    seen: list[str] = []
    monkeypatch.setattr(
        pv_http,
        "_curl_get",
        lambda url, **kw: (seen.append("curl_cffi"), _Resp(text="via-curl"))[1],
    )
    monkeypatch.setattr(
        f.session,
        "get",
        lambda *a, **kw: (seen.append("requests"), _Resp(text="via-requests"))[1],
    )
    assert f.get("https://www1.hkexnews.hk/search/x") == "via-requests"
    assert seen == ["requests"]
    assert f.last_transport == "requests"


def test_fallback_to_requests_when_curl_unavailable(monkeypatch):
    """缺 curl_cffi / TLS 握手失败时，白名单站点必须仍能走 requests。"""
    f = _fetcher()
    seen: list[str] = []

    def boom(url, **kwargs):
        seen.append("curl_cffi")
        raise RuntimeError("curl_cffi 未安装")

    monkeypatch.setattr(pv_http, "_curl_get", boom)
    monkeypatch.setattr(
        f.session,
        "get",
        lambda *a, **kw: (seen.append("requests"), _Resp(text="via-requests"))[1],
    )
    assert f.get("https://hq.smm.cn/x") == "via-requests"
    assert seen == ["curl_cffi", "requests"]
    assert f.last_transport == "requests"


def test_fallback_to_curl_when_requests_fails(monkeypatch):
    """非白名单站点 requests 失败时，用 curl_cffi 兜底。"""
    f = _fetcher()
    seen: list[str] = []

    def bad_requests(*args, **kwargs):
        seen.append("requests")
        raise ConnectionError("proxy error")

    monkeypatch.setattr(f.session, "get", bad_requests)
    monkeypatch.setattr(
        pv_http,
        "_curl_get",
        lambda url, **kw: (seen.append("curl_cffi"), _Resp(text="via-curl"))[1],
    )
    assert f.get("https://example.com/x") == "via-curl"
    assert seen == ["requests", "curl_cffi"]
    assert f.last_transport == "curl_cffi"


def test_http_error_reports_both_transports(monkeypatch):
    f = _fetcher()
    monkeypatch.setattr(pv_http, "_curl_get", lambda url, **kw: _Resp(status=500))
    monkeypatch.setattr(f.session, "get", lambda *a, **kw: _Resp(status=500))
    with pytest.raises(pv_http.FetchError) as exc:
        f.get("https://hq.smm.cn/x")
    message = str(exc.value)
    assert "curl_cffi:FetchError" in message
    assert "requests:FetchError" in message


# ── 编码边界 ───────────────────────────────────────────────────────────
def test_encoding_defaults_to_utf8_without_apparent_encoding(monkeypatch):
    """curl_cffi 没有 requests 的 apparent_encoding，空编码要兜到 utf-8。"""
    f = _fetcher()
    resp = _Resp(encoding="")
    monkeypatch.setattr(pv_http, "_curl_get", lambda url, **kw: resp)
    f.get("https://hq.smm.cn/x")
    assert resp.encoding == "utf-8"


def test_iso_8859_1_is_treated_as_unknown(monkeypatch):
    f = _fetcher()
    resp = _Resp(encoding="ISO-8859-1")
    monkeypatch.setattr(pv_http, "_curl_get", lambda url, **kw: resp)
    f.get("https://hq.smm.cn/x")
    assert resp.encoding == "utf-8"


def test_explicit_encoding_wins(monkeypatch):
    f = _fetcher()
    resp = _Resp(encoding="utf-8")
    monkeypatch.setattr(pv_http, "_curl_get", lambda url, **kw: resp)
    f.get("https://money.163.com/x", encoding="gbk")
    assert resp.encoding == "gbk"


def test_proxy_env_is_passed_to_curl(monkeypatch):
    """NOUS_PVGLASS_PROXY 生效（给需要走 Clash 的站点用）。"""
    captured: dict = {}

    class _Session:
        headers = {}

        def get(self, url, **kwargs):
            captured.update(kwargs)
            return _Resp()

    monkeypatch.setattr(pv_http, "_curl_session_cache", _Session())
    monkeypatch.setenv(pv_http.PROXY_ENV, "http://127.0.0.1:7897")
    pv_http._curl_get(
        "https://hq.smm.cn/x", params=None, headers=None, timeout=5, impersonate="chrome"
    )
    assert captured["proxies"] == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }


def test_no_proxy_when_env_empty(monkeypatch):
    captured: dict = {}

    class _Session:
        headers = {}

        def get(self, url, **kwargs):
            captured.update(kwargs)
            return _Resp()

    monkeypatch.setattr(pv_http, "_curl_session_cache", _Session())
    monkeypatch.delenv(pv_http.PROXY_ENV, raising=False)
    pv_http._curl_get(
        "https://hq.smm.cn/x", params=None, headers=None, timeout=5, impersonate="chrome"
    )
    assert "proxies" not in captured
