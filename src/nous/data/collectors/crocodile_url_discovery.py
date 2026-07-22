#!/usr/bin/env python3
"""鳄鱼派微信文章URL发现 — 搜狗+Bing多关键词搜索"""
import re, json, time, urllib.parse
from playwright.sync_api import sync_playwright

OUTPUT = '~/wiki/finance/raw/articles/crocodile-wechat-urls.json'

KEYWORDS = [
    '像鳄鱼一样思考',
    '像鳄鱼一样思考 A股',
    '像鳄鱼一样思考 复盘',
    '像鳄鱼一样思考 交易',
    '像鳄鱼一样思考 主线',
    '像鳄鱼一样思考 板块',
    '像鳄鱼一样思考 飞行员',
    '像鳄鱼一样思考 文大户',
    '像鳄鱼一样思考 金融 科技',
    '像鳄鱼一样思考 IF 基差',
]

def extract_urls_from_page(page, html):
    """从搜狗搜索结果页提取 mp.weixin.qq.com 文章链接"""
    urls = set()
    # 方式1: 直接找 mp.weixin.qq.com/s/ 链接
    for m in re.finditer(r'https?://mp\.weixin\.qq\.com/s/[A-Za-z0-9_-]+', html):
        urls.add(m.group(0))
    
    # 方式2: 通过搜狗重定向链接提取
    for m in re.finditer(r'url=([^"&\s]+)', html):
        decoded = urllib.parse.unquote(m.group(1))
        if 'mp.weixin.qq.com/s/' in decoded:
            urls.add(decoded)
    
    return urls

def search_sogou(keyword):
    """用Playwright搜索搜狗微信（绕过JS反爬）"""
    url = f'https://weixin.sogou.com/weixin?type=1&query={urllib.parse.quote(keyword)}'
    try:
        p = sync_playwright().start()
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=15000)
        page.wait_for_timeout(2000)
        html = page.content()
        urls = extract_urls_from_page(page, html)
        b.close()
        p.stop()
        return urls
    except Exception as e:
        print(f'  Sogou error for "{keyword}": {e}')
        return set()

def main():
    all_urls = set()
    
    for kw in KEYWORDS:
        print(f'Searching: {kw}')
        urls = search_sogou(kw)
        print(f'  Found {len(urls)} URLs')
        all_urls.update(urls)
        time.sleep(2)  # 避免搜狗限流
    
    # 去重+排序
    url_list = sorted(all_urls)
    print(f'\nTotal unique URLs: {len(url_list)}')
    
    # 保存
    import os
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, 'w') as f:
        json.dump(url_list, f, indent=2, ensure_ascii=False)
    print(f'Saved to {OUTPUT}')
    
    # 预览
    for u in url_list[:10]:
        print(f'  {u}')

if __name__ == '__main__':
    main()
