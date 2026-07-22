"""港股做空数据采集 — Playwright浏览器渲染方案

HKEX于2025年将做空数据从CSV迁移到JS动态渲染页面。
唯一可靠方案: Playwright headless Chrome + Clash代理。

用法:
  python hk_short_selling.py                    # 全量采集
  python hk_short_selling.py --dry-run          # 验证可用性
  python hk_short_selling.py --symbols 00700,09988  # 指定标的

数据源: HKEX Short Selling Turnover Today
页面: https://www.hkex.com.hk/Market-Data/Statistics/Securities-Market/Short-Selling-Turnover-Today?sc_lang=en
"""

from __future__ import annotations
import sys, os, json, re, time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ═══════════ Playwright 浏览器引擎 ═══════════

def _get_page_with_retry(max_retries=2):
    """启动Playwright浏览器，返回page对象"""
    from playwright.sync_api import sync_playwright
    
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--proxy-server=http://127.0.0.1:7897',  # Clash proxy
        ]
    )
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        locale='en-US',
    )
    page = context.new_page()
    return p, browser, context, page


def _extract_short_selling_table(page) -> list[dict]:
    """从HKEX页面提取做空数据表格
    
    HKEX页面结构: 
    - 主板做空成交(today up to day close) — 表格含 Stock Code, Stock Name, 
      Short Selling Volume, Short Selling Turnover, % of Turnover
    """
    results = []
    
    try:
        # 等待表格加载 (HKEX页面用JS渲染)
        page.wait_for_selector('table', timeout=15000)
        time.sleep(2)  # 等待JS完成渲染
        
        # 提取所有表格行
        rows = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            const allRows = [];
            for (const table of tables) {
                const trs = table.querySelectorAll('tr');
                for (const tr of trs) {
                    const tds = tr.querySelectorAll('td, th');
                    const row = [];
                    for (const td of tds) {
                        row.push(td.innerText.trim());
                    }
                    if (row.length >= 4) allRows.push(row);
                }
            }
            return allRows;
        }""")
        
        today_str = date.today().isoformat()
        
        for row in rows:
            # 过滤: 找包含5位数字代码的行
            code_match = re.search(r'\b(\d{5})\b', row[0] if row else '')
            if not code_match:
                # 也可能代码在其他列
                for cell in row:
                    code_match = re.search(r'\b(\d{5})\b', cell)
                    if code_match:
                        break
                if not code_match:
                    continue
            
            code = code_match.group(1)
            
            # 查找名称(非数字、有一定长度的文本)
            name = ''
            for cell in row:
                if not re.match(r'^[\d,.%\- ]+$', cell) and len(cell) >= 2 and len(cell) <= 50:
                    name = cell
                    break
            
            # 查找数字字段 (做空成交量/成交额/占比)
            numbers = []
            for cell in row:
                val = re.sub(r'[,\s]', '', cell)
                try:
                    num = float(val.replace('%', ''))
                    numbers.append(num)
                except ValueError:
                    pass
            
            if len(numbers) >= 2:
                # 通常结构: [代码, 名称, 做空股数, 做空金额, 占比]
                short_volume = int(numbers[0]) if numbers else 0
                short_amount = numbers[1] if len(numbers) > 1 else 0
                short_ratio = numbers[2] if len(numbers) > 2 else 0
                
                results.append({
                    'symbol': code,
                    'name': name or code,
                    'short_volume': short_volume,
                    'short_amount': short_amount,
                    'short_ratio': short_ratio,
                    'date': today_str,
                })
    
    except Exception as e:
        print(f"  [Playwright] 提取表格失败: {e}")
    
    return results


def fetch_hk_short_selling_playwright(symbols: list = None) -> list[dict]:
    """通过Playwright浏览器获取HKEX做空数据
    
    Args:
        symbols: 可选，指定标的列表。None=全量
    
    Returns:
        [{'symbol':'00700', 'name':'TENCENT', 'short_volume':..., 'short_ratio':..., ...}, ...]
    """
    print("  [Playwright] 启动浏览器...")
    p = browser = context = page = None
    
    try:
        p, browser, context, page = _get_page_with_retry()
        
        url = 'https://www.hkex.com.hk/Market-Data/Statistics/Securities-Market/Short-Selling-Turnover-Today?sc_lang=en'
        print(f"  [Playwright] 加载HKEX做空页面...")
        page.goto(url, wait_until='networkidle', timeout=30000)
        
        data = _extract_short_selling_table(page)
        
        if symbols:
            data = [d for d in data if d['symbol'] in symbols]
        
        print(f"  [Playwright] 获取到 {len(data)} 条做空数据")
        return data
    
    except ImportError:
        print("  [Playwright] playwright未安装: pip install playwright && playwright install chromium")
        return []
    except Exception as e:
        print(f"  [Playwright] 错误: {e}")
        return []
    finally:
        if page: page.close()
        if context: context.close()
        if browser: browser.close()
        if p: p.stop()


# ═══════════ 轻量回退 ═══════════

def _fetch_from_sina_v3() -> list[dict] | None:
    """Sina做空API — 快速尝试(可能已失效)"""
    import requests as req
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://stock.finance.sina.com.cn/hkstock/",
    }
    urls = [
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHKStockData?page=1&num=200&sort=amount&asc=0&node=shortsell",
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHKStockData?page=1&num=200&sort=short_ratio&asc=0&node=shortsell",
    ]
    for url in urls:
        try:
            r = req.get(url, headers=headers, timeout=10)
            text = r.text.strip().strip(";")
            if text.startswith("[") and text != "[]":
                data = json.loads(text)
                if data and isinstance(data, list) and len(data) > 0:
                    results = []
                    for item in data:
                        code = str(item.get("code","")).zfill(5)
                        name = str(item.get("name",""))
                        sr = item.get("short_ratio") or item.get("shortSellRatio")
                        if code and sr is not None:
                            results.append({
                                "symbol": code, "name": name,
                                "short_ratio": float(str(sr).replace("%","")),
                                "short_volume": 0, "short_amount": 0,
                                "date": date.today().isoformat()
                            })
                    if results:
                        print(f"  [Sina v3] 获取到 {len(results)} 条")
                        return results
        except Exception:
            continue
    return None


def fetch_hk_short_selling(symbols: list = None) -> list[dict]:
    """统一入口: 多源降级获取港股做空数据
    
    优先级:
    1. Sina API (快, 免费, 可能失败)
    2. Playwright (慢, 需要代理, 最可靠)
    3. 全失败返回空列表
    """
    # L1: Sina快速尝试
    data = _fetch_from_sina_v3()
    if data:
        return data
    
    # L2: Playwright浏览器渲染
    print("  [港股做空] Sina不可用，切换到Playwright...")
    data = fetch_hk_short_selling_playwright(symbols)
    if data:
        return data
    
    print("  [港股做空] ⚠️ 所有数据源不可用")
    return []


# ═══════════ CLI ═══════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="港股做空数据采集")
    parser.add_argument("--dry-run", action="store_true", help="验证可用性")
    parser.add_argument("--symbols", type=str, help="指定标的，逗号分隔")
    args = parser.parse_args()
    
    print("=" * 50)
    print("  港股做空数据采集 v3 (Playwright)")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    symbols = args.symbols.split(",") if args.symbols else None
    
    if args.dry_run:
        print("[Dry-run] 验证数据源...")
        # 快速测试Sina
        data = _fetch_from_sina_v3()
        if data:
            print(f"[Dry-run] ✅ Sina可用: {len(data)}条")
        else:
            print("[Dry-run] ⚠️ Sina不可用, Playwright需要代理")
            print("[Dry-run] 确认Clash代理在7897端口运行")
            try:
                import requests
                r = requests.get("http://127.0.0.1:7897", timeout=3)
                print("[Dry-run] ✅ Clash代理可达")
            except:
                print("[Dry-run] ❌ Clash代理不可达")
    else:
        data = fetch_hk_short_selling(symbols)
        if data:
            print(f"\n获取到 {len(data)} 条做空数据:")
            for row in data[:20]:
                print(f"  {row['symbol']} {row.get('name',''):<12s} "
                      f"做空率={row.get('short_ratio',0):.1f}% "
                      f"股数={row.get('short_volume',0):,}")
        else:
            print("\n未获取到做空数据")
