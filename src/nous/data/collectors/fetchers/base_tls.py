"""统一 TLS 伪装会话工厂 — curl_cffi impersonate Chrome/Safari"""
from curl_cffi import requests

# 浏览器指纹
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
}

SEC_FETCH_API = {
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

SEC_CH_UA = {
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
}

# 站点 Referer 映射
SITE_REFERERS = {
    "eastmoney": "https://data.eastmoney.com/",
    "eastmoney_quote": "https://quote.eastmoney.com/",
    "sina": "https://finance.sina.com.cn/",
    "10jqka": "https://www.10jqka.com.cn/",
    "tencent": "https://finance.qq.com/",
}


def create_session(site: str = "eastmoney", impersonate: str = "chrome131"):
    """创建带站点专属头的 curl_cffi Session"""
    s = requests.Session(impersonate=impersonate)
    s.headers.update(BASE_HEADERS)
    s.headers.update(SEC_FETCH_API)
    s.headers.update(SEC_CH_UA)
    s.headers["Referer"] = SITE_REFERERS.get(site, SITE_REFERERS["eastmoney"])
    return s


def em_api_session(warmup: bool = True):
    """Eastmoney API 专用 session，含 Cookie 预热"""
    s = create_session("eastmoney")
    if warmup:
        try:
            s.get("https://data.eastmoney.com/", timeout=10)
        except Exception:
            pass  # 预热失败不阻塞
    return s
