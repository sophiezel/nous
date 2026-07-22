#!/usr/bin/env python3
"""弹性数据获取 — 多源逐级降级 + 缓存兜底 + 健康监控

每条降级链: 主源 → 备源 → 缓存 → 告警
反爬分级: Level 0(requests) → Level 1(curl_cffi指纹) → Level 3(代理+指纹)

用法:
  from resilient_fetch import resilient_fetch
  result = resilient_fetch("hk_quote", {"symbol": "00700"})
  result = resilient_fetch("short_sell", {"date": "2026-05-14"})
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests as py_requests

logger = logging.getLogger(__name__)

# ── 路径 ──────────────────────────────────
HEALTH_PATH = Path.home() / "wiki/finance/raw/source-health.json"
CACHE_DIR = Path.home() / "wiki/finance/raw/cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── 公共 UA ────────────────────────────────
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# ── 健康数据（内存缓存，定期刷盘）─────────
_health: dict[str, dict] = {}


def _load_health():
    global _health
    if HEALTH_PATH.exists():
        try:
            _health = json.loads(HEALTH_PATH.read_text()).get("sources", {})
        except Exception:
            _health = {}


def _save_health():
    HEALTH_PATH.write_text(json.dumps({
        "updated": datetime.now().isoformat(),
        "sources": _health,
    }, ensure_ascii=False, indent=2))


def _record(name: str, ok: bool, latency_ms: float = 0, error: str = ""):
    if name not in _health:
        _health[name] = {"ok": True, "latency_ms": 0, "fails": 0, "last_ok": None, "error": ""}
    h = _health[name]
    if ok:
        h["ok"] = True
        h["latency_ms"] = round(latency_ms)
        h["last_ok"] = datetime.now().isoformat()
        h["fails"] = 0
        h["error"] = ""
    else:
        h["ok"] = False
        h["fails"] = h.get("fails", 0) + 1
        h["error"] = error[:200]
    _save_health()


_load_health()

# ════════════════════════════════════════════
# FetchResult
# ════════════════════════════════════════════

class FetchResult:
    def __init__(self, data: Any, source: str, is_stale: bool = False):
        self.data = data
        self.source = source
        self.is_stale = is_stale

    def __repr__(self):
        stale = " [stale]" if self.is_stale else ""
        return f"FetchResult({self.source}{stale})"


# ════════════════════════════════════════════
# Source 定义
# ════════════════════════════════════════════

class Source:
    def __init__(self, name: str, fetch_fn: Callable, level: int = 0, timeout: int = 5, proxy: bool = False):
        self.name = name
        self.fetch_fn = fetch_fn
        self.level = level      # 0=requests, 1=curl_cffi, 3=proxy
        self.timeout = timeout
        self.proxy = proxy      # 是否需要开 Clash


# ════════════════════════════════════════════
# 缓存工具
# ════════════════════════════════════════════

def _cache_key(data_type: str, params: dict) -> str:
    raw = f"{data_type}:{json.dumps(params or {}, sort_keys=True)}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _read_cache(data_type: str, params: dict) -> dict | None:
    path = CACHE_DIR / f"{_cache_key(data_type, params)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        path.unlink(missing_ok=True)
        return None


def _write_cache(data_type: str, params: dict, data: Any):
    path = CACHE_DIR / f"{_cache_key(data_type, params)}.json"
    path.write_text(json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False, default=str))


# ════════════════════════════════════════════
# 代理控制
# ════════════════════════════════════════════

def _clash_set_mode(mode: str):
    """通过 Clash API 切换模式"""
    try:
        import subprocess
        sock = "/tmp/verge/verge-mihomo.sock"
        subprocess.run(
            ["curl", "-s", "--unix-socket", sock,
             "-X", "PATCH", "http://localhost/configs",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"mode": mode})],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


# ════════════════════════════════════════════
# Fetch 实现
# ════════════════════════════════════════════

def _fetch_sina_spot(params: dict) -> list[dict]:
    """Sina A股实时行情 hq.sinajs.cn"""
    codes = params.get("symbols", [])
    sina_codes = ",".join(
        f"sh{c}" if c.startswith(("6", "5")) else f"sz{c}"
        for c in codes
    )
    url = f"http://hq.sinajs.cn/list={sina_codes}"
    r = py_requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=3)
    r.encoding = "gbk"
    results = []
    for line in r.text.strip().split("\n"):
        if "=" not in line:
            continue
        fields = line.split("=", 1)[1].strip('";').split(",")
        if len(fields) < 4 or not fields[0]:
            continue
        results.append({
            "name": fields[0], "open": float(fields[1] or 0),
            "prev_close": float(fields[2] or 0), "price": float(fields[3] or 0),
            "high": float(fields[4] or 0), "low": float(fields[5] or 0),
            "volume": float(fields[8] or 0), "amount": float(fields[9] or 0),
        })
    return results


def _fetch_push2_quote(params: dict) -> list[dict]:
    """push2 备用行情（动态Clash切换: rule→direct→fetch→rule）"""
    try:
        import subprocess, json as _json
        subprocess.run(['curl','-s','--unix-socket','/tmp/verge/verge-mihomo.sock',
            '-X','PATCH','http://localhost/configs',
            '-H','Content-Type: application/json',
            '-d',_json.dumps({'mode':'direct'})], capture_output=True, timeout=5)
        import time; time.sleep(1.5)
    except Exception:
        pass
    try:
        codes = params.get("symbols", [])
        secids = [f"{'1' if c.startswith('6') else '0'}.{c}" for c in codes]
        r = py_requests.get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={"secids": ",".join(secids), "fields": "f2,f3,f12,f14,f15,f16,f18,f8"},
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=10,
        )
        data = r.json()
        results = []
        for i in data.get("data", {}).get("diff", []):
            results.append({
                "name": i.get("f14",""), "price": (i.get("f2",0) or 0) / 100,
                "change_pct": (i.get("f3",0) or 0) / 100,
                "high": (i.get("f15",0) or 0) / 100 if i.get("f15") else None,
                "low": (i.get("f16",0) or 0) / 100 if i.get("f16") else None,
                "prev_close": (i.get("f18",0) or 0) / 100 if i.get("f18") else None,
                "volume": i.get("f8",0) or 0,
            })
        return results
    finally:
        try:
            subprocess.run(['curl','-s','--unix-socket','/tmp/verge/verge-mihomo.sock',
                '-X','PATCH','http://localhost/configs',
                '-H','Content-Type: application/json',
                '-d',_json.dumps({'mode':'rule'})], capture_output=True, timeout=5)
        except Exception:
            pass


def _fetch_sina_rt_hk(params: dict) -> list[dict]:
    """Sina 港股实时行情 rt_hk"""
    codes = params.get("symbols", [])
    hk_codes = ",".join(f"rt_hk{c.zfill(5)}" for c in codes)
    url = f"http://hq.sinajs.cn/list={hk_codes}"
    r = py_requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=3)
    r.encoding = "gbk"
    results = []
    for line in r.text.strip().split("\n"):
        if "=" not in line:
            continue
        raw = line.split("=", 1)[1].strip('";')
        fields = raw.split(",")
        if len(fields) < 9:
            continue
        results.append({
            "name": fields[1],
            "open": float(fields[2] or 0), "prev_close": float(fields[3] or 0),
            "high": float(fields[4] or 0), "low": float(fields[5] or 0),
            "price": float(fields[6] or 0), "change_pct": float(fields[8] or 0),
            "volume": float(fields[9] or 0) if len(fields) > 9 else 0,
        })
    return results


def _fetch_sina_short_sell(params: dict) -> list[dict]:
    """Sina 港股做空数据 — JSON API 已废弃，尝试替代端点"""
    # 尝试1: Sina market center API (node-based, may work)
    url = (
        "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHKStockData?page=1&size=100&sort=changepercent&"
        "order=desc&node=hkstock"
    )
    r = py_requests.get(
        url,
        headers={"Referer": "https://stock.finance.sina.com.cn", "User-Agent": UA},
        timeout=10,
    )
    r.encoding = "gbk"
    text = r.text.strip()
    if text == "[]" or not text:
        raise Exception("Sina short sell API returned empty (deprecated)")
    try:
        raw = json.loads(text)
    except Exception:
        raise Exception(f"Sina short sell parse error: {text[:100]}")
    return [{
        "code": item.get("symbol", ""),
        "name": item.get("name", ""),
        "short_volume": float(item.get("short_volume", 0) or 0),
        "short_amount": float(item.get("short_amount", 0) or 0),
        "short_pct": float(item.get("short_pct", 0) or 0),
    } for item in raw]


def _fetch_em_short_sell(params: dict) -> list[dict]:
    """东财港股做空数据"""
    import requests as rq
    session = rq.Session()
    session.trust_env = False
    r = session.get(
        "https://datacenter.eastmoney.com/securities/api/data/v1/get",
        params={
            "reportName": "RPT_HK_SHORT_SELL",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,SHORT_VOL,SHORT_AMOUNT,TURNOVER_RATIO",
            "pageSize": 100,
            "sortTypes": -1,
            "sortColumns": "TRADE_DATE",
        },
        headers={"Referer": "https://data.eastmoney.com/", "User-Agent": UA},
        timeout=10,
    )
    if r.status_code != 200:
        raise Exception(f"EM short sell failed: {r.status_code}")
    data = r.json()
    if not data.get("success"):
        raise Exception("EM API returned failure")
    return [{
        "code": row.get("SECURITY_CODE", ""),
        "name": row.get("SECURITY_NAME_ABBR", ""),
        "short_volume": row.get("SHORT_VOL", 0) or 0,
        "short_amount": row.get("SHORT_AMOUNT", 0) or 0,
        "date": row.get("TRADE_DATE", ""),
    } for row in data.get("result", {}).get("data", [])]


def _fetch_hkex_short_sell(params: dict) -> list[dict]:
    """HKEX 做空数据（curl_cffi 绕过 Akamai）"""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        raise Exception("curl_cffi not installed")
    r = curl_requests.get(
        "https://www.hkex.com.hk/eng/stat/smstat/shortselling/ssqty.htm",
        impersonate="chrome131",
        headers={"Accept": "text/html", "User-Agent": UA},
        timeout=15,
    )
    if r.status_code == 503 and "Akamai" in (r.headers.get("Server", "") or ""):
        raise Exception("Akamai 503 — blocked")
    if r.status_code != 200:
        raise Exception(f"HKEX returned {r.status_code}")
    # HTML 解析做空表格（简化：提取 table 行）
    import re
    rows = re.findall(r'<tr[^>]*>.*?</tr>', r.text, re.DOTALL)
    results = []
    for row in rows[1:11]:  # 跳过表头，取前10
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) >= 4:
            results.append({
                "code": re.sub(r'<[^>]+>', '', cells[0]).strip(),
                "name": re.sub(r'<[^>]+>', '', cells[1]).strip(),
                "short_volume": _parse_num(re.sub(r'<[^>]+>', '', cells[2])),
                "short_pct": _parse_num(re.sub(r'<[^>]+>', '', cells[3])),
            })
    return results


def _parse_num(s: str) -> float:
    import re
    s = re.sub(r'[^\d.,-]', '', s).replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0


def _fetch_akshare_margin(params: dict) -> list[dict]:
    """akshare 融资融券数据"""
    import akshare as ak
    df = ak.macro_china_market_margin_sh()
    df_valid = df[df["融资余额"].notna()]
    if df_valid.empty:
        raise Exception("融资余额全部NaN")
    last = df_valid.iloc[-1]
    prev = df_valid.iloc[-2] if len(df_valid) >= 2 else last
    return [{
        "date": str(last["日期"])[:10],
        "margin_balance": float(last["融资余额"]),
        "margin_buy": float(last.get("融资买入额", 0) or 0),
        "prev_balance": float(prev["融资余额"]),
    }]


def _fetch_sina_lhb(params: dict) -> list[dict]:
    """Sina 龙虎榜"""
    import akshare as ak
    from datetime import date, timedelta
    target = params.get("date", (date.today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    df = ak.stock_lhb_detail_daily_sina(date=target)
    if df is None or df.empty:
        raise Exception(f"Sina LHB empty for {target}")
    return df.head(50).to_dict(orient="records")


def _fetch_em_lhb(params: dict) -> list[dict]:
    """东财龙虎榜"""
    import akshare as ak
    from datetime import date, timedelta
    target = params.get("date", (date.today() - timedelta(days=1)).isoformat().replace("-", ""))
    try:
        df = ak.stock_lhb_detail_em(start_date=target, end_date=target)
        if df is None or df.empty:
            raise Exception(f"EM LHB empty for {target}")
        return df.head(50).to_dict(orient="records")
    except TypeError:
        # akshare API可能变了，降级
        raise Exception("akshare stock_lhb_detail_em API changed")


# ════════════════════════════════════════════
# 降级链注册表
# ════════════════════════════════════════════

SOURCE_CHAIN: dict[str, list[Source]] = {
    "a_quote": [
        Source("sina_direct",    _fetch_sina_spot,      level=0, timeout=3),
        Source("push2_fallback", _fetch_push2_quote,    level=0, timeout=10, proxy=False),
        Source("db_cache",       lambda p: _cache_fallback("a_quote", p), level=None),
    ],
    "hk_quote": [
        Source("sina_rt_hk",     _fetch_sina_rt_hk,     level=0, timeout=3),
        Source("db_cache",       lambda p: _cache_fallback("hk_quote", p), level=None),
    ],
    "short_sell": [
        Source("sina_short_sell", _fetch_sina_short_sell, level=0, timeout=10),
        Source("em_short_sell",   _fetch_em_short_sell,   level=0, timeout=10),
        Source("hkex_direct",     _fetch_hkex_short_sell, level=1, timeout=15),
        Source("file_cache",      lambda p: _cache_fallback("short_sell", p), level=None),
    ],
    "macro_margin": [
        Source("akshare_margin",  _fetch_akshare_margin,  level=0, timeout=10),
        Source("file_cache",      lambda p: _cache_fallback("macro_margin", p), level=None),
    ],
    "lhb_detail": [
        Source("sina_lhb",       _fetch_sina_lhb,        level=0, timeout=10),
        Source("em_lhb",         _fetch_em_lhb,          level=0, timeout=10),
    ],
}


def _cache_fallback(data_type: str, params: dict) -> Any:
    cached = _read_cache(data_type, params)
    if cached and (time.time() - cached["ts"]) < 86400:
        return cached["data"]
    raise Exception(f"Cache miss or expired for {data_type}")


# ════════════════════════════════════════════
# 核心引擎
# ════════════════════════════════════════════

class AllSourcesFailed(Exception):
    def __init__(self, data_type: str, errors: list):
        self.data_type = data_type
        self.errors = errors
        super().__init__(f"ALL_SOURCES_DOWN: {data_type} — {len(errors)} sources failed")


def resilient_fetch(
    data_type: str,
    params: dict = None,
    max_staleness: int = 86400,
    proxy_ok: bool = False,
) -> FetchResult:
    """
    沿降级链依次尝试各数据源。

    Args:
        data_type: 数据类型（见 SOURCE_CHAIN keys）
        params: 传给源的参数
        max_staleness: 缓存最大允许年龄(秒)
        proxy_ok: 是否允许自动开 Clash 代理

    Returns:
        FetchResult(data, source_name, is_stale)

    Raises:
        AllSourcesFailed: 全部源+缓存都失败
    """
    params = params or {}
    chain = SOURCE_CHAIN.get(data_type, [])
    errors = []

    for src in chain:
        # 跳过需要代理但未授权的源
        if src.proxy and not proxy_ok:
            continue

        t0 = time.time()
        try:
            if src.proxy:
                _clash_set_mode("rule")
                time.sleep(1)

            data = src.fetch_fn(params)
            elapsed = (time.time() - t0) * 1000

            if src.proxy:
                _clash_set_mode("direct")

            _record(src.name, ok=True, latency_ms=elapsed)
            if data and (not isinstance(data, list) or len(data) > 0):
                _write_cache(data_type, params, data)
            return FetchResult(data=data, source=src.name)

        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            _record(src.name, ok=False, latency_ms=elapsed, error=str(e)[:200])
            errors.append((src.name, str(e)[:200]))
            if src.proxy:
                _clash_set_mode("direct")
            continue

    # 全源失败 → 缓存兜底
    cached = _read_cache(data_type, params)
    if cached:
        age = time.time() - cached["ts"]
        if age < max_staleness:
            return FetchResult(data=cached["data"], source="cache", is_stale=True)

    raise AllSourcesFailed(data_type, errors)
