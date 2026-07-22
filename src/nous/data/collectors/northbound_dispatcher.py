#!/usr/bin/env python3
"""北向资金多源调度器
优先级: 东财TOP50(已有) → 新浪直连 → 同花顺 → 本地DB推算
每源独立熔断器, 任一成功即返回
"""
import sqlite3
import sys
from pathlib import Path
from datetime import date, datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from nous.core.db import _resolve_path
DB = Path(_resolve_path("screener.db"))

# 熔断器配置
CIRCUIT_CONFIG = {
    "failure_threshold": 3,
    "cooldown_seconds": 300,  # 5分钟
    "half_open_probe": True,
}


class SourceCircuitBreaker:
    """简易熔断器: 3次失败→open, 5分钟冷却→half-open, 1次成功→closed"""
    
    def __init__(self, name: str):
        self.name = name
        self.failures = 0
        self.last_fail_time: Optional[datetime] = None
        self.state = "closed"  # closed | open | half_open
    
    def record_failure(self):
        self.failures += 1
        self.last_fail_time = datetime.now()
        if self.failures >= CIRCUIT_CONFIG["failure_threshold"]:
            self.state = "open"
    
    def record_success(self):
        self.failures = 0
        self.last_fail_time = None
        self.state = "closed"
    
    def can_try(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            elapsed = (datetime.now() - self.last_fail_time).total_seconds() if self.last_fail_time else 999
            if elapsed >= CIRCUIT_CONFIG["cooldown_seconds"]:
                self.state = "half_open"
                return True
            return False
        # half_open
        return True


# 全局熔断器实例(模块级, 跨调用持久)
_breakers: dict[str, SourceCircuitBreaker] = {}


def _get_breaker(name: str) -> SourceCircuitBreaker:
    if name not in _breakers:
        _breakers[name] = SourceCircuitBreaker(name)
    return _breakers[name]


def fetch_top50_aggregate(as_of_date: str = None) -> Optional[dict]:
    """源1: 东财TOP50个股聚合 (已有数据在hsgt_stock_daily)"""
    breaker = _get_breaker("eastmoney_top50")
    if not breaker.can_try():
        return None
    
    try:
        conn = sqlite3.connect(str(DB))
        conn.execute("PRAGMA busy_timeout=5000")
        
        if as_of_date is None:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM hsgt_stock_daily WHERE estimated_net_buy IS NOT NULL"
            ).fetchone()
            as_of_date = row[0] if row else date.today().isoformat()
        
        top50 = conn.execute("""
            SELECT SUM(estimated_net_buy)/1e8, COUNT(*), MAX(trade_date)
            FROM hsgt_stock_daily
            WHERE direction='北向' AND trade_date=? AND estimated_net_buy IS NOT NULL
        """, (as_of_date,)).fetchone()
        
        conn.close()
        
        if top50 and top50[0] and top50[1] >= 20:
            breaker.record_success()
            return {
                "net_buy": round(top50[0] * 2.0, 2),  # K=2.0外推
                "top50_raw": round(top50[0], 2),
                "stocks": top50[1],
                "trade_date": as_of_date,
                "source": "eastmoney_top50_extrapolation",
            }
        else:
            breaker.record_failure()
            return None
    except Exception:
        breaker.record_failure()
        return None


def fetch_sina_northbound() -> Optional[dict]:
    """源2: 新浪页面爬取"""
    breaker = _get_breaker("sina_page")
    if not breaker.can_try():
        return None
    
    try:
        from nous.data.collectors.sina_northbound import fetch_hsgt_summary
        result = fetch_hsgt_summary()
        if result and result.get("north_net_buy") is not None:
            breaker.record_success()
            return {
                "net_buy": result["north_net_buy"],
                "south_net_buy": result.get("south_net_buy"),
                "trade_date": result["trade_date"],
                "source": "sina_hsgt_page",
            }
        breaker.record_failure()
        return None
    except Exception:
        breaker.record_failure()
        return None


def fetch_estimator_db() -> Optional[dict]:
    """源3: 本地northbound_estimator推算值 (hsgt_daily.north)"""
    breaker = _get_breaker("estimator_db")
    if not breaker.can_try():
        return None
    
    try:
        conn = sqlite3.connect(str(DB))
        conn.execute("PRAGMA busy_timeout=5000")
        
        # 用MAX去重
        row = conn.execute("""
            SELECT trade_date, MAX(net_buy) FROM hsgt_daily
            WHERE direction='north' AND net_buy IS NOT NULL
            GROUP BY trade_date
            ORDER BY trade_date DESC LIMIT 1
        """).fetchone()
        
        conn.close()
        
        if row and row[1]:
            breaker.record_success()
            return {
                "net_buy": round(row[1], 2),
                "trade_date": row[0],
                "source": "northbound_estimator_db",
            }
        breaker.record_failure()
        return None
    except Exception:
        breaker.record_failure()
        return None


def dispatch(as_of_date: str = None) -> dict:
    """多源调度: 按优先级依次尝试
    
    Returns:
        {net_buy: float, source: str, confidence: str, tried_sources: [str]}
    """
    tried = []
    
    # 优先级: TOP50 → 推算DB → 新浪
    sources = [
        ("TOP50外推", lambda: fetch_top50_aggregate(as_of_date)),
        ("推算DB", fetch_estimator_db),
        ("新浪页面", fetch_sina_northbound),
    ]
    
    for name, fetcher in sources:
        tried.append(name)
        result = fetcher()
        if result and result.get("net_buy") is not None:
            return {
                "net_buy": result["net_buy"],
                "source": result.get("source", name),
                "confidence": "high" if result.get("stocks", 0) >= 50 else "medium",
                "tried_sources": tried,
                "trade_date": result.get("trade_date", ""),
                "raw": result,
            }
    
    return {
        "net_buy": 0,
        "source": "all_failed",
        "confidence": "low",
        "tried_sources": tried,
        "trade_date": as_of_date or date.today().isoformat(),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="指定日期 YYYY-MM-DD")
    args = parser.parse_args()
    
    result = dispatch(args.date)
    print(f"北向净买: {result['net_buy']:.1f}亿")
    print(f"来源: {result['source']}")
    print(f"置信度: {result['confidence']}")
    print(f"尝试源: {', '.join(result['tried_sources'])}")
    
    if result["source"] == "all_failed":
        print("⚠️  所有源均失败, 建议检查网络/数据新鲜度")
        sys.exit(1)
