#!/usr/bin/env python3
"""
沪深港通板块流向聚合器.

从 hsgt_stock_daily 个股数据按行业（INDUSTRY 字段 / stock_basic 名称关键词 fallback）
聚合为板块级别流向，写入 hsgt_sector_daily 表。

数据流:
  hsgt_stock_daily (个股) → 按行业聚合 → hsgt_sector_daily (板块)

聚合字段:
  - trade_date, direction, sector (行业名称)
  - total_net_buy: SUM(estimated_net_buy) 行业净买入
  - total_net_buy_pos_sum: 正向买入合计 (estimated_net_buy > 0)
  - total_net_buy_neg_sum: 负向卖出合计 (estimated_net_buy < 0)
  - stock_count: 行业中个股数量
  - buy_count: 买入股票数
  - sell_count: 卖出股票数
  - top_buy_symbol: estimated_net_buy 最大的股票代码
  - top_buy_name: 对应名称
  - top_buy_value: 对应数值
  - top_sell_symbol: estimated_net_buy 最小的股票代码
  - top_sell_name: 对应名称
  - top_sell_value: 对应数值

采集频率：每日收盘后，由看门狗调度。
"""

import sys
import os
import time
from datetime import datetime, date, timedelta
from typing import Optional

# ── 路径 ────────────────────────────────────────────────
PROJECT_DIR = "~/code/stock-screener"
sys.path.insert(0, PROJECT_DIR)

from nous.data.collectors import resilient_fetch, CircuitBreaker, heartbeat
from nous.data.storage import get_db, with_retry

# 进程名
PROCESS_NAME = "hsgt_sector_aggregator"

# ── 行业关键词映射（fallback：当 stock_basic 无行业信息时使用名称匹配）──
# 从 EM API 的 INDUSTRY 字段中观察到的行业列表
KEYWORD_INDUSTRY_MAP = [
    ("银行", "银行"),
    ("证券", "证券"),
    ("保险", "保险"),
    ("半导体", "半导体"),
    ("医药", "医药生物"),
    ("医疗", "医药生物"),
    ("生物", "医药生物"),
    ("新能源", "新能源"),
    ("光伏", "新能源"),
    ("太阳能", "新能源"),
    ("锂电", "新能源"),
    ("宁德时代", "电力设备"),
    ("汽车", "汽车"),
    ("新能源车", "汽车"),
    ("锂电池", "新能源"),
    ("白酒", "食品饮料"),
    ("食品", "食品饮料"),
    ("饮料", "食品饮料"),
    ("茅台", "食品饮料"),
    ("五粮液", "食品饮料"),
    ("消费", "大消费"),
    ("家电", "家用电器"),
    ("电器", "家用电器"),
    ("房地产", "房地产"),
    ("地产", "房地产"),
    ("煤炭", "煤炭"),
    ("石油", "石油石化"),
    ("石化", "石油石化"),
    ("化工", "化工"),
    ("有色", "有色金属"),
    ("钢铁", "钢铁"),
    ("建材", "建材"),
    ("建筑", "建筑装饰"),
    ("基建", "建筑装饰"),
    ("机械", "机械设备"),
    ("军工", "国防军工"),
    ("国防", "国防军工"),
    ("通信", "通信"),
    ("5G", "通信"),
    ("电子", "电子"),
    ("芯片", "电子"),
    ("计算机", "计算机"),
    ("软件", "计算机"),
    ("互联网", "传媒"),
    ("传媒", "传媒"),
    ("游戏", "传媒"),
    ("农业", "农林牧渔"),
    ("猪肉", "农林牧渔"),
    ("养殖", "农林牧渔"),
    ("电力", "公用事业"),
    ("公用事业", "公用事业"),
    ("环保", "公用事业"),
    ("交通运输", "交通运输"),
    ("航空", "交通运输"),
    ("物流", "交通运输"),
    ("港口", "交通运输"),
    ("纺织", "纺织服装"),
    ("服装", "纺织服装"),
    ("轻工", "轻工制造"),
    ("造纸", "轻工制造"),
    ("商贸", "商贸零售"),
    ("零售", "商贸零售"),
    ("社会服务", "社会服务"),
    ("旅游", "社会服务"),
    ("酒店", "社会服务"),
    ("教育", "社会服务"),
    ("综合", "综合"),
]


def _match_industry_by_name(name: str) -> str:
    """通过股票名称关键词匹配行业（fallback）。"""
    if not name:
        return "其他"
    for keyword, industry in KEYWORD_INDUSTRY_MAP:
        if keyword in name:
            return industry
    return "其他"


# ══════════════════════════════════════════════════════════
#  数据获取
# ══════════════════════════════════════════════════════════

def fetch_stock_daily(trade_date: str) -> list[dict]:
    """从 hsgt_stock_daily 读取当日个股数据。"""
    conn = get_db(write=False)
    try:
        rows = conn.execute(
            """SELECT trade_date, symbol, direction, rank, net_inflow, change_pct,
                      estimated_net_buy, estimated_net_buy_direction,
                      holding_market_cap, holding_pct, confidence, industry
               FROM hsgt_stock_daily
               WHERE trade_date = ?
               ORDER BY direction, ABS(estimated_net_buy) DESC""",
            (trade_date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fetch_stock_basic_info() -> dict:
    """读取 stock_basic 表获取股票名称映射 {symbol: name}。"""
    conn = get_db(write=False)
    try:
        rows = conn.execute(
            "SELECT symbol, name FROM stock_basic"
        ).fetchall()
        return {r["symbol"]: r["name"] for r in rows}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════
#  行业聚合
# ══════════════════════════════════════════════════════════

def get_industry_for_stock(
    record: dict,
    stock_names: dict,
) -> str:
    """获取股票的行业分类。
    
    优先级:
      1. hsgt_stock_daily.industry (来自 EM API)
      2. stock_basic.name 关键词匹配 (fallback)
      3. 其他
    """
    # 1) 优先使用 API 返回的 INDUSTRY
    industry = record.get("industry")
    if industry and isinstance(industry, str) and industry.strip() and industry.strip() != "None":
        return industry.strip()

    # 2) fallback: 通过名称关键词匹配
    symbol = record.get("symbol", "")
    name = stock_names.get(symbol, "")
    if name:
        matched = _match_industry_by_name(name)
        if matched != "其他":
            return matched

    return "其他"


def aggregate_by_sector(
    records: list[dict],
    stock_names: dict,
) -> list[dict]:
    """按行业聚合个股数据为板块流向。"""
    from collections import defaultdict

    # 按 (direction, sector) 分组
    groups = defaultdict(lambda: {
        "estimated_net_buys": [],
        "records": [],
    })

    for r in records:
        direction = r.get("direction", "")
        sector = get_industry_for_stock(r, stock_names)
        key = (direction, sector)

        net_buy = r.get("estimated_net_buy")
        if net_buy is not None:
            groups[key]["estimated_net_buys"].append(net_buy)
        groups[key]["records"].append(r)

    results = []
    for (direction, sector), data in groups.items():
        net_buys = data["estimated_net_buys"]
        recs = data["records"]

        total_net_buy = sum(net_buys) if net_buys else 0.0
        pos_sum = sum(v for v in net_buys if v and v > 0)
        neg_sum = sum(v for v in net_buys if v and v < 0)
        stock_count = len(recs)
        buy_count = sum(1 for v in net_buys if v and v > 0)
        sell_count = sum(1 for v in net_buys if v and v < 0)

        # TOP买入/卖出
        sorted_recs = sorted(recs, key=lambda x: x.get("estimated_net_buy") or 0, reverse=True)
        top_buy = sorted_recs[0] if sorted_recs else None
        top_sell = sorted_recs[-1] if sorted_recs else None

        trade_date = recs[0].get("trade_date", "")

        results.append({
            "trade_date": trade_date,
            "direction": direction,
            "sector": sector,
            "total_net_buy": round(total_net_buy, 2),
            "total_net_buy_pos_sum": round(pos_sum, 2),
            "total_net_buy_neg_sum": round(neg_sum, 2),
            "stock_count": stock_count,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "top_buy_symbol": top_buy.get("symbol", "") if top_buy else "",
            "top_buy_name": stock_names.get(top_buy.get("symbol", ""), "") if top_buy else "",
            "top_buy_value": round(top_buy.get("estimated_net_buy") or 0, 2) if top_buy else 0.0,
            "top_sell_symbol": top_sell.get("symbol", "") if top_sell else "",
            "top_sell_name": stock_names.get(top_sell.get("symbol", ""), "") if top_sell else "",
            "top_sell_value": round(top_sell.get("estimated_net_buy") or 0, 2) if top_sell else 0.0,
        })

    # 按 direction, total_net_buy 降序排列
    results.sort(key=lambda x: (x["direction"], -abs(x["total_net_buy"])))
    return results


# ══════════════════════════════════════════════════════════
#  数据库操作
# ══════════════════════════════════════════════════════════

HSGT_SECTOR_DDL = """
CREATE TABLE IF NOT EXISTS hsgt_sector_daily (
    trade_date TEXT NOT NULL,
    direction TEXT NOT NULL,
    sector TEXT NOT NULL,
    total_net_buy REAL,
    total_net_buy_pos_sum REAL,
    total_net_buy_neg_sum REAL,
    stock_count INTEGER,
    buy_count INTEGER,
    sell_count INTEGER,
    top_buy_symbol TEXT,
    top_buy_name TEXT,
    top_buy_value REAL,
    top_sell_symbol TEXT,
    top_sell_name TEXT,
    top_sell_value REAL,
    PRIMARY KEY (trade_date, direction, sector)
);
"""


def ensure_table(conn):
    """确保 hsgt_sector_daily 表存在。"""
    conn.executescript(HSGT_SECTOR_DDL)
    conn.commit()


@with_retry(max_attempts=3)
def save_sector_flow(records: list[dict]) -> int:
    """写入 hsgt_sector_daily 表。"""
    if not records:
        return 0
    conn = get_db(write=True)
    ensure_table(conn)

    sql = """INSERT OR REPLACE INTO hsgt_sector_daily
(trade_date, direction, sector, total_net_buy,
 total_net_buy_pos_sum, total_net_buy_neg_sum,
 stock_count, buy_count, sell_count,
 top_buy_symbol, top_buy_name, top_buy_value,
 top_sell_symbol, top_sell_name, top_sell_value)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    count = 0
    try:
        for r in records:
            try:
                conn.execute(sql, (
                    r.get("trade_date"), r.get("direction"), r.get("sector"),
                    r.get("total_net_buy"),
                    r.get("total_net_buy_pos_sum"), r.get("total_net_buy_neg_sum"),
                    r.get("stock_count"), r.get("buy_count"), r.get("sell_count"),
                    r.get("top_buy_symbol"), r.get("top_buy_name"),
                    r.get("top_buy_value"),
                    r.get("top_sell_symbol"), r.get("top_sell_name"),
                    r.get("top_sell_value"),
                ))
                count += 1
            except Exception as e:
                print("  [写入] 跳过 %s/%s: %s" %
                      (r.get("direction"), r.get("sector"), e), file=sys.stderr)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return count


# ══════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════

def get_target_date() -> str:
    """获取目标日期：昨日（交易日）。
    
    也可通过 --date=YYYY-MM-DD 或 --date YYYY-MM-DD 命令行参数指定。
    """
    for arg in sys.argv:
        if arg.startswith("--date="):
            return arg.split("=", 1)[1]
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    # 周末跳过：取最近的星期五
    d = date.today() - timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def main() -> bool:
    """一次聚合完整流程，返回是否全部成功。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 50)
    print("[%s] hsgt_sector_aggregator 启动" % ts)
    print("=" * 50)

    heartbeat(PROCESS_NAME)

    trade_date = get_target_date()
    print("  目标日期: %s" % trade_date)

    errors = []
    saved_count = 0

    # 1) 读取个股数据
    print("\n--- 读取 hsgt_stock_daily ---")
    try:
        records = fetch_stock_daily(trade_date)
        print("  [读取] %d 条记录" % len(records))
        if not records:
            print("  [警告] 当日无个股数据，无法聚合")
    except Exception as e:
        print("  [读取] 异常: %s" % e, file=sys.stderr)
        errors.append("fetch: %s" % e)
        heartbeat(PROCESS_NAME)
        return False

    # 2) 读取 stock_basic 名称映射
    print("\n--- 读取 stock_basic ---")
    try:
        stock_names = fetch_stock_basic_info()
        print("  [读取] %d 只股票名称" % len(stock_names))
    except Exception as e:
        print("  [读取] 异常: %s" % e, file=sys.stderr)
        stock_names = {}

    # 3) 按行业聚合
    print("\n--- 行业聚合 ---")
    try:
        sector_records = aggregate_by_sector(records, stock_names)
        print("  [聚合] %d 个行业板块" % len(sector_records))

        for sec in sector_records[:10]:
            print("    %s [%s] 净买 %.2f | %d只 | 买入%d 卖出%d" % (
                sec["direction"], sec["sector"], sec["total_net_buy"],
                sec["stock_count"], sec["buy_count"], sec["sell_count"],
            ))
        if len(sector_records) > 10:
            print("    ... 共 %d 个板块" % len(sector_records))
    except Exception as e:
        print("  [聚合] 异常: %s" % e, file=sys.stderr)
        errors.append("aggregate: %s" % e)

    # 4) 写入数据库
    if sector_records and not errors:
        try:
            saved_count = save_sector_flow(sector_records)
            print("  [写入] %d 条 (hsgt_sector_daily)" % saved_count)
        except Exception as e:
            print("  [写入] 异常: %s" % e, file=sys.stderr)
            errors.append("save: %s" % e)

    # 最终报告
    print("\n--- 完成 ---")
    if saved_count > 0:
        print("  写入总量: %d 条 (hsgt_sector_daily)" % saved_count)
    else:
        print("  无数据写入")
    if errors:
        print("  错误: %d 个" % len(errors))
        for e in errors:
            print("    - %s" % e)

    heartbeat(PROCESS_NAME)
    return len(errors) == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
