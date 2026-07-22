#!/usr/bin/env python3
"""新浪北向资金直连采集器
数据源: Sina finance API (低反爬, 无需代理)
使用 curl_cffi + impersonate 绕过基础反爬
"""
import re
import json
import sqlite3
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

from nous.core.db import _resolve_path
DB = Path(_resolve_path("screener.db"))

# Sina接口 (列表页提供汇总数据)
SINA_HSGT_URL = "https://vip.stock.finance.sina.com.cn/q/go.php/vInvestConsult/kind/hsgt/index.phtml"
SINA_REALTIME_API = "https://hq.sinajs.cn/list="

def _get_session():
    """获取带标准头的requests session"""
    import requests
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn/',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    return s


def fetch_hsgt_summary() -> Optional[dict]:
    """爬取新浪沪深港通汇总页面, 提取北向/南向净买额
    
    Returns:
        {north_net_buy: float(亿), south_net_buy: float(亿), trade_date: str} or None
    """
    try:
        # 尝试curl_cffi (如果可用)
        try:
            from curl_cffi import requests as cffi_requests
            resp = cffi_requests.get(
                SINA_HSGT_URL,
                impersonate="chrome131",
                timeout=10
            )
        except ImportError:
            resp = _get_session().get(SINA_HSGT_URL, timeout=10)
        
        if resp.status_code != 200:
            return None
        
        html = resp.text
        
        # 提取北向净买
        north_patterns = [
            r'北向资金.*?净买[入出].*?([+-]?\d+\.?\d*)\s*亿',
            r'沪股通.*?净买[入出].*?([+-]?\d+\.?\d*)\s*亿',
            r'north.*?net.*?([+-]?\d+\.?\d*)',
        ]
        north_net = None
        for pat in north_patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                try:
                    north_net = float(m.group(1))
                    break
                except ValueError:
                    continue
        
        # 提取南向净买
        south_patterns = [
            r'南向资金.*?净买[入出].*?([+-]?\d+\.?\d*)\s*亿',
            r'港股通.*?净买[入出].*?([+-]?\d+\.?\d*)\s*亿',
        ]
        south_net = None
        for pat in south_patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                try:
                    south_net = float(m.group(1))
                    break
                except ValueError:
                    continue
        
        if north_net is not None or south_net is not None:
            return {
                "north_net_buy": north_net,
                "south_net_buy": south_net,
                "trade_date": date.today().isoformat(),
                "source": "sina_hsgt_page",
            }
    except Exception:
        pass
    
    return None


def fetch_index_flow() -> Optional[dict]:
    """通过Sina实时API获取大盘指数资金流
    
    利用指数涨跌推断资金方向(弱信号, 仅辅助验证)
    """
    try:
        symbols = "s_sh000001,s_sz399001,s_sh000300"
        resp = _get_session().get(
            f"{SINA_REALTIME_API}{symbols}",
            headers={'Referer': 'https://finance.sina.com.cn'},
            timeout=5
        )
        if resp.status_code != 200:
            return None
        
        # 解析: var hq_str_s_sh000001="上证指数,3300.50,25.30,0.77%,..."
        results = {}
        for line in resp.text.split('\n'):
            m = re.search(r'hq_str_(\w+)="([^"]+)"', line)
            if m:
                code = m.group(1)
                fields = m.group(2).split(',')
                if len(fields) >= 4:
                    results[code] = {
                        "name": fields[0],
                        "price": float(fields[1]) if fields[1] else None,
                        "change": float(fields[2]) if fields[2] else None,
                        "change_pct": fields[3],
                    }
        
        if results:
            return {
                "indices": results,
                "trade_date": date.today().isoformat(),
                "source": "sina_realtime_index",
            }
    except Exception:
        pass
    
    return None


def write_to_db(data: dict):
    """写入sina_northbound_cache表(如不存在则建)"""
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA busy_timeout=5000")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sina_northbound_cache (
            trade_date TEXT PRIMARY KEY,
            north_net_buy REAL,
            south_net_buy REAL,
            source TEXT,
            fetched_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    conn.execute("""
        INSERT OR REPLACE INTO sina_northbound_cache (trade_date, north_net_buy, south_net_buy, source)
        VALUES (?, ?, ?, ?)
    """, (data["trade_date"], data.get("north_net_buy"), data.get("south_net_buy"), data.get("source", "sina")))
    
    conn.commit()
    conn.close()


if __name__ == "__main__":
    result = fetch_hsgt_summary()
    if result:
        print(f"✅ Sina北向: {result.get('north_net_buy', 'N/A')}亿 | 南向: {result.get('south_net_buy', 'N/A')}亿")
        write_to_db(result)
    else:
        print("❌ Sina页面解析失败")
    
    # 辅助: 指数信号
    idx = fetch_index_flow()
    if idx:
        for k, v in idx.get("indices", {}).items():
            print(f"  {v['name']}: {v['price']} ({v['change_pct']})")
