"""港股通数据获取：akshare 日线 + 动态标的列表 + 流动性分档"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

from nous.data import storage


# ── 港股通主要标的（静态列表，作为动态获取的最终回退）──
# 覆盖恒生指数成分 + 主要港股通标的，共 ~80 只
GGT_SYMBOLS = [
    # 科技互联网
    ("00700", "腾讯控股"), ("09988", "阿里巴巴-SW"), ("09999", "网易-S"),
    ("03690", "美团-W"), ("09618", "京东集团-SW"), ("09888", "百度集团-SW"),
    ("01810", "小米集团-W"), ("01024", "快手-W"), ("02015", "理想汽车-W"),
    ("09866", "蔚来-SW"), ("09868", "小鹏汽车-W"), ("09961", "携程集团-S"),
    # 金融
    ("00388", "香港交易所"), ("01299", "友邦保险"), ("01398", "工商银行"),
    ("03988", "中国银行"), ("01288", "农业银行"), ("00939", "建设银行"),
    ("02628", "中国人寿"), ("02318", "中国平安"), ("03968", "招商银行"),
    # 消费
    ("02020", "安踏体育"), ("02331", "李宁"), ("09633", "农夫山泉"),
    ("06862", "海底捞"), ("09987", "百胜中国"), ("09626", "哔哩哔哩-W"),
    # 能源/资源
    ("00883", "中国海洋石油"), ("00857", "中国石油股份"),
    ("01088", "中国神华"), ("01171", "兖矿能源"),
    # 医药
    ("02269", "药明生物"), ("06160", "百济神州"), ("01801", "信达生物"),
    ("01177", "中国生物制药"),
    # 地产/综合
    ("00016", "新鸿基地产"), ("00001", "长和"), ("01113", "长实集团"),
    ("00027", "银河娱乐"), ("01928", "金沙中国有限公司"),
    # 汽车/工业
    ("00175", "吉利汽车"), ("02382", "舜宇光学科技"), ("01211", "比亚迪股份"),
    # 电信
    ("00941", "中国移动"), ("00728", "中国电信"), ("00762", "中国联通"),
    # 其他蓝筹
    ("00005", "汇丰控股"), ("00011", "恒生银行"), ("00288", "万洲国际"),
    ("00669", "创科实业"), ("01038", "长江基建集团"), ("00002", "中电控股"),
    ("00003", "香港中华煤气"), ("00006", "电能实业"), ("00066", "港铁公司"),
    ("00267", "中信股份"), ("00291", "华润啤酒"), ("00823", "领展房产基金"),
    ("00992", "联想集团"), ("01093", "石药集团"), ("01929", "周大福"),
    ("02319", "蒙牛乳业"), ("02388", "中银香港"), ("02688", "新奥能源"),
]


# ── 工具函数 ─────────────────────────────────

def _get_ggt_from_akshare() -> Optional[list[tuple[str, str]]]:
    """尝试从 akshare 动态获取港股通成分股"""
    try:
        import akshare as ak  # noqa: F811 — late import for availability
        df = ak.stock_hk_ggt_components_em()
        # 列名: 序号, 代码, 名称, ...
        symbols = []
        for _, row in df.iterrows():
            code = str(row["代码"]).strip()
            name = str(row["名称"]).strip()
            if code:
                # 确保 5 位数字代码
                code = code.zfill(5)
                symbols.append((code, name))
        if symbols:
            print(f"  [akshare] 获取到 {len(symbols)} 只港股通成分股")
            return symbols
    except Exception:
        pass
    return None


def _get_ggt_from_em_direct() -> Optional[list[tuple[str, str]]]:
    """尝试通过 East Money API 直接获取港股通成分股（akshare 不可用时的回退）"""
    try:
        url = "https://33.push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "500",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "fid": "f12",
            "fs": "b:DLMK0146,b:DLMK0144",
            "fields": "f12,f14",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://quote.eastmoney.com/",
        }
        r = requests.get(url, params=params, headers=headers, timeout=15)
        data = r.json()
        items = data.get("data", {}).get("diff", [])
        if items:
            symbols = []
            for item in items:
                code = str(item.get("f12", "")).strip().zfill(5)
                name = str(item.get("f14", "")).strip()
                if code:
                    symbols.append((code, name))
            print(f"  [EM 直连] 获取到 {len(symbols)} 只港股通成分股")
            return symbols
    except Exception:
        pass
    return None


def _get_ggt_from_sina() -> Optional[list[tuple[str, str]]]:
    """尝试通过 Sina 港股通列表页面获取"""
    try:
        url = (
            "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHKStockData"
        )
        params = {
            "page": "1",
            "num": "1000",
            "sort": "symbol",
            "asc": "1",
            "node": "ggt",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://finance.sina.com.cn/",
        }
        r = requests.get(url, params=params, headers=headers, timeout=15)
        text = r.text.strip().strip(";")
        import json
        data = json.loads(text)
        if data and isinstance(data, list):
            symbols = []
            for item in data:
                code = str(item.get("code", "")).strip().zfill(5)
                name = str(item.get("name", "")).strip()
                if code:
                    symbols.append((code, name))
            if symbols:
                print(f"  [Sina] 获取到 {len(symbols)} 只港股通成分股")
                return symbols
    except Exception:
        pass
    return None


def _resolve_ggt_list() -> list[tuple[str, str]]:
    """
    多层次获取港股通标的列表：
    1. akshare → stock_hk_ggt_components_em()
    2. East Money API 直连
    3. Sina 港股通列表 API
    4. 硬编码 GGT_SYMBOLS（最终回退）
    """
    attempts = [
        ("akshare", _get_ggt_from_akshare),
        ("EM直连", _get_ggt_from_em_direct),
        ("Sina", _get_ggt_from_sina),
    ]
    for name, func in attempts:
        result = func()
        if result:
            return result
        print(f"  [{name}] 不可用，尝试下一个源...")

    print(f"  所有动态源均失效，回退到静态列表 ({len(GGT_SYMBOLS)} 只)")
    return GGT_SYMBOLS


def update_stock_list():
    """写入港股通标的到 SQLite（支持动态刷新）"""
    symbol_list = _resolve_ggt_list()
    existing = {s["symbol"] for s in storage.list_symbols("hk")}
    new_stocks = [(sym, name, "hk") for sym, name in symbol_list if sym not in existing]
    if new_stocks:
        storage.upsert_stocks(new_stocks)
        print(f"  新增 {len(new_stocks)} 只港股")
    else:
        print(f"  港股列表已是最新（共 {len(symbol_list)} 只）")


# ── 日线数据 ──────────────────────────────────

def fetch_daily(symbol: str, days: int = 120, use_polars: bool = False):
    """
    获取单只港股日线（前复权）。
    数据源：akshare stock_hk_daily（新浪源，无代理问题）

    当 use_polars=True 时返回 pl.DataFrame，否则返回 pd.DataFrame。
    """
    import akshare as ak  # late import — akshare may not be in all envs

    df = ak.stock_hk_daily(symbol=symbol, adjust="qfq")

    if use_polars:
        from nous.data.etl.polars_pipeline import daily_to_polars
        pl_df = daily_to_polars(df).tail(days)
        return pl_df
    else:
        df = df.rename(columns={
            "date": "trade_date",
        })
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df[["trade_date", "open", "high", "low", "close", "volume", "amount"]]
        return df.tail(days)


def update_all_daily(full: bool = False, cfg: Optional[dict] = None):
    """批量更新港股日线（使用 Polars ETL 加速）"""
    batch_size = cfg.get("runtime", {}).get("update_batch_size", 10) if cfg else 10
    sleep_sec = cfg.get("runtime", {}).get("update_sleep_sec", 3.0) if cfg else 3.0

    symbols = [s["symbol"] for s in storage.list_symbols("hk")]
    total = len(symbols)
    if total == 0:
        print("  港股列表为空，请先运行 update_stock_list")
        return

    print(f"  共 {total} 只港股，批量大小={batch_size}，间隔={sleep_sec}s")
    updated = 0
    skipped = 0
    failed = 0

    import akshare as ak  # late import
    conn = storage.get_db()
    from nous.data.etl.polars_pipeline import clean_and_store

    for i in range(0, total, batch_size):
        batch = symbols[i : i + batch_size]
        for sym in batch:
            try:
                if not full:
                    latest = storage.get_latest_date(sym)
                    today = date.today()
                    if latest and latest >= today - timedelta(days=1):
                        skipped += 1
                        continue

                raw_df = ak.stock_hk_daily(symbol=sym, adjust="qfq")
                clean_and_store(raw_df, sym, conn)
                updated += 1
            except Exception as e:
                failed += 1
                if failed <= 3:
                    print(f"  [{sym}] 失败: {e}")

        done = min(i + batch_size, total)
        if updated > 0 or failed > 0:
            print(f"  进度: {done}/{total}  已更新={updated}  跳过={skipped}  失败={failed}")
        time.sleep(sleep_sec)

    conn.close()
    print(f"  完成: 更新={updated} 跳过={skipped} 失败={failed}")


# ── 流动性分档 ──────────────────────────────


def get_hk_liquidity_tier(symbol: str) -> str:
    """
    返回流动性分档:
    - 'high': 日均成交 > 1亿港币
    - 'medium': 1000万-1亿
    - 'low': 100万-1000万
    - 'illiquid': < 100万 (交易风险极高)

    从 screener.db stock_daily 表取近20日成交额计算日均值。
    若数据不足，返回 'unknown'。
    """
    try:
        daily_data = storage.get_daily(symbol, limit=20)
        if not daily_data:
            return "unknown"

        amounts = [d.get("amount") for d in daily_data if d.get("amount") is not None]
        if len(amounts) < 5:
            return "unknown"

        avg_amount = sum(amounts) / len(amounts)

        if avg_amount > 100_000_000:  # > 1亿 HKD
            return "high"
        elif avg_amount >= 10_000_000:  # 1000万 - 1亿
            return "medium"
        elif avg_amount >= 1_000_000:  # 100万 - 1000万
            return "low"
        else:
            return "illiquid"
    except Exception:
        return "unknown"


def batch_liquidity_tiers(symbols: list[str]) -> dict[str, str]:
    """批量查询流动性分档，返回 {symbol: tier}"""
    result = {}
    for sym in symbols:
        result[sym] = get_hk_liquidity_tier(sym)
    return result


# ── 基本面数据 ──────────────────────────────

def fetch_hk_fundamental_indicator(symbol: str) -> dict | None:
    """
    从 East Money 获取港股财务指标。
    数据源: ak.stock_hk_financial_indicator_em (push2 后端，需节流)

    返回: {pe, pb, roe, eps, dividend_yield, total_mv} 或 None
    """
    try:
        import akshare as ak
        df = ak.stock_hk_financial_indicator_em(symbol=symbol)
        if df.empty:
            return None
        row = df.iloc[0]
        pe_val = row.get("市盈率")
        pb_val = row.get("市净率")
        roe_val = row.get("股东权益回报率(%)")
        eps_val = row.get("基本每股收益(元)")
        dy_val = row.get("股息率TTM(%)")
        mv_val = row.get("总市值(港元)")

        result = {}
        # PE: 正数才有效(排除负PE亏损股,保留为None让screener处理)
        if pe_val is not None and (isinstance(pe_val, (int, float)) and pe_val > 0):
            result["pe"] = round(float(pe_val), 1)
        if pb_val is not None and (isinstance(pb_val, (int, float)) and pb_val > 0):
            result["pb"] = round(float(pb_val), 2)
        if roe_val is not None:
            result["roe"] = round(float(roe_val), 1)
        if eps_val is not None:
            result["eps"] = float(eps_val)
        if dy_val is not None and dy_val > 0:
            result["dividend_yield"] = round(float(dy_val), 2)
        if mv_val is not None and mv_val > 0:
            result["total_mv"] = float(mv_val)

        return result if result else None
    except Exception:
        return None


def sync_hk_fundamentals(symbol: str, sleep_sec: float = 0.5):
    """
    同步单只港股基本面到 screener.db stock_fundamental 表。
    """
    data = fetch_hk_fundamental_indicator(symbol)
    if not data:
        return False

    from datetime import date
    storage.upsert_fundamentals([{
        "symbol": symbol,
        "pe": data.get("pe"),
        "pe_static": None,  # EM 源只提供混合PE，不区分TTM/静态
        "pe_dynamic": None,
        "pb": data.get("pb"),
        "roe": data.get("roe"),
        "dividend_yield": data.get("dividend_yield"),
        "debt_ratio": None,
        "total_mv": data.get("total_mv"),
        "snapshot_date": str(date.today()),
    }])

    if sleep_sec:
        time.sleep(sleep_sec)
    return True


def sync_all_hk_fundamentals(sleep_sec: float = 1.5):
    """
    批量同步所有港股基本面。EM push2 有 IP 限流，间隔≥1.5s。
    """
    symbols = [s["symbol"] for s in storage.list_symbols("hk")]
    total = len(symbols)
    if total == 0:
        print("  港股列表为空，请先运行 update_stock_list")
        return

    print(f"  共 {total} 只港股，间隔={sleep_sec}s")
    ok = 0
    fail = 0
    for i, sym in enumerate(symbols):
        try:
            if sync_hk_fundamentals(sym, sleep_sec=0):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            if fail <= 3:
                print(f"  [{sym}] 失败: {e}")
        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{total}  成功={ok}  失败={fail}")
    print(f"  完成: 成功={ok} 失败={fail}")
