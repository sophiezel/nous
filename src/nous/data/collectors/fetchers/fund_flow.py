"""个股资金流向：同花顺 10jqka + curl_cffi + JS challenge"""
import sys, os, re, time, io
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

# ── Monkey-patch requests → curl_cffi ──────────
import requests as _orig_requests
from curl_cffi import requests as _curl_requests
_orig_requests.get = lambda url, **kw: _curl_requests.get(
    url, impersonate='chrome131', timeout=15,
    **{k: v for k, v in kw.items() if k not in ('proxies',)}
)
for k in list(os.environ):
    if 'proxy' in k.lower(): del os.environ[k]

# ── 同花顺 JS challenge ─────────────────────────
from py_mini_racer import MiniRacer
import akshare.stock_feature.stock_fund_flow as _ths_mod
_ths_get_file = _ths_mod._get_file_content_ths

def _gen_v_code() -> str:
    """生成同花顺 hexin-v token"""
    js_code = MiniRacer()
    js_content = _ths_get_file("ths.js")
    js_code.eval(js_content)
    return js_code.call("v")

HEADERS_BASE = {
    "Accept": "text/html, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Host": "data.10jqka.com.cn",
    "Pragma": "no-cache",
    "Referer": "http://data.10jqka.com.cn/funds/hyzjl/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.85 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

BOARD_MAP = {"即时": None, "3日": 3, "5日": 5, "10日": 10, "20日": 20}

from nous.data import storage


def fetch_fund_flow_10jqka(symbol: str = "即时") -> list[dict]:
    """直接从同花顺采集个股资金流向（绕过 AKShare pandas bug）"""
    headers = {**HEADERS_BASE, "hexin-v": _gen_v_code()}

    # 第一步：获取总页数
    url_first = "http://data.10jqka.com.cn/funds/ggzjl/field/code/order/desc/ajax/1/free/1/"
    try:
        r = _orig_requests.get(url_first, headers=headers)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")
        page_span = soup.find("span", class_="page_info")
        if not page_span:
            print(f"[FUNDFLOW] No page_info, status={r.status_code}")
            return []
        page_num = int(page_span.text.split("/")[1])
    except Exception as e:
        print(f"[FUNDFLOW] First page error: {e}")
        return []

    # 第二步：确定 URL pattern
    board = BOARD_MAP.get(symbol)
    if board:
        url_pattern = f"http://data.10jqka.com.cn/funds/ggzjl/board/{board}/field/zdf/order/desc/page/{{}}/ajax/1/free/1/"
    else:
        url_pattern = "http://data.10jqka.com.cn/funds/ggzjl/field/zdf/order/desc/page/{}/ajax/1/free/1/"

    # 第三步：逐页拉取
    all_rows = []
    for page in range(1, page_num + 1):
        headers["hexin-v"] = _gen_v_code()  # 每页重新生成
        try:
            r = _orig_requests.get(url_pattern.format(page), headers=headers)
            if r.status_code != 200:
                break
            import pandas as pd
            df = pd.read_html(io.StringIO(r.text))[0]
            for _, row in df.iterrows():
                all_rows.append({
                    "symbol": str(row.get("股票代码", "")).zfill(6),
                    "name": str(row.get("股票简称", "")),
                    "close": _f(row.get("最新价")),
                    "pct_change": _f(row.get("涨跌幅")),
                    "main_net": _f(row.get("净额(元)")),       # 主力净额
                    "inflow": _f(row.get("流入资金(元)")),     # 流入
                    "outflow": _f(row.get("流出资金(元)")),    # 流出
                    "amount": _f(row.get("成交额(元)")),       # 成交额
                    "turnover": _f(row.get("换手率")),
                })
        except Exception as e:
            print(f"[FUNDFLOW] Page {page} error: {e}")
            continue
        time.sleep(0.3)

    print(f"[FUNDFLOW] {page_num} pages, {len(all_rows)} stocks")
    return all_rows


def _f(v):
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        s = str(v).replace(",", "").replace("%", "").strip()
        if not s or s == "nan":
            return None
        # 处理中文单位
        multiplier = 1
        if "亿" in s:
            multiplier = 1e8
            s = s.replace("亿", "")
        elif "万" in s:
            multiplier = 1e4
            s = s.replace("万", "")
        f = float(s) * multiplier
        return f
    except (ValueError, TypeError):
        return None


def collect_today():
    today_str = date.today().isoformat()
    rows = fetch_fund_flow_10jqka("即时")
    if not rows:
        return 0
    
    # 标准化为 fund_flow_stock 格式
    db_rows = []
    for r in rows:
        db_rows.append({
            "trade_date": today_str,
            "symbol": r["symbol"],
            "main_net": r.get("main_net"),
            "main_pct": None,
            "super_large_net": None,
            "large_net": None,
            "medium_net": None,
            "small_net": None,
            "total_amount": None,
        })
    
    n = storage.save_fund_flow(db_rows)
    print(f"[FUNDFLOW] Saved {n} rows")
    return n


if __name__ == "__main__":
    collect_today()
