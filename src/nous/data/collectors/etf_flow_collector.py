#!/usr/bin/env python3
"""
etf_flow_collector — ETF资金流数据采集

采集核心宽基/行业/跨境/港股ETF的每日成交额和规模变化，
写入 etf_flow_daily 表，用于分析机构配置方向。

数据源:
  - fund_etf_spot_em(): 东方财富ETF实时行情（单次请求返回全部ETF）
    包含: 代码, 名称, 最新价, 涨跌幅, 成交额, 总市值(基金规模)

自愈: resilient_fetch + CircuitBreaker + 指数退避重试
看门狗: heartbeat('etf_flow_collector')
DB: get_db(write=True) + @with_retry

独立运行 (单次采集):
    python -m src.collectors.etf_flow_collector

运行频率: 盘后cron, 每日一次
速率: ~15只ETF, 1次API调用 ≈ 3s
"""

import sys
import os
import sqlite3
from datetime import date, datetime

# ── 确保可以从 src 导入 ─────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd

from nous.data.collectors import resilient_fetch, heartbeat
from nous.data.storage import get_db, with_retry

# ═════════════════════════════════════════════════════
#  核心ETF列表
# ═════════════════════════════════════════════════════

CORE_ETFS = [
    # ── 宽基 ──
    {'symbol': '510050', 'name': '上证50',       'etf_type': '宽基'},
    {'symbol': '510300', 'name': '沪深300',      'etf_type': '宽基'},
    {'symbol': '510500', 'name': '中证500',      'etf_type': '宽基'},
    {'symbol': '588000', 'name': '科创50',       'etf_type': '宽基'},
    {'symbol': '159915', 'name': '创业板',       'etf_type': '宽基'},
    # ── 行业 ──
    {'symbol': '512480', 'name': '半导体',       'etf_type': '行业'},
    {'symbol': '159995', 'name': '芯片ETF',      'etf_type': '行业'},
    {'symbol': '516160', 'name': '新能源',       'etf_type': '行业'},
    {'symbol': '512690', 'name': '酒ETF',        'etf_type': '行业'},
    {'symbol': '159766', 'name': '旅游ETF',      'etf_type': '行业'},
    # ── 跨境 ──
    {'symbol': '513050', 'name': '中概互联',     'etf_type': '跨境'},
    {'symbol': '159941', 'name': '纳指ETF',      'etf_type': '跨境'},
    {'symbol': '513100', 'name': '纳指100',      'etf_type': '跨境'},
    {'symbol': '513300', 'name': '日经ETF',      'etf_type': '跨境'},
    # ── 港股 ──
    {'symbol': '513090', 'name': '恒生科技',     'etf_type': '港股'},
    {'symbol': '159920', 'name': '恒生ETF',      'etf_type': '港股'},
    {'symbol': '513660', 'name': '恒生ETF(沪)',  'etf_type': '港股'},
]

# 快速查找: symbol → {name, etf_type}
ETF_MAP = {e['symbol']: e for e in CORE_ETFS}

# ═════════════════════════════════════════════════════
#  表 DDL
# ═════════════════════════════════════════════════════

DDL_ETF_FLOW_DAILY = """
CREATE TABLE IF NOT EXISTS etf_flow_daily (
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    etf_type TEXT,
    close REAL,
    turnover REAL,
    fund_size REAL,
    pct_change REAL,
    fetched_at TEXT,
    PRIMARY KEY (trade_date, symbol)
);
"""

# ═════════════════════════════════════════════════════
#  数据采集
# ═════════════════════════════════════════════════════

def fetch_all_etf_spot() -> pd.DataFrame:
    """使用 resilient_fetch 获取全市场ETF实时行情。

    fund_etf_spot_em() 返回全部ETF列表，包含:
      代码, 名称, 最新价, 涨跌幅, 成交额, 总市值(规模), 最新份额 等

    Returns:
        DataFrame 或空DataFrame（失败时）
    """
    def _fetch():
        import akshare
        return akshare.fund_etf_spot_em()

    result, status = resilient_fetch(
        'akshare', _fetch,
        fallback_fn=lambda: pd.DataFrame(),
        max_retries=3, base_delay=1.0,
    )

    if not status.get('success') or result is None:
        print(f"  [etf_flow] fund_etf_spot_em 获取失败: {status.get('error', 'unknown')}",
              file=sys.stderr)
        return pd.DataFrame()

    if status.get('fallback_used'):
        print(f"  [etf_flow] 使用降级数据", file=sys.stderr)

    return result


def parse_etf_flow_data(df: pd.DataFrame, trade_date: str) -> list[dict]:
    """从全市场ETF行情DataFrame中筛选目标ETF并提取所需字段。

    必要列:
      代码, 名称, 最新价, 涨跌幅, 成交额, 总市值

    Args:
        df: fund_etf_spot_em 返回的DataFrame
        trade_date: 交易日字符串 YYYY-MM-DD

    Returns:
        list[dict], 每个dict包含写入DB需要的字段
    """
    if df.empty:
        return []

    # 确保列存在，列的别名检查
    col_code = '代码' if '代码' in df.columns else None
    col_name = '名称' if '名称' in df.columns else None
    col_close = '最新价' if '最新价' in df.columns else None
    col_pct = '涨跌幅' if '涨跌幅' in df.columns else None
    col_turnover = '成交额' if '成交额' in df.columns else None
    col_size = '总市值' if '总市值' in df.columns else None

    missing = [c for c, v in zip(
        ['代码', '名称', '最新价', '涨跌幅', '成交额', '总市值'],
        [col_code, col_name, col_close, col_pct, col_turnover, col_size]
    ) if v is None]
    if missing:
        print(f"  [etf_flow] DataFrame缺少必要列: {missing}, "
              f"实际列: {df.columns.tolist()}", file=sys.stderr)
        return []

    records = []
    fetched_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for _, row in df.iterrows():
        code = str(row[col_code]).strip().zfill(6)
        etf_info = ETF_MAP.get(code)
        if etf_info is None:
            continue  # 跳过非目标ETF

        try:
            close_val = float(row[col_close]) if pd.notna(row[col_close]) else 0.0
            pct_val = float(row[col_pct]) if pd.notna(row[col_pct]) else 0.0
            turnover_val = float(row[col_turnover]) if pd.notna(row[col_turnover]) else 0.0
            size_val = float(row[col_size]) if pd.notna(row[col_size]) else 0.0
        except (ValueError, TypeError) as e:
            print(f"  [etf_flow] {code} 数值解析失败: {e}", file=sys.stderr)
            continue

        records.append({
            'trade_date': trade_date,
            'symbol': code,
            'name': etf_info['name'],
            'etf_type': etf_info['etf_type'],
            'close': round(close_val, 4),
            'turnover': round(turnover_val, 2),
            'fund_size': round(size_val, 2),
            'pct_change': round(pct_val, 4),
            'fetched_at': fetched_at,
        })

    return records


# ═════════════════════════════════════════════════════
#  写入 DB
# ═════════════════════════════════════════════════════

@with_retry(max_attempts=3)
def write_etf_flow_daily(conn: sqlite3.Connection, records: list[dict]):
    """批量写入 etf_flow_daily 表 (INSERT OR REPLACE)。

    使用 @with_retry 处理 SQLITE_BUSY/LOCKED 情况。
    """
    conn.executemany("""
        INSERT OR REPLACE INTO etf_flow_daily
        (trade_date, symbol, name, etf_type, close, turnover,
         fund_size, pct_change, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            r['trade_date'],
            r['symbol'],
            r['name'],
            r['etf_type'],
            r['close'],
            r['turnover'],
            r['fund_size'],
            r['pct_change'],
            r['fetched_at'],
        )
        for r in records
    ])
    conn.commit()


# ═════════════════════════════════════════════════════
#  采集主函数
# ═════════════════════════════════════════════════════

def collect() -> bool:
    """执行一次完整采集。返回 True 表示成功。"""
    trade_date = date.today().strftime('%Y-%m-%d')
    now_str = datetime.now().strftime('%H:%M:%S')
    print(f"  [etf_flow] ===== {now_str} ETF资金流采集 ({trade_date}) =====",
          file=sys.stderr)

    # 1. 获取全市场ETF行情数据
    df = fetch_all_etf_spot()
    if df.empty:
        print(f"  [etf_flow] fund_etf_spot_em 返回空数据", file=sys.stderr)
        return False

    print(f"  [etf_flow] 全市场ETF共 {len(df)} 只", file=sys.stderr)

    # 2. 解析目标ETF数据
    records = parse_etf_flow_data(df, trade_date)
    if not records:
        print(f"  [etf_flow] 未匹配到目标ETF", file=sys.stderr)
        return False

    print(f"  [etf_flow] 匹配到 {len(records)} 只目标ETF:", file=sys.stderr)
    for r in records:
        print(f"    {r['symbol']} {r['name']} ({r['etf_type']}) "
              f"收盘={r['close']:.4f} 成交额={r['turnover']:.2f} "
              f"规模={r['fund_size']:.2f} 涨跌={r['pct_change']:+.2f}%",
              file=sys.stderr)

    # 3. 写入数据库
    try:
        conn = get_db(write=True)
        try:
            # 建表
            conn.executescript(DDL_ETF_FLOW_DAILY)

            # 写入
            write_etf_flow_daily(conn, records)

            # 心跳
            heartbeat('etf_flow_collector')

            print(f"  [etf_flow] ✓ etf_flow_daily 写入 {len(records)} 条记录成功",
                  file=sys.stderr)
            return True
        finally:
            conn.close()
    except Exception as e:
        print(f"  [etf_flow] DB写入失败: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═════════════════════════════════════════════════════
#  可选增强: 宽基vs行业成交额占比分析
# ═════════════════════════════════════════════════════

def compute_flow_indicators(records: list[dict]) -> dict:
    """计算资金配置方向指标（供日志/监控使用）。

    Returns:
        dict with keys:
          - broad_turnover: 宽基ETF总成交额
          - sector_turnover: 行业ETF总成交额
          - broad_pct: 宽基成交额占比
          - sector_pct: 行业成交额占比
          - cross_border_turnover: 跨境ETF总成交额
          - hk_turnover: 港股ETF总成交额
    """
    broad = sum(r['turnover'] for r in records if r['etf_type'] == '宽基')
    sector = sum(r['turnover'] for r in records if r['etf_type'] == '行业')
    cross = sum(r['turnover'] for r in records if r['etf_type'] == '跨境')
    hk = sum(r['turnover'] for r in records if r['etf_type'] == '港股')
    total = broad + sector + cross + hk

    return {
        'broad_turnover': round(broad, 2),
        'sector_turnover': round(sector, 2),
        'cross_border_turnover': round(cross, 2),
        'hk_turnover': round(hk, 2),
        'broad_pct': round(broad / total * 100, 2) if total > 0 else 0.0,
        'sector_pct': round(sector / total * 100, 2) if total > 0 else 0.0,
        'cross_border_pct': round(cross / total * 100, 2) if total > 0 else 0.0,
        'hk_pct': round(hk / total * 100, 2) if total > 0 else 0.0,
    }


# ═════════════════════════════════════════════════════
#  主入口
# ═════════════════════════════════════════════════════

def main():
    """单次采集入口。"""
    print(f"[etf_flow_collector] ETF资金流数据采集 (单次)", file=sys.stderr)
    print(f"[etf_flow_collector] 目标ETF: {len(CORE_ETFS)} 只", file=sys.stderr)

    success = collect()

    if success:
        print(f"[etf_flow_collector] 采集完成 ✓", file=sys.stderr)
        sys.exit(0)
    else:
        print(f"[etf_flow_collector] 采集失败 ✗", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
