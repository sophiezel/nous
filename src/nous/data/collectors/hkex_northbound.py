#!/usr/bin/env python3
"""HKEX Stock Connect官方数据采集器 (Playwright)
Layer 3 降级源: 仅当东财/新浪/本地DB全部不可用时启动

数据源: https://www.hkex.com.hk/Mutual-Market/Stock-Connect/
官方每日北向/南向成交额, 无监管限制, 数据100%准确但需翻墙
"""
import sqlite3
from pathlib import Path
from datetime import date
from typing import Optional

from nous.core.db import _resolve_path
DB = Path(_resolve_path("screener.db"))


def fetch_hkex_daily() -> Optional[dict]:
    """使用Playwright爬取HKEX官方每日成交额
    
    触发条件: L1(东财+新浪+本地DB)全部不可用
    注意: 需要Playwright + chromium, 首次运行需 playwright install chromium
    
    Returns:
        {north_turnover: float(亿), south_turnover: float(亿), trade_date: str} or None
    """
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # HKEX Stock Connect 统计页面
            page.goto(
                "https://www.hkex.com.hk/Mutual-Market/Stock-Connect/Statistics/Historical-Daily-Turnover-Update.htm",
                wait_until="domcontentloaded",
                timeout=15000
            )
            
            # 等待表格加载 (HKEX 2025改为JS渲染)
            page.wait_for_selector("table", timeout=10000)
            
            # 提取最新一行数据
            rows = page.evaluate("""() => {
                const table = document.querySelector('table');
                if (!table) return [];
                const trs = table.querySelectorAll('tr');
                const data = [];
                trs.forEach(tr => {
                    const tds = tr.querySelectorAll('td');
                    if (tds.length >= 4) {
                        data.push({
                            date: tds[0]?.textContent?.trim(),
                            north_buy: tds[1]?.textContent?.trim(),
                            north_sell: tds[2]?.textContent?.trim(),
                            south_buy: tds[3]?.textContent?.trim(),
                            south_sell: tds[4]?.textContent?.trim(),
                        });
                    }
                });
                return data;
            }""")
            
            browser.close()
            
            if rows:
                latest = rows[-1]
                return {
                    "trade_date": latest.get("date", date.today().isoformat()),
                    "north_turnover": _parse_hkex_number(latest.get("north_buy", "0")),
                    "south_turnover": _parse_hkex_number(latest.get("south_buy", "0")),
                    "source": "hkex_official",
                }
    except ImportError:
        print("⚠️  Playwright未安装: pip install playwright && playwright install chromium")
    except Exception as e:
        print(f"⚠️  HKEX采集失败: {e}")
    
    return None


def _parse_hkex_number(s: str) -> float:
    """解析HKEX表格中的数字: '1,234.5M' → 12.345亿"""
    s = s.replace(",", "").strip()
    if s.endswith("M"):
        return float(s[:-1]) / 10  # Million → 亿
    if s.endswith("B"):
        return float(s[:-1]) * 10  # Billion → 亿
    try:
        return float(s)
    except ValueError:
        return 0.0


if __name__ == "__main__":
    result = fetch_hkex_daily()
    if result:
        print(f"✅ HKEX: 北向成交{result.get('north_turnover','?')}亿 | 南向成交{result.get('south_turnover','?')}亿")
    else:
        print("❌ HKEX数据不可用 (可能需翻墙或Playwright未安装)")
