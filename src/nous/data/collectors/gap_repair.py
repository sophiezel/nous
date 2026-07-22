#!/usr/bin/env python3
"""缺口自动修复 — 三路补拉 + 盲区标记"""
import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


def repair_gap(symbol: str, target_date: Optional[date] = None, market: str = "a") -> dict:
    """
    三路补拉：重试主源 → 备用源 → 标记盲区
    返回 {'success': bool, 'method': str, 'rows': int}
    """
    from nous.data.collectors.fetchers.a_share import fetch_daily

    if target_date is None:
        target_date = date.today()

    # 第1路：重试主源 (Sina via akshare)
    try:
        df = fetch_daily(symbol, days=1)
        if df is not None and len(df) > 0:
            close_val = df.iloc[-1].get('close', 0) if hasattr(df, 'iloc') else df[-1].get('close', 0)
            if close_val and close_val > 0:
                logger.info(f"repair_gap: {symbol} 主源成功")
                return {'success': True, 'method': 'primary', 'rows': len(df)}
    except Exception as e:
        logger.debug(f"repair_gap: {symbol} 主源失败: {e}")

    # 第2路：备用源 (EM — 目前不可用，但保留接口)
    try:
        from nous.data.collectors.fetchers.a_share import fetch_em_daily
        df = fetch_em_daily(symbol)
        if df is not None and len(df) > 0:
            logger.info(f"repair_gap: {symbol} 备用源(EM)成功")
            return {'success': True, 'method': 'em_fallback', 'rows': len(df)}
    except RuntimeError:
        # EM源不可用是预期的
        pass
    except Exception as e:
        logger.debug(f"repair_gap: {symbol} 备用源失败: {e}")

    # 第3路：标记盲区
    from nous.data.storage import mark_blindspot
    reason = "all_sources_failed"
    mark_blindspot(symbol, market, target_date, reason)
    logger.warning(f"repair_gap: {symbol} 三路全失败，已标记盲区")
    return {'success': False, 'method': 'blindspot', 'rows': 0}


def repair_batch(symbols: list, target_date=None, market="a", max_per_second: int = 2):
    """
    批量修复缺口，限速执行。
    max_per_second: 最多每秒修复几只（默认2，避免触发限流）
    """
    import time
    from nous.data.collectors.rate_limiter import SOURCE_LIMITERS

    results = []
    for i, sym in enumerate(symbols):
        # 限速
        SOURCE_LIMITERS['repair'].acquire(timeout=30)

        result = repair_gap(sym, target_date, market)
        results.append({'symbol': sym, **result})

        if (i + 1) % 20 == 0:
            ok = sum(1 for r in results if r['success'])
            print(f"  repair进度: {i+1}/{len(symbols)} ok={ok}")

    return results
