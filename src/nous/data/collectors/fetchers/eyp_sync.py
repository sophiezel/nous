"""
2025eyp.com 增量同步脚本
每日凌晨运行，拉取新增文章、更新已有文章
"""

import requests
import json
import os
import hashlib
from datetime import datetime

BASE = "http://127.0.0.1"
WIKI_DATA = os.path.expanduser("~/wiki/finance/raw/data/eyp")
WIKI_ARTICLES = os.path.join(WIKI_DATA, "articles")
ALL_JSON = os.path.join(WIKI_DATA, "all-articles.json")

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": "Bearer",
    "Origin": "http://h5.2025eyp.com",
    "Referer": "http://h5.2025eyp.com/",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15",
})
# TLS verification enabled (was disabled)


def fetch_new_articles(column_id: int = 1, pages: int = 5):
    """拉取最新文章列表（前 N 页）"""
    articles = []
    for page in range(1, pages + 1):
        r = SESSION.get(
            f"{BASE}/v1/articles/new",
            params={"page": page, "count": 10, "columnId": column_id},
            timeout=10,
        )
        data = r.json()
        if data.get("status") == 200:
            articles.extend(data["data"]["rows"])
    return articles


def fetch_article_detail(article_id: int):
    """拉取单篇文章详情"""
    r = SESSION.get(f"{BASE}/v1/articles/{article_id}", timeout=10)
    if r.status_code == 200:
        data = r.json()
        if data.get("status") == 200:
            return data
    return None


def fetch_comments(article_id: int):
    """拉取单篇文章评论"""
    r = SESSION.get(f"{BASE}/v1/articles/{article_id}/comments", timeout=10)
    if r.status_code == 200:
        data = r.json()
        if data.get("status") == 200:
            return data
    return None


def load_existing_ids():
    """加载已有文章 ID 列表"""
    if os.path.exists(ALL_JSON):
        with open(ALL_JSON) as f:
            existing = json.load(f)
        return {a["id"] for a in existing}, existing
    return set(), []


def main():
    print(f"[{datetime.now()}] 2025eyp 增量同步开始...")
    os.makedirs(WIKI_ARTICLES, exist_ok=True)
    
    existing_ids, existing_articles = load_existing_ids()
    print(f"  已有文章: {len(existing_ids)} 篇")
    
    # 拉取最新文章
    new_articles = fetch_new_articles(column_id=1, pages=10)
    print(f"  最新 100 篇中...")
    
    new_count = 0
    for a in new_articles:
        if a["id"] not in existing_ids:
            # 新文章，拉取详情
            detail = fetch_article_detail(a["id"])
            if detail:
                with open(os.path.join(WIKI_ARTICLES, f"{a['id']}.json"), "w") as f:
                    json.dump(detail, f, ensure_ascii=False)
                new_count += 1
                print(f"    NEW [{a['id']}] {a['title']}")
            
            # 评论
            comments = fetch_comments(a["id"])
            if comments:
                with open(os.path.join(WIKI_ARTICLES, f"{a['id']}-comments.json"), "w") as f:
                    json.dump(comments, f, ensure_ascii=False)
            
            existing_articles.append(a)
            existing_ids.add(a["id"])
    
    # 更新全量列表
    with open(ALL_JSON, "w") as f:
        json.dump(existing_articles, f, ensure_ascii=False, indent=2)
    
    print(f"[{datetime.now()}] 同步完成: 新增 {new_count} 篇, 总计 {len(existing_ids)} 篇")
    return new_count


if __name__ == "__main__":
    main()
