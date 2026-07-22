#!/usr/bin/env python3
"""
数据发布时刻知识库 — release_schedule.py

每个数据类型的官方发布时间 + 就绪探测策略 + 最佳采集窗口
用于智能调度器避免"数据还没发布就去拉取导致空数据"的问题。

用法:
    from nous.data.collectors.release_schedule import get_schedule, wait_until_ready
    
    schedule = get_schedule('hsgt_daily')
    ready = wait_until_ready('hsgt_daily')
"""

from datetime import datetime, time, timedelta
import time as _time
from typing import Optional, Callable

# ═══ 知识库: 所有数据类型 × 发布时间 ═══

RELEASE_SCHEDULE = {
    # ── 盘前数据 ──
    'global_index_overnight': {
        'description': '美股收盘/金龙指数/全球指数',
        'release_time': '05:00',       # 美股收盘后数据即ready
        'earliest_probe': '05:30',
        'probe_interval': 300,          # 5min
        'max_wait': 1800,               # 30min
        'probe_api': 'index_global_daily',
        'probe_field': 'close',
        'not_empty_check': True,
        'data_type': 'us_index',
    },
    'premarket_macro': {
        'description': '宏观数据 (SHIBOR/LPR/CPI/PPI/PMI)',
        'release_time': '17:00(前日)',  # 央行通常在16:30-17:00发布
        'earliest_probe': '08:00',
        'probe_interval': 600,
        'max_wait': 1800,
        'probe_api': 'macro_shibor',
        'not_empty_check': True,
        'data_type': 'macro',
    },
    'margin_daily': {
        'description': '两融余额/做空数据',
        'release_time': '20:00(前日)',  # 交易所晚上发布
        'earliest_probe': '08:15',
        'probe_interval': 300,
        'max_wait': 1200,
        'probe_api': 'margin_daily',
        'not_empty_check': True,
        'data_type': 'margin',
    },
    'lhb_daily': {
        'description': '龙虎榜/大宗交易',
        'release_time': '17:00(前日)',  # 龙虎榜盘后即发布
        'earliest_probe': '09:00',
        'probe_interval': 300,
        'max_wait': 600,
        'probe_api': 'lhb_daily',
        'not_empty_check': True,
        'data_type': 'lhb',
    },

    # ── 盘中数据 ──
    'intraday_indices': {
        'description': '盘中实时指数(上证/深/创/科/恒生)',
        'release_time': '实时',
        'earliest_probe': '09:30:05',
        'probe_interval': 60,
        'max_wait': 30,
        'probe_api': 'realtime_quote',
        'not_empty_check': True,
        'data_type': 'a_index',
        'continuous': True,  # 持续采集
    },
    'intraday_pool_stocks': {
        'description': '股票池个股分时',
        'release_time': '实时',
        'earliest_probe': '09:30:10',
        'probe_interval': 60,
        'max_wait': 30,
        'probe_api': 'stock_minute',
        'not_empty_check': True,
        'data_type': 'stock',
        'continuous': True,
    },
    'northbound_intraday': {
        'description': '北向资金盘中额度',
        'release_time': '实时(09:30开始每5分钟)',
        'earliest_probe': '09:35',
        'probe_interval': 300,
        'max_wait': 60,
        'probe_api': 'northbound_quota',
        'not_empty_check': False,  # 允许0(净买入可能为0)
        'data_type': 'hsgt',
        'continuous': True,
    },

    # ── 收盘数据 ──
    'closing_snapshot': {
        'description': '收盘快照(所有指数/个股收盘价)',
        'release_time': '15:00:03',
        'earliest_probe': '15:00:05',
        'probe_interval': 5,
        'max_wait': 60,
        'probe_api': 'index_close',
        'not_empty_check': True,
        'data_type': 'a_index',
    },
    'sentiment_daily': {
        'description': '涨停情绪(涨停数/炸板率/连板高度)',
        'release_time': '15:05',
        'earliest_probe': '15:35',
        'probe_interval': 60,
        'max_wait': 600,
        'probe_api': 'limit_up_sentiment',
        'not_empty_check': True,
        'data_type': 'sentiment',
    },
    'futures_close': {
        'description': '期货收盘/基差计算',
        'release_time': '15:15',  # 期货收盘
        'earliest_probe': '15:35',
        'probe_interval': 60,
        'max_wait': 600,
        'probe_api': 'futures_basis',
        'not_empty_check': True,
        'data_type': 'futures',
    },

    # ── 盘后数据(发布延迟较大) ──
    'stock_daily_update': {
        'description': '日线/基本面(全量)',
        'release_time': '15:30',
        'earliest_probe': '16:00',
        'probe_interval': 300,
        'max_wait': 1800,
        'probe_api': 'stock_daily',
        'not_empty_check': True,
        'data_type': 'stock',
    },
    'institution_research': {
        'description': '机构调研',
        'release_time': '16:00',
        'earliest_probe': '16:30',
        'probe_interval': 300,
        'max_wait': 1800,
        'probe_api': 'institution_research',
        'not_empty_check': True,
        'data_type': 'institution',
    },
    'etf_flow': {
        'description': 'ETF资金流',
        'release_time': '15:30',
        'earliest_probe': '15:40',
        'probe_interval': 300,
        'max_wait': 1800,
        'probe_api': 'etf_flow_daily',
        'not_empty_check': True,
        'data_type': 'etf',
    },
    'fund_flow_stock': {
        'description': '个股资金流向',
        'release_time': '15:30',
        'earliest_probe': '16:35',
        'probe_interval': 300,
        'max_wait': 1800,
        'probe_api': 'fund_flow_stock',
        'not_empty_check': True,
        'data_type': 'fund',
    },

    # ── 北向/南向 (17:30官方披露) ──
    'hsgt_market': {
        'description': '北向/南向资金汇总',
        'release_time': '17:30',  # 官方披露时间
        'earliest_probe': '17:28',
        'probe_interval': 60,
        'max_wait': 1800,
        'probe_api': 'stock_hsgt_fund_flow_summary_em',
        'probe_field': '成交净买额',
        'not_empty_check': True,
        'data_type': 'hsgt',
    },
    'hsgt_stock': {
        'description': '北向/南向个股TOP50',
        'release_time': '17:30',
        'earliest_probe': '17:42',  # 等到汇总采集完后
        'probe_interval': 60,
        'max_wait': 1800,
        'probe_api': 'stock_hsgt_hold_stock_em',
        'not_empty_check': True,
        'data_type': 'hsgt_stock',
        'depends_on': ['hsgt_market'],  # 依赖上游
    },
    'hsgt_sector': {
        'description': '北向/南向板块聚合',
        'release_time': '依赖hsgt_stock',
        'earliest_probe': '17:48',
        'probe_interval': 60,
        'max_wait': 1800,
        'probe_api': 'hsgt_sector_aggregator',
        'not_empty_check': True,
        'data_type': 'hsgt_sector',
        'depends_on': ['hsgt_stock'],
    },

    # ── 夜间全量复采 ──
    'night_reconcile_full': {
        'description': '20:00全量复采对账(所有数据线)',
        'release_time': '20:00',
        'earliest_probe': '20:00',
        'probe_interval': 300,
        'max_wait': 600,
        'probe_api': 'all',
        'not_empty_check': True,
        'data_type': 'reconcile',
        'full_reconcile': True,  # 全量对账模式
    },

    # ── 凌晨数据 ──
    'historical_backfill': {
        'description': '历史数据回补(周日凌晨)',
        'release_time': 'N/A(批量)',
        'earliest_probe': '02:00',
        'probe_interval': 3600,
        'max_wait': 14400,
        'probe_api': 'backfill_*',
        'not_empty_check': False,
        'data_type': 'backfill',
        'weekend_only': True,
    },
}


def get_schedule(key: str) -> Optional[dict]:
    """获取数据类型的调度配置"""
    return RELEASE_SCHEDULE.get(key)


def wait_until_ready(
    key: str,
    probe_fn: Optional[Callable] = None,
    max_wait_override: Optional[int] = None,
) -> bool:
    """
    智能等待数据就绪
    
    逻辑: 等到earliest_probe → 探测数据是否非空 → 是则返回True
    不是轮询到就返回，而是检查数据是否真的发布了。
    
    Args:
        key: 数据类型键
        probe_fn: 自定义探测函数(不提供则用默认API)
        max_wait_override: 覆盖最大等待时间
    
    Returns:
        True: 数据就绪
        False: 超时
    """
    schedule = RELEASE_SCHEDULE.get(key)
    if not schedule:
        print(f"  [scheduler] unknown key: {key}")
        return True  # 未知类型不阻塞

    # 如果周末且标记为仅周末，直接返回
    if schedule.get('weekend_only'):
        now = datetime.now()
        if now.weekday() not in (5, 6):  # 周六日
            return False

    # 计算等待到earliest_probe
    probe_str = schedule['earliest_probe']
    max_wait = max_wait_override or schedule['max_wait']
    probe_interval = schedule['probe_interval']
    not_empty = schedule.get('not_empty_check', True)

    # 解析earliest_probe时间
    if ':' in probe_str:
        parts = probe_str.split(':')
        target_time = time(int(parts[0]), int(parts[1]), 
                          int(parts[2]) if len(parts) > 2 else 0)
    else:
        # 如 "05:30"
        h, m = map(int, probe_str.split(':'))
        target_time = time(h, m)

    now = datetime.now()
    target_dt = datetime.combine(now.date(), target_time)
    
    # 如果目标时间已过，立即开始探测
    if now < target_dt:
        wait_secs = (target_dt - now).total_seconds()
        print(f"  [scheduler] {key}: waiting {wait_secs:.0f}s until {probe_str}")
        _time.sleep(min(wait_secs, max_wait))

    # 探测循环
    start = _time.time()
    attempts = 0
    while _time.time() - start < max_wait:
        attempts += 1
        try:
            if probe_fn:
                data = probe_fn()
            else:
                # 没有自定义探测函数 → 认为数据已就绪
                print(f"  [scheduler] {key}: ready (attempt {attempts})")
                return True

            if data is not None:
                if not_empty:
                    # 检查非空
                    if hasattr(data, '__len__') and len(data) > 0:
                        print(f"  [scheduler] {key}: data ready ({len(data)} rows, "
                              f"attempt {attempts})")
                        return True
                    elif not hasattr(data, '__len__'):
                        print(f"  [scheduler] {key}: data ready (non-empty, "
                              f"attempt {attempts})")
                        return True
                else:
                    print(f"  [scheduler] {key}: data ready (no empty check, "
                          f"attempt {attempts})")
                    return True

            print(f"  [scheduler] {key}: probing... (attempt {attempts}, "
                  f"data empty/none)")
        except Exception as e:
            print(f"  [scheduler] {key}: probe failed (attempt {attempts}): {e}")

        _time.sleep(probe_interval)

    print(f"  [scheduler] {key}: TIMEOUT after {max_wait}s ({attempts} attempts)")
    return False


def get_collection_window(key: str) -> tuple[str, str]:
    """
    获取最佳采集时间窗口
    
    Returns: (optimal_start, optimal_end) as HH:MM strings
    """
    schedule = RELEASE_SCHEDULE.get(key)
    if not schedule:
        return ("00:00", "23:59")

    optimal = schedule.get('optimal_window')
    if optimal:
        return optimal

    # 默认: earliest_probe + 30s → earliest_probe + max_wait
    probe_str = schedule['earliest_probe']
    max_wait = schedule.get('max_wait', 1800)
    
    parts = probe_str.split(':')
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    start_dt = datetime(2000, 1, 1, h, m, s) + timedelta(seconds=30)
    end_dt = start_dt + timedelta(seconds=max_wait)
    
    return (start_dt.strftime('%H:%M'), end_dt.strftime('%H:%M'))


def get_dependency_order() -> list[str]:
    """返回依赖拓扑排序的采集顺序"""
    # 简单拓扑排序: 先采集无依赖的，再采集有依赖的
    all_keys = list(RELEASE_SCHEDULE.keys())
    
    # 构建依赖图
    deps = {}
    for key in all_keys:
        deps[key] = RELEASE_SCHEDULE[key].get('depends_on', [])
    
    # BFS拓扑排序
    order = []
    visited = set()
    
    def visit(k):
        if k in visited:
            return
        visited.add(k)
        for dep in deps.get(k, []):
            if dep in deps:
                visit(dep)
        order.append(k)
    
    for k in all_keys:
        visit(k)
    
    return order


# ═══ 快速自检 ═══

if __name__ == '__main__':
    print("release_schedule.py — 知识库自检")
    
    print(f"\n  总计: {len(RELEASE_SCHEDULE)} 种数据类型")
    
    # 按大类统计
    categories = {}
    for key, cfg in RELEASE_SCHEDULE.items():
        cat = cfg.get('data_type', 'unknown')
        categories[cat] = categories.get(cat, 0) + 1
    
    print("  分类统计:")
    for cat, count in sorted(categories.items()):
        print(f"    {cat:20s}: {count}")
    
    # 检查发布时间的合理性
    print("\n  发布时间线:")
    release_times = []
    for key, cfg in RELEASE_SCHEDULE.items():
        rt = cfg.get('release_time', '')
        if rt and ':' in rt:
            try:
                h, m = map(int, rt.split(':'))
                release_times.append((key, h * 60 + m, rt))
            except:
                pass
    
    for key, mins, rt in sorted(release_times, key=lambda x: x[1]):
        print(f"    {rt:12s} → {key}")
    
    # 依赖链
    print("\n  依赖链:")
    for key in ['hsgt_market', 'hsgt_stock', 'hsgt_sector']:
        deps = RELEASE_SCHEDULE[key].get('depends_on', [])
        ep = RELEASE_SCHEDULE[key].get('earliest_probe', '')
        print(f"    {key} (probe={ep}): depends_on={deps}")
    
    print("\n  ✅ 知识库加载完成")
