#!/usr/bin/env python3
"""全管线速率控制 — 令牌桶 + 源级QPS预算 + 交易时段保护"""
import time
import threading
from datetime import datetime
from dataclasses import dataclass

@dataclass
class TokenBucket:
    rate: float       # 令牌/秒
    capacity: int     # 最大突发
    name: str = ""
    
    def __post_init__(self):
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
    
    @property
    def tokens_available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens
    
    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(float(self.capacity), self._tokens + elapsed * self.rate)
        self._last_refill = now
    
    def acquire(self, tokens: int = 1, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
            time.sleep(0.05)
        return False

# 全局源限流器
# baostock 为非 HTTP socket API，Hermes interceptor 管不到，必须显式限流（回补默认 ≤1 QPS）
SOURCE_LIMITERS = {
    'sina': TokenBucket(rate=5, capacity=5, name='sina'),
    'tencent': TokenBucket(rate=5, capacity=5, name='tencent'),
    'em_datacenter': TokenBucket(rate=3, capacity=5, name='em_datacenter'),
    'yahoo': TokenBucket(rate=0.5, capacity=1, name='yahoo'),
    'akshare': TokenBucket(rate=2, capacity=2, name='akshare'),
    'baostock': TokenBucket(rate=1.0, capacity=2, name='baostock'),
    'repair': TokenBucket(rate=2, capacity=3, name='repair'),
}

def get_rate_multiplier() -> float:
    now = datetime.now()
    if now.weekday() < 5 and now.hour == 9 and 25 <= now.minute <= 35:
        return 0.5
    return 1.0

def acquire_with_multiplier(source: str, tokens: int = 1, timeout: float = 30) -> bool:
    limiter = SOURCE_LIMITERS.get(source)
    if not limiter:
        return True
    multiplier = get_rate_multiplier()
    effective_tokens = max(1, int(tokens / multiplier))
    return limiter.acquire(effective_tokens, timeout=timeout)
