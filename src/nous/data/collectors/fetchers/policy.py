"""
宏观政策数据采集器
每日爬取国家政策网站的最新政策新闻，存入 ~/wiki/finance/raw/articles/
用于 Phase 2.1 - 为每日荐股提供政策催化信号

站点列表：
- www.gov.cn (国务院)
- www.pbc.gov.cn (央行)
- www.ndrc.gov.cn (发改委)
- www.csrc.gov.cn (证监会)
- news.xinhuanet.com (新华网财经)
- finance.eastmoney.com (东方财富要闻 - 需 rule 代理模式)
"""

import requests
import json
import hashlib
import os
import re
from datetime import datetime, date
from typing import List, Dict

WIKI_RAW = os.path.expanduser("~/wiki/finance/raw/articles")

# 政策站点配置
POLICY_SOURCES = [
    {
        "name": "国务院",
        "url": "https://www.gov.cn/lianbo/bumen/index.htm",
        "selector": "a.news-title",  # CSS selector placeholder
    },
    {
        "name": "央行",
        "url": "http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html",
    },
    {
        "name": "发改委",
        "url": "https://www.ndrc.gov.cn/xwdt/xwfb/",
    },
    {
        "name": "证监会",
        "url": "http://www.csrc.gov.cn/csrc/c100028/common_list.shtml",
    },
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
})


def fetch_source(source: Dict) -> List[Dict]:
    """爬取单个政策来源的最新文章"""
    articles = []
    try:
        r = SESSION.get(source["url"], timeout=15)
        r.encoding = r.apparent_encoding or "utf-8"
        html = r.text
        
        # 通用标题提取（基于常见政府网站结构）
        # 提取所有 <a> 标签的标题和链接
        links = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>([^<]{10,200})</a>', html)
        
        today = date.today().strftime("%Y-%m-%d")
        for href, title in links:
            title = re.sub(r'<[^>]+>', '', title).strip()
            # 过滤：只保留含政策关键词的标题
            if any(kw in title for kw in ["政策", "通知", "意见", "措施", "监管", 
                   "改革", "利率", "贷款", "降准", "降息", "财政", "货币",
                   "产业", "规划", "方案", "条例", "公告"]):
                # 构造完整 URL
                if href.startswith("/"):
                    base = re.match(r'https?://[^/]+', source["url"]).group()
                    full_url = base + href
                elif href.startswith("./"):
                    base = source["url"].rsplit("/", 1)[0]
                    full_url = base + "/" + href[2:]
                else:
                    full_url = href
                
                articles.append({
                    "source": source["name"],
                    "title": title,
                    "url": full_url,
                    "fetched_at": datetime.now().isoformat(),
                })
    except Exception as e:
        print(f"[ERROR] {source['name']}: {e}")
    
    return articles


def save_articles(articles: List[Dict]):
    """保存文章到 wiki raw 目录，自动去重"""
    os.makedirs(WIKI_RAW, exist_ok=True)
    
    today = date.today().strftime("%Y-%m-%d")
    filename = f"policy-{today}.json"
    filepath = os.path.join(WIKI_RAW, filename)
    
    # 去重（基于 title hash）
    unique = {}
    for a in articles:
        key = hashlib.md5(a["title"].encode()).hexdigest()
        if key not in unique:
            unique[key] = a
    
    with open(filepath, "w") as f:
        json.dump(list(unique.values()), f, ensure_ascii=False, indent=2)
    
    print(f"[OK] Saved {len(unique)} articles to {filepath}")
    return filepath


def main():
    print(f"[{datetime.now()}] 宏观政策采集开始...")
    all_articles = []
    
    for source in POLICY_SOURCES:
        print(f"  Fetching {source['name']}...")
        articles = fetch_source(source)
        print(f"    Found {len(articles)} articles")
        all_articles.extend(articles)
    
    filepath = save_articles(all_articles)
    print(f"[{datetime.now()}] 采集完成: {filepath}")


if __name__ == "__main__":
    main()
