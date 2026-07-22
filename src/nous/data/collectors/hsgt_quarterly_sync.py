#!/usr/bin/env python3
"""
hsgt_quarterly_sync — 沪深港通季度持股数据同步

逐只股票调用 akshare stock_hsgt_individual_em(symbol='XXXXXX')
拉取北向/南向个股持股明细, 增量追加至 hsgt_quarterly_holding 表。

数据源:
  - akshare stock_hsgt_individual_em(symbol='XXXXXX')
    返回列: 持股日期, 当日收盘价, 当日涨跌幅, 持股数量, 持股市值,
            持股数量占A股百分比, 今日增持股数, 今日增持资金, 今日持股市值变化

标的来源:
  - realtime_pool 表中所有股票
  - portfolio state.yaml 中实盘持仓

策略:
  - 只同步最新不存在的日期 (增量), 已存在的数据跳过
  - 每只股票 ~1s, 200 只约 3-4 分钟

自愈: resilient_fetch + CircuitBreaker + heartbeat
独立运行:
    python -m src.collectors.hsgt_quarterly_sync
"""

import sys
import os
import time
import json
import yaml
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

# ── 确保可以从 src 导入 ─────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd

from nous.data.collectors import resilient_fetch, CircuitBreaker, heartbeat
from nous.data.storage import get_db, with_retry

# ═════════════════════════════════════════════════════
#  常量
# ═════════════════════════════════════════════════════

PROCESS_NAME = "hsgt_quarterly_sync"

# 路径
PORTFOLIO_STATE = Path.home() / "wiki/finance/portfolio/state.yaml"

# 熔断器
AKSHARE_CB = CircuitBreaker("hsgt_q_akshare", failure_threshold=5, cooldown_seconds=120)
INDIVIDUAL_CB = CircuitBreaker("hsgt_q_individual", failure_threshold=3, cooldown_seconds=180)

# 每只股票间隔 (秒)
STOCK_SLEEP = 1.0

# ═════════════════════════════════════════════════════
#  表 DDL
# ═════════════════════════════════════════════════════

DDL_HSGT_QUARTERLY_HOLDING = """
CREATE TABLE IF NOT EXISTS hsgt_quarterly_holding (
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    holding_date TEXT NOT NULL,
    close_price REAL,
    holding_shares REAL,
    holding_market_cap REAL,
    holding_pct REAL,
    daily_shares_change REAL,
    daily_capital_change REAL,
    fetched_at TEXT,
    PRIMARY KEY (symbol, direction, holding_date)
);
"""


# ═════════════════════════════════════════════════════
#  辅助函数
# ═════════════════════════════════════════════════════

def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ═════════════════════════════════════════════════════
#  1. 获取标的列表
# ═════════════════════════════════════════════════════

def get_symbols_from_pool() -> list[str]:
    """从 realtime_pool 表获取活跃股票列表。"""
    conn = get_db(write=False)
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM realtime_pool WHERE active = 1"
        ).fetchall()
        symbols = [row["symbol"] for row in rows if row["symbol"]]
        print(f"  [pool] realtime_pool 取到 {len(symbols)} 只股票", file=sys.stderr)
        return symbols
    except Exception as e:
        print(f"  [pool] 查询失败: {e}", file=sys.stderr)
        return []
    finally:
        conn.close()


def get_symbols_from_portfolio() -> list[str]:
    """从 portfolio state.yaml 获取实盘持仓股票。"""
    if not PORTFOLIO_STATE.exists():
        print(f"  [portfolio] 文件不存在: {PORTFOLIO_STATE}", file=sys.stderr)
        return []

    try:
        with open(PORTFOLIO_STATE, "r") as f:
            state = yaml.safe_load(f)
        if not state:
            return []

        symbols = []
        # holdings 可能是 dict or list
        holdings = state.get("holdings", [])
        if isinstance(holdings, dict):
            for code in holdings.keys():
                symbols.append(str(code).strip())
        elif isinstance(holdings, list):
            for item in holdings:
                if isinstance(item, dict):
                    code = item.get("code", item.get("symbol", ""))
                else:
                    code = str(item)
                if code:
                    symbols.append(str(code).strip())

        # 去重
        symbols = list(set(s for s in symbols if s))
        print(f"  [portfolio] 取到 {len(symbols)} 只持仓股票", file=sys.stderr)
        return symbols
    except Exception as e:
        print(f"  [portfolio] 读取失败: {e}", file=sys.stderr)
        return []


def get_target_symbols() -> list[str]:
    """合并所有来源的标的列表, 去重后返回。"""
    symbols = set()

    for code in get_symbols_from_pool():
        symbols.add(code)

    for code in get_symbols_from_portfolio():
        symbols.add(code)

    # 过滤空字符串
    symbols = {s for s in symbols if s}

    result = sorted(symbols)
    print(f"  [target] 合并后共 {len(result)} 只标的", file=sys.stderr)
    return result


# ═════════════════════════════════════════════════════
#  2. 查询已存在日期 (用于增量判断)
# ═════════════════════════════════════════════════════

def get_existing_dates(symbol: str) -> set[str]:
    """查询 hsgt_quarterly_holding 中该股票已有的日期集合。"""
    conn = get_db(write=False)
    try:
        rows = conn.execute(
            "SELECT holding_date FROM hsgt_quarterly_holding WHERE symbol = ?",
            (symbol,),
        ).fetchall()
        return {row["holding_date"] for row in rows}
    except Exception as e:
        # 表可能还不存在
        return set()
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
#  3. 个股持股数据采集 (通过 akshare stock_hsgt_individual_em)
# ═════════════════════════════════════════════════════

def fetch_stock_holding(symbol: str, direction: str = "北向") -> pd.DataFrame:
    """
    使用 resilient_fetch 获取单只股票持股数据。
    
    Args:
        symbol: 股票代码 (6位)
        direction: "北向" 或 "南向"
    
    Returns:
        DataFrame with columns:
            持股日期, 当日收盘价, 当日涨跌幅, 持股数量, 持股市值,
            持股数量占A股百分比, 今日增持股数, 今日增持资金, 今日持股市值变化
    """
    def _fetch():
        import akshare as ak
        return ak.stock_hsgt_individual_em(symbol=symbol)

    result, status = resilient_fetch(
        "akshare", _fetch,
        fallback_fn=lambda: pd.DataFrame(),
        max_retries=3, base_delay=1.0,
    )

    if not status.get("success") or result is None:
        print(f"    [fetch] {symbol} 采集失败: {status.get('error', 'unknown')}",
              file=sys.stderr)
        return pd.DataFrame()

    if status.get("fallback_used"):
        print(f"    [fetch] {symbol} 使用降级数据", file=sys.stderr)

    return result


def parse_holding_records(df: pd.DataFrame, symbol: str, direction: str,
                           existing_dates: set[str]) -> list[dict]:
    """
    解析 DataFrame 为统一记录格式, 过滤已存在的日期 (增量)。
    
    Returns:
        [{symbol, direction, holding_date, close_price, holding_shares,
          holding_market_cap, holding_pct, daily_shares_change,
          daily_capital_change, fetched_at}, ...]
    """
    if df.empty:
        return []

    records = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 列名映射 (akshare 返回的列名)
    # 常见列名: 持股日期, 当日收盘价, 当日涨跌幅, 持股数量, 持股市值,
    #          持股数量占A股百分比, 今日增持股数, 今日增持资金, 今日持股市值变化
    col_date = next((c for c in df.columns if "日期" in c), None)
    col_close = next((c for c in df.columns if "收盘价" in c), None)
    col_shares = next((c for c in df.columns if "持股数量" in c and "占比" not in c), None)
    col_market_cap = next((c for c in df.columns if "持股市值" in c and "变化" not in c), None)
    col_pct = next((c for c in df.columns if "占比" in c or "百分比" in c), None)
    col_shares_change = next((c for c in df.columns if "增持股" in c), None)
    col_capital_change = next((c for c in df.columns if "增持资金" in c), None)
    col_price_change = next((c for c in df.columns if "涨跌幅" in c), None)

    if not col_date:
        print(f"    [parse] {symbol} 未找到日期列, 可用列: {list(df.columns)}",
              file=sys.stderr)
        return []

    for _, row in df.iterrows():
        try:
            holding_date = str(row.get(col_date, "")).strip()
            if not holding_date:
                continue

            # 标准化日期格式 (akshare 可能返回 "2024-01-15" 或 "2024-01-15 00:00:00")
            holding_date = holding_date[:10]

            # 增量过滤: 跳过已有日期
            if holding_date in existing_dates:
                continue

            records.append({
                "symbol": symbol,
                "direction": direction,
                "holding_date": holding_date,
                "close_price": _safe_float(row.get(col_close)),
                "holding_shares": _safe_float(row.get(col_shares)),
                "holding_market_cap": _safe_float(row.get(col_market_cap)),
                "holding_pct": _safe_float(row.get(col_pct)),
                "daily_shares_change": _safe_float(row.get(col_shares_change)),
                "daily_capital_change": _safe_float(row.get(col_capital_change)),
                "fetched_at": now_str,
            })
        except Exception as e:
            print(f"    [parse] {symbol} 行解析异常: {e}", file=sys.stderr)
            continue

    return records


# ═════════════════════════════════════════════════════
#  4. DB 写入
# ═════════════════════════════════════════════════════

@with_retry(max_attempts=3)
def save_holding_records(records: list[dict]) -> int:
    """批量写入 hsgt_quarterly_holding 表。"""
    if not records:
        return 0

    conn = get_db(write=True)
    try:
        # 建表
        conn.executescript(DDL_HSGT_QUARTERLY_HOLDING)

        sql = """
            INSERT OR IGNORE INTO hsgt_quarterly_holding
            (symbol, direction, holding_date, close_price,
             holding_shares, holding_market_cap, holding_pct,
             daily_shares_change, daily_capital_change, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        count = 0
        for r in records:
            try:
                conn.execute(sql, (
                    r.get("symbol"),
                    r.get("direction"),
                    r.get("holding_date"),
                    r.get("close_price"),
                    r.get("holding_shares"),
                    r.get("holding_market_cap"),
                    r.get("holding_pct"),
                    r.get("daily_shares_change"),
                    r.get("daily_capital_change"),
                    r.get("fetched_at"),
                ))
                count += 1
            except Exception as e:
                print(f"    [写入] 跳过 {r.get('symbol')}/{r.get('holding_date')}: {e}",
                      file=sys.stderr)
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
#  5. 主流程
# ═════════════════════════════════════════════════════

def main() -> bool:
    """一次完整同步流程。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 55)
    print(f"[{ts}] {PROCESS_NAME} 启动")
    print("=" * 55)

    # 心跳
    heartbeat(PROCESS_NAME)

    # ── Step 1: 获取标的列表 ──
    print("\n--- 1. 获取标的列表 ---")
    symbols = get_target_symbols()
    if not symbols:
        print("  [target] 无标的, 跳过本次同步", file=sys.stderr)
        heartbeat(PROCESS_NAME)
        return True

    # ── Step 2: 逐只采集 ──
    print(f"\n--- 2. 逐只采集持股数据 ({len(symbols)} 只) ---")

    total_new = 0
    errors = []
    success_count = 0

    for i, symbol in enumerate(symbols, start=1):
        print(f"\n  [{i}/{len(symbols)}] {symbol} ...", file=sys.stderr)

        try:
            # 2a. 查询已有日期
            existing = get_existing_dates(symbol)

            # 2b. 采集数据
            df = fetch_stock_holding(symbol)

            if df.empty:
                print(f"    [skip] 无数据", file=sys.stderr)
                success_count += 1
                time.sleep(STOCK_SLEEP)
                continue

            # 2c. 解析并增量过滤
            records = parse_holding_records(df, symbol, "北向", existing)
            # 也尝试南向方向 (akshare 接口可能同时返回)
            # 暂时只处理北向, 因为 stock_hsgt_individual_em 默认返回北向

            if not records:
                print(f"    [skip] 无新增数据 (已有 {len(existing)} 天)", file=sys.stderr)
                success_count += 1
                time.sleep(STOCK_SLEEP)
                continue

            # 2d. 写入数据库
            saved = save_holding_records(records)
            total_new += saved
            success_count += 1

            print(f"    ✓ 新增 {saved} 条 (累计 {total_new})", file=sys.stderr)

        except Exception as e:
            print(f"    ✗ 异常: {type(e).__name__}: {e}", file=sys.stderr)
            errors.append(f"{symbol}: {e}")

        # 速率控制
        time.sleep(STOCK_SLEEP)

        # 每 20 只心跳一次
        if i % 20 == 0:
            heartbeat(PROCESS_NAME)

    # ── 最终报告 ──
    print("\n" + "=" * 55)
    print("同步完成!")
    print(f"  标的处理: {success_count}/{len(symbols)}")
    print(f"  hsgt_quarterly_holding 新增: {total_new} 条")
    if errors:
        print(f"  错误 ({len(errors)}):")
        # 最多显示前 10 个错误
        for e in errors[:10]:
            print(f"    - {e}")
        if len(errors) > 10:
            print(f"    ... 及另外 {len(errors) - 10} 个错误")
    else:
        print("  ✓ 全部成功")

    # 心跳
    heartbeat(PROCESS_NAME)
    return len(errors) == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
