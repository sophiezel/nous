#!/usr/bin/env python3
"""
手动同步脚本：读取 ~/wiki/finance/reports/ 下的已有报告文件，
持久化到 Dashboard SQLite 数据库。用于：
- 首次启动时回填历史数据
- AGENT-mode cron 的补充覆盖
"""

import os
import sys
import json
import glob
from datetime import datetime

sys.path.insert(0, os.path.expanduser("~/code/dashboard/scripts"))
from report_store import store_report, store_macro_score, store_sentiment

WIKI = os.path.expanduser("~/wiki/finance")


def sync_reports_dir(subdir: str, report_type: str, glob_pattern: str = "*.md"):
    """同步 reports 子目录下的所有 markdown 文件"""
    base = os.path.join(WIKI, "reports", subdir)
    if not os.path.isdir(base):
        print(f"  [SKIP] dir not found: {base}")
        return 0

    files = sorted(glob.glob(os.path.join(base, glob_pattern)))
    count = 0
    for f in files[-30:]:  # 最近 30 个文件
        try:
            with open(f, encoding="utf-8") as fh:
                content = fh.read()
            fname = os.path.basename(f).replace(".md", "")
            store_report(report_type, content, title=fname)
            count += 1
        except Exception as e:
            print(f"  [ERROR] {f}: {e}")
    print(f"  {subdir}: {count} files synced")
    return count


def sync_macro_scores():
    """同步宏观评分 JSON 历史"""
    json_path = os.path.join(WIKI, "raw", "macro", "macro_score.json")
    if not os.path.exists(json_path):
        print("  [SKIP] macro_score.json not found")
        return 0

    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        store_macro_score(
            date=data.get("date", datetime.now().strftime("%Y-%m-%d")),
            score=data.get("total", 0),
            position=data.get("recommended_position", 0.5),
            indicators={
                "cpi": data.get("cpi_score", 0),
                "ppi": data.get("ppi_score", 0),
                "pmi": data.get("pmi_score", 0),
                "liquidity": data.get("liquidity_score", 0),
                "margin": data.get("margin_score", 0),
            },
        )
        print(f"  macro_score: synced (score={data.get('total')})")
        return 1
    except Exception as e:
        print(f"  [ERROR] macro_score: {e}")
        return 0


def sync_sentiment():
    """同步最新情绪数据"""
    sent_dir = os.path.join(WIKI, "raw", "sentiment")
    if not os.path.isdir(sent_dir):
        print("  [SKIP] sentiment dir not found")
        return 0

    files = sorted(glob.glob(os.path.join(sent_dir, "*.json")))
    if not files:
        return 0

    latest = files[-1]
    try:
        with open(latest, encoding="utf-8") as f:
            data = json.load(f)
        store_sentiment(
            date=data.get("date", datetime.now().strftime("%Y-%m-%d")),
            score=int(data.get("sentiment_score", 0)),
            limit_up_count=data.get("limit_up_count", 0),
            limit_up_rate=round(data.get("limit_up_count", 0) / 5514, 4),
            details={
                "涨停家数": data.get("limit_up_count"),
                "跌停家数": data.get("limit_down_count"),
                "炸板率": data.get("bust_ratio"),
                "涨跌比": data.get("adv_decl_ratio"),
                "最高连板": data.get("max_consecutive_boards"),
                "情绪标签": data.get("sentiment_label"),
            },
        )
        print(f"  sentiment: synced (score={data.get('sentiment_score')})")
        return 1
    except Exception as e:
        print(f"  [ERROR] sentiment: {e}")
        return 0


if __name__ == "__main__":
    print("=" * 50)
    print(f"  Dashboard 数据同步 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    print()

    total = 0

    # P0 报告
    total += sync_reports_dir("daily", "daily_picks", "*.md")
    total += sync_reports_dir("trader", "trader_daily", "*.md")
    total += sync_reports_dir("portfolio", "portfolio_review", "*.md")

    # 结构化数据
    total += sync_macro_scores()
    total += sync_sentiment()

    print()
    print(f"  总计同步: {total} 条")
    print(f"  Dashboard: http://localhost:3456")
