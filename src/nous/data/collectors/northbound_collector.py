#!/usr/bin/env python3
"""
northbound_collector — 北向资金盘中实时额度采集

每5min 通过 akshare 获取沪股通/深股通实时额度及当日资金余额，
写入 northbound_intraday 表。
每30min 汇总检查阈值: 半日累计净买入 >100亿 或 <-50亿 则打印告警。

数据源: akshare.stock_hsgt_fund_flow_summary_em()
  返回列: 交易日, 类型, 板块, 资金方向, 交易状态, 成交净买额,
          资金净流入, 当日资金余额, 上涨数, 持平数, 下跌数,
          相关指数, 指数涨跌幅
  北向资金行: 资金方向='北向', 板块='沪股通'/'深股通'
  交易状态: 3=已收盘, 1=交易中, 2=午间休市

自愈: resilient_fetch + CircuitBreaker + 指数退避重试
看门狗: heartbeat('northbound_collector')

独立运行 (持续采集循环):
    python -m src.collectors.northbound_collector

单次采集:
    python -m src.collectors.northbound_collector --once
"""

from __future__ import annotations

import sys
import os
import time
import sqlite3
from datetime import datetime

# ── 确保可以从 src 导入 ─────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd

from nous.data.collectors import resilient_fetch, heartbeat, collector_main_loop
from nous.data.storage import get_db

# ═════════════════════════════════════════════════════
#  常量
# ═════════════════════════════════════════════════════

SH_TOTAL_QUOTA = 520.0   # 沪股通总额度(亿)
SZ_TOTAL_QUOTA = 520.0   # 深股通总额度(亿)
ALERT_UPPER = 100.0      # 半日净买入 >100亿 告警
ALERT_LOWER = -50.0      # 半日净买入 <-50亿 告警
ALERT_EVERY_N = 6        # 每 6 次采集 (=30min) 检查一次阈值
TRADE_STATUS_CLOSED = 3  # 已收盘

# ═════════════════════════════════════════════════════
#  表 DDL
# ═════════════════════════════════════════════════════

DDL_NORTHBOUND_INTRADAY = """
CREATE TABLE IF NOT EXISTS northbound_intraday (
    datetime TEXT NOT NULL,
    sh_quota_used REAL,
    sz_quota_used REAL,
    sh_quota_remain REAL,
    sz_quota_remain REAL,
    sh_net_estimated REAL,
    sz_net_estimated REAL,
    PRIMARY KEY (datetime)
);
"""

# ═════════════════════════════════════════════════════
#  数据采集
# ═════════════════════════════════════════════════════

def fetch_northbound_flow() -> pd.DataFrame:
    """使用 resilient_fetch 获取北向资金实时额度 summary。"""
    def _fetch():
        import akshare
        return akshare.stock_hsgt_fund_flow_summary_em()

    result, status = resilient_fetch(
        'akshare', _fetch,
        fallback_fn=lambda: pd.DataFrame(),
        max_retries=3, base_delay=1.0,
    )
    if not status.get('success') or result is None:
        print(f"  [northbound] 获取失败: {status.get('error', 'unknown')}", file=sys.stderr)
        return pd.DataFrame()
    if status.get('fallback_used'):
        print(f"  [northbound] 使用降级数据", file=sys.stderr)
    return result


def parse_northbound_data(df: pd.DataFrame) -> dict | None:
    """从 DataFrame 解析沪股通/深股通额度与净买入。

    Returns:
        dict with keys: datetime, sh_quota_used, sz_quota_used,
                        sh_quota_remain, sz_quota_remain,
                        sh_net_estimated, sz_net_estimated, sh_status, sz_status
        或 None (解析失败)。
    """
    if df.empty:
        return None

    try:
        # 筛选北向资金记录
        nb = df[df['资金方向'] == '北向']
        if nb.empty:
            print(f"  [northbound] 未找到北向资金行", file=sys.stderr)
            return None

        sh_row = nb[nb['板块'] == '沪股通']
        sz_row = nb[nb['板块'] == '深股通']

        if sh_row.empty:
            print(f"  [northbound] 缺少沪股通行", file=sys.stderr)
            return None
        if sz_row.empty:
            print(f"  [northbound] 缺少深股通行", file=sys.stderr)
            return None

        sh = sh_row.iloc[0]
        sz = sz_row.iloc[0]

        sh_status = int(sh['交易状态'])
        sz_status = int(sz['交易状态'])

        sh_quota_remain = float(sh['当日资金余额']) if pd.notna(sh['当日资金余额']) else 0.0
        sz_quota_remain = float(sz['当日资金余额']) if pd.notna(sz['当日资金余额']) else 0.0

        sh_net = float(sh['成交净买额']) if pd.notna(sh['成交净买额']) else 0.0
        sz_net = float(sz['成交净买额']) if pd.notna(sz['成交净买额']) else 0.0

        # 计算已用额度
        #   当市场交易中且余额 > 0: 已用额度 = 总额度 - 余额
        #   当余额为 0 (收盘后): 用成交净买额近似
        if sh_quota_remain > 0 and sh_status != TRADE_STATUS_CLOSED:
            sh_quota_used = max(SH_TOTAL_QUOTA - sh_quota_remain, 0.0)
        else:
            sh_quota_used = sh_net if sh_net > 0 else 0.0

        if sz_quota_remain > 0 and sz_status != TRADE_STATUS_CLOSED:
            sz_quota_used = max(SZ_TOTAL_QUOTA - sz_quota_remain, 0.0)
        else:
            sz_quota_used = sz_net if sz_net > 0 else 0.0

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        return {
            'datetime': now_str,
            'sh_quota_used': round(sh_quota_used, 2),
            'sz_quota_used': round(sz_quota_used, 2),
            'sh_quota_remain': round(sh_quota_remain, 2),
            'sz_quota_remain': round(sz_quota_remain, 2),
            'sh_net_estimated': round(sh_net, 2),
            'sz_net_estimated': round(sz_net, 2),
            'sh_status': sh_status,
            'sz_status': sz_status,
        }
    except Exception as e:
        print(f"  [northbound] 数据解析失败: {type(e).__name__}: {e}", file=sys.stderr)
        return None


# ═════════════════════════════════════════════════════
#  阈值告警 (每30min)
# ═════════════════════════════════════════════════════

_alert_check_count = 0
_last_alert_total = None

def check_threshold(record: dict):
    """每 ALERT_EVERY_N 次采集检查一次半日累计净买入是否超阈值。

    只在交易状态下告警 (交易中/午间休市)，收盘后跳过。
    """
    global _alert_check_count, _last_alert_total

    _alert_check_count += 1
    if _alert_check_count % ALERT_EVERY_N != 0:
        return

    sh_status = record.get('sh_status', TRADE_STATUS_CLOSED)
    sz_status = record.get('sz_status', TRADE_STATUS_CLOSED)

    # 收盘后跳过告警
    if sh_status == TRADE_STATUS_CLOSED and sz_status == TRADE_STATUS_CLOSED:
        return

    sh_net = record.get('sh_net_estimated', 0.0)
    sz_net = record.get('sz_net_estimated', 0.0)
    total_net = sh_net + sz_net

    # 去重: 相同数值不重复告警
    if _last_alert_total is not None and abs(total_net - _last_alert_total) < 0.01:
        return

    if total_net > ALERT_UPPER:
        print(f"  [northbound] ⚠️ 北向资金大幅净买入 {total_net:.2f}亿 "
              f"(沪{sh_net:.2f}亿+深{sz_net:.2f}亿) > {ALERT_UPPER}亿阈值",
              file=sys.stderr)
        _last_alert_total = total_net
    elif total_net < ALERT_LOWER:
        print(f"  [northbound] ⚠️ 北向资金大幅净卖出 {total_net:.2f}亿 "
              f"(沪{sh_net:.2f}亿+深{sz_net:.2f}亿) < {ALERT_LOWER}亿阈值",
              file=sys.stderr)
        _last_alert_total = total_net


# ═════════════════════════════════════════════════════
#  写入 DB
# ═════════════════════════════════════════════════════

def write_northbound_intraday(conn: sqlite3.Connection, record: dict):
    """写入 northbound_intraday 表 (INSERT OR REPLACE)。"""
    conn.execute("""
        INSERT OR REPLACE INTO northbound_intraday
        (datetime, sh_quota_used, sz_quota_used, sh_quota_remain,
         sz_quota_remain, sh_net_estimated, sz_net_estimated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        record['datetime'],
        record['sh_quota_used'],
        record['sz_quota_used'],
        record['sh_quota_remain'],
        record['sz_quota_remain'],
        record['sh_net_estimated'],
        record['sz_net_estimated'],
    ))
    conn.commit()


# ═════════════════════════════════════════════════════
#  采集主函数
# ═════════════════════════════════════════════════════

def collect() -> bool:
    """执行一次完整采集。返回 True 表示成功。"""
    now_str = datetime.now().strftime('%H:%M:%S')
    print(f"  [northbound] ===== {now_str} 北向资金实时额度采集 =====",
          file=sys.stderr)

    # 1. 获取数据
    df = fetch_northbound_flow()
    if df.empty:
        print(f"  [northbound] 数据源为空, 跳过本次采集", file=sys.stderr)
        return False

    # 2. 解析
    record = parse_northbound_data(df)
    if record is None:
        return False

    print(f"  [northbound] 沪股通: 已用{record['sh_quota_used']:.2f}亿 "
          f"余额{record['sh_quota_remain']:.2f}亿 "
          f"净买入{record['sh_net_estimated']:.2f}亿",
          file=sys.stderr)
    print(f"  [northbound] 深股通: 已用{record['sz_quota_used']:.2f}亿 "
          f"余额{record['sz_quota_remain']:.2f}亿 "
          f"净买入{record['sz_net_estimated']:.2f}亿",
          file=sys.stderr)

    # 3. 阈值检查 (每 30min)
    check_threshold(record)

    # 4. 写入数据库
    try:
        conn = get_db(write=True)
        try:
            # 建表
            conn.executescript(DDL_NORTHBOUND_INTRADAY)

            write_northbound_intraday(conn, record)

            heartbeat('northbound_collector')
            print(f"  [northbound] ✓ northbound_intraday 写入成功", file=sys.stderr)
            return True
        finally:
            conn.close()
    except Exception as e:
        print(f"  [northbound] DB 写入失败: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═════════════════════════════════════════════════════
#  主入口
# ═════════════════════════════════════════════════════

def main():
    """单次采集入口 (`--once` 模式)。"""
    print(f"[northbound_collector] 北向资金实时额度采集 (单次)", file=sys.stderr)
    success = collect()
    if success:
        print(f"[northbound_collector] 采集完成 ✓", file=sys.stderr)
        sys.exit(0)
    else:
        print(f"[northbound_collector] 采集失败 ✗", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    if '--once' in sys.argv:
        main()
    else:
        # 持续采集: 每 300s (5min) 一次
        collector_main_loop(
            name='northbound_collector',
            collect_fn=collect,
            interval_seconds=300,
            max_consecutive_failures=10,
            gc_interval=100,
        )
