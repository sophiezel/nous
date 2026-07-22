"""数据获取基类：代理绕过、重试、限流"""
import os
import time
from typing import Optional

import requests


def clean_session() -> requests.Session:
    """创建绕过系统代理的 requests.Session"""
    for k in list(os.environ):
        if "proxy" in k.lower():
            del os.environ[k]
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return s


def fetch_json(url: str, params: Optional[dict] = None, timeout: int = 15, retries: int = 2) -> dict:
    """GET JSON，含重试"""
    s = clean_session()
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = s.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait = 2 ** attempt
                time.sleep(wait)
    raise last_err


def fetch_text(url: str, headers: Optional[dict] = None, timeout: int = 15, retries: int = 2) -> str:
    """GET 纯文本，含重试"""
    s = clean_session()
    if headers:
        s.headers.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = s.get(url, timeout=timeout)
            r.encoding = "gbk"  # 新浪接口返回 GBK
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait = 2 ** attempt
                time.sleep(wait)
    raise last_err
