"""
采集器自愈框架 v1
- CircuitBreaker: 数据源熔断器 (closed→open→half_open→closed)
- resilient_fetch: API调用自愈包装 (指数退避+熔断+降级)
- heartbeat: 进程心跳文件 (供看门狗读取)
- HealthTracker: 数据源健康评分
"""

import time
import random
import sys
import os
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Callable, Optional, Any

HEARTBEAT_DIR = Path.home() / ".hermes" / "cache" / "heartbeats"
HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════
# CircuitBreaker — 熔断器
# ═══════════════════════════════════════════

@dataclass
class CircuitBreaker:
    """单数据源熔断器 — 防止故障扩散"""
    name: str
    failure_threshold: int = 5
    cooldown_seconds: int = 120
    half_open_limit: int = 2

    _failures: int = 0
    _last_failure: float = 0.0
    _state: str = "closed"  # closed → open → half_open → closed
    _half_open_count: int = 0
    _total_failures: int = 0
    _total_successes: int = 0

    def allow(self) -> bool:
        """是否允许请求通过"""
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.time() - self._last_failure > self.cooldown_seconds:
                self._state = "half_open"
                self._half_open_count = 0
                print(f"  [CB:{self.name}] OPEN→HALF_OPEN (cooldown {self.cooldown_seconds}s)", file=sys.stderr)
                return True
            return False
        if self._state == "half_open":
            return self._half_open_count < self.half_open_limit
        return True

    def success(self):
        """记录一次成功"""
        self._total_successes += 1
        if self._state == "half_open":
            self._state = "closed"
            self._failures = 0
            print(f"  [CB:{self.name}] HALF_OPEN→CLOSED (recovered)", file=sys.stderr)
        elif self._state == "closed":
            self._failures = 0  # 重置连续失败计数

    def failure(self):
        """记录一次失败"""
        self._failures += 1
        self._total_failures += 1
        self._last_failure = time.time()

        if self._state == "half_open":
            self._half_open_count += 1
            if self._half_open_count >= self.half_open_limit:
                self._state = "open"
                print(f"  [CB:{self.name}] HALF_OPEN→OPEN (still failing)", file=sys.stderr)
        elif self._state == "closed" and self._failures >= self.failure_threshold:
            self._state = "open"
            print(f"  [CB:{self.name}] CLOSED→OPEN ({self._failures} consecutive failures, "
                  f"cooldown {self.cooldown_seconds}s)", file=sys.stderr)

    @property
    def health_score(self) -> float:
        """健康评分 0-1"""
        total = self._total_successes + self._total_failures
        if total == 0:
            return 1.0
        return self._total_successes / total

    @property
    def is_open(self) -> bool:
        return self._state == "open"


# ═══════════════════════════════════════════
# 全局熔断器注册表
# ═══════════════════════════════════════════

DEFAULT_BREAKERS: dict[str, CircuitBreaker] = {
    "sina": CircuitBreaker("sina", failure_threshold=5, cooldown_seconds=120),
    "em": CircuitBreaker("em", failure_threshold=3, cooldown_seconds=180),
    "yfinance": CircuitBreaker("yfinance", failure_threshold=3, cooldown_seconds=300),
    "akshare": CircuitBreaker("akshare", failure_threshold=5, cooldown_seconds=120),
}


def get_breaker(name: str) -> CircuitBreaker:
    """获取或创建数据源熔断器"""
    return DEFAULT_BREAKERS[name]


# ═══════════════════════════════════════════
# resilient_fetch — API调用自愈包装
# ═══════════════════════════════════════════

def resilient_fetch(
    source_name: str,
    fetch_fn: Callable,
    fallback_fn: Optional[Callable] = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> tuple[Any, dict]:
    """
    自愈API调用包装器
    
    Args:
        source_name: 'sina'/'em'/'yfinance'/'akshare'
        fetch_fn: 主采集函数 callable() → Any
        fallback_fn: 降级函数 (可选)
        max_retries: 最大重试次数
        base_delay: 基础退避延迟(秒)
    
    Returns:
        (result, status_dict)
        status: {success, source, fallback_used, retries, circuit_open, error}
    """
    breaker = DEFAULT_BREAKERS.get(source_name)
    retries = 0

    # 熔断检查
    if breaker and not breaker.allow():
        print(f"  [{source_name}] Circuit OPEN, skipping", file=sys.stderr)
        if fallback_fn:
            try:
                result = fallback_fn()
                return result, {"success": True, "source": source_name,
                                "fallback_used": True, "circuit_open": True}
            except Exception as e:
                return None, {"success": False, "source": source_name,
                              "circuit_open": True, "error": str(e)}
        return None, {"success": False, "source": source_name,
                      "circuit_open": True}

    # 指数退避重试
    last_error = None
    for attempt in range(max_retries):
        try:
            result = fetch_fn()
            if breaker:
                breaker.success()
            return result, {"success": True, "source": source_name, "retries": retries}
        except Exception as e:
            retries = attempt + 1
            last_error = str(e)[:200]
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"  [{source_name}] attempt {attempt+1}/{max_retries} failed: "
                      f"{type(e).__name__}, retry in {delay:.1f}s", file=sys.stderr)
                time.sleep(delay)
            else:
                if breaker:
                    breaker.failure()
                print(f"  [{source_name}] ALL {max_retries} attempts failed: "
                      f"{type(e).__name__}", file=sys.stderr)

    # 全部失败 → 降级
    if fallback_fn:
        try:
            result = fallback_fn()
            return result, {"success": True, "source": source_name,
                            "fallback_used": True, "retries": retries}
        except Exception as e:
            return None, {"success": False, "source": source_name,
                          "retries": retries, "error": str(e)}

    return None, {"success": False, "source": source_name,
                  "retries": retries, "error": last_error}


# ═══════════════════════════════════════════
# Heartbeat — 心跳文件
# ═══════════════════════════════════════════

def heartbeat(process_name: str):
    """写入心跳时间戳。Watchdog每30s检查此文件"""
    hb_file = HEARTBEAT_DIR / f"{process_name}.heartbeat"
    try:
        hb_file.write_text(str(time.time()))
    except Exception:
        pass  # 心跳失败不能影响主流程


def check_heartbeat(process_name: str) -> tuple[bool, str]:
    """检查进程心跳状态。返回(alive, message)"""
    hb_file = HEARTBEAT_DIR / f"{process_name}.heartbeat"
    if not hb_file.exists():
        return False, "no heartbeat file"
    try:
        last_hb = float(hb_file.read_text().strip())
        age = time.time() - last_hb
        return True, f"ok ({age:.0f}s ago)"
    except Exception:
        return False, "heartbeat unreadable"


# ═══════════════════════════════════════════
# HealthTracker — 数据源健康评分
# ═══════════════════════════════════════════

@dataclass
class HealthTracker:
    """滑动窗口健康追踪"""
    window_size: int = 100
    _successes: list[float] = field(default_factory=list)
    _failures: list[float] = field(default_factory=list)

    def record_success(self):
        self._successes.append(time.time())
        self._trim()

    def record_failure(self):
        self._failures.append(time.time())
        self._trim()

    def _trim(self):
        cutoff = time.time() - self.window_size
        self._successes = [t for t in self._successes if t > cutoff]
        self._failures = [t for t in self._failures if t > cutoff]

    @property
    def health_pct(self) -> float:
        total = len(self._successes) + len(self._failures)
        if total == 0:
            return 100.0
        return len(self._successes) / total * 100

    @property
    def recent_failure_rate(self) -> float:
        total = len(self._successes) + len(self._failures)
        if total < 10:
            return 0.0
        return len(self._failures) / total


# ═══════════════════════════════════════════
# 采集脚本标准骨架
# ═══════════════════════════════════════════

def collector_main_loop(
    name: str,
    collect_fn: Callable[[], bool],
    interval_seconds: int = 60,
    max_consecutive_failures: int = 10,
    gc_interval: int = 100,
):
    """
    采集器主循环骨架 — 自愈 + 心跳 + 内存管理
    
    Args:
        name: 进程名(用于心跳文件)
        collect_fn: 采集函数 callable() → bool(成功/失败)
        interval_seconds: 采集间隔
        max_consecutive_failures: 连续失败上限(超过后退出,让看门狗重启)
        gc_interval: 多少次循环后强制gc
    """
    import gc
    import signal as sig

    # 优雅退出
    sig.signal(sig.SIGTERM, lambda *_: sys.exit(0))
    sig.signal(sig.SIGINT, lambda *_: sys.exit(0))

    consecutive_failures = 0
    loop_count = 0

    print(f"[{name}] Starting collector loop (interval={interval_seconds}s, "
          f"max_failures={max_consecutive_failures})", file=sys.stderr)

    while True:
        heartbeat(name)
        loop_count += 1

        try:
            success = collect_fn()
        except Exception as e:
            print(f"[{name}] Unhandled exception: {type(e).__name__}: {e}", file=sys.stderr)
            success = False

        if success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            print(f"[{name}] Failure {consecutive_failures}/{max_consecutive_failures}",
                  file=sys.stderr)

        if consecutive_failures >= max_consecutive_failures:
            print(f"[{name}] FATAL: {consecutive_failures} consecutive failures, "
                  f"exiting for watchdog restart", file=sys.stderr)
            sys.exit(1)

        # 定期内存回收
        if loop_count % gc_interval == 0:
            gc.collect()

        time.sleep(interval_seconds)


# ═══════════════════════════════════════════
# 断路器状态查询 (供外部使用)
# ═══════════════════════════════════════════

def get_all_breaker_status() -> dict:
    """获取所有熔断器状态"""
    return {
        name: {
            "state": cb._state,
            "failures": cb._failures,
            "health_score": round(cb.health_score, 3),
            "total_successes": cb._total_successes,
            "total_failures": cb._total_failures,
        }
        for name, cb in DEFAULT_BREAKERS.items()
    }


def get_all_heartbeats() -> dict:
    """获取所有进程心跳状态"""
    result = {}
    for hb_file in sorted(HEARTBEAT_DIR.glob("*.heartbeat")):
        name = hb_file.stem
        alive, msg = check_heartbeat(name)
        result[name] = {"alive": alive, "message": msg}
    return result
