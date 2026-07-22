"""数据源健康评分引擎 — 四维加固·稳定层

每个数据源维护滑动窗口健康分(0-100):
- Sina直连: 成功率×响应延迟衰减
- push2(EM): 熔断器状态+恢复探测
- yfinance: 代理可用性+响应时间
- screener.db缓存: 始终100(本地)

用法:
  from src.data_quality.source_health import HealthTracker
  tracker = HealthTracker()
  tracker.record_success('sina', latency_ms=200)
  score = tracker.get_score('sina')  # 0-100
"""

import time
import json
from pathlib import Path
from dataclasses import dataclass, field
from collections import deque
from typing import Optional

STATE_PATH = Path.home() / ".hermes" / "cache" / "source_health.json"

# 滑动窗口大小(最近N次请求)
WINDOW_SIZE = 100

# 各数据源配置
SOURCE_CONFIG = {
    "sina": {
        "weight_success": 0.7,      # 成功率权重
        "weight_latency": 0.3,      # 延迟权重
        "latency_ok_ms": 500,       # 正常延迟阈值
        "latency_bad_ms": 3000,     # 差延迟阈值
        "min_samples": 5,           # 最少样本数才评分
    },
    "push2": {
        "weight_success": 0.8,
        "weight_latency": 0.2,
        "latency_ok_ms": 1000,
        "latency_bad_ms": 5000,
        "min_samples": 3,
        "circuit_breaker": True,    # 启用熔断器
        "cb_fail_threshold": 3,     # 连续失败N次→OPEN
        "cb_recovery_sec": 300,     # 5分钟后尝试HALF_OPEN
    },
    "yfinance": {
        "weight_success": 0.6,
        "weight_latency": 0.4,
        "latency_ok_ms": 2000,
        "latency_bad_ms": 10000,
        "min_samples": 3,
    },
    "screener_cache": {
        "weight_success": 1.0,
        "weight_latency": 0.0,
        "min_samples": 0,
    },
}


@dataclass
class SourceState:
    """单个数据源的状态"""
    name: str
    successes: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    latencies: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    # 熔断器状态
    cb_state: str = "CLOSED"        # CLOSED | OPEN | HALF_OPEN
    cb_fail_count: int = 0
    cb_opened_at: float = 0.0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_error: str = ""


class HealthTracker:
    """多数据源健康追踪器(单例)"""
    
    _instance: Optional["HealthTracker"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._sources: dict[str, SourceState] = {}
        self._load_state()
        self._initialized = True
    
    def _load_state(self):
        """从磁盘恢复状态"""
        if STATE_PATH.exists():
            try:
                data = json.loads(STATE_PATH.read_text())
                for name, sdata in data.get("sources", {}).items():
                    src = SourceState(name=name)
                    src.cb_state = sdata.get("cb_state", "CLOSED")
                    src.cb_fail_count = sdata.get("cb_fail_count", 0)
                    src.last_success_at = sdata.get("last_success_at", 0)
                    src.last_failure_at = sdata.get("last_failure_at", 0)
                    src.last_error = sdata.get("last_error", "")
                    self._sources[name] = src
            except Exception:
                pass
    
    def _save_state(self):
        """持久化到磁盘"""
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "updated_at": time.time(),
            "sources": {
                name: {
                    "cb_state": s.cb_state,
                    "cb_fail_count": s.cb_fail_count,
                    "last_success_at": s.last_success_at,
                    "last_failure_at": s.last_failure_at,
                    "last_error": s.last_error,
                }
                for name, s in self._sources.items()
            }
        }
        STATE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    
    def _get_or_create(self, name: str) -> SourceState:
        if name not in self._sources:
            self._sources[name] = SourceState(name=name)
        return self._sources[name]
    
    # ── Public API ──
    
    def record_success(self, source: str, latency_ms: float = 0):
        """记录一次成功请求"""
        s = self._get_or_create(source)
        s.successes.append(1)
        s.latencies.append(latency_ms)
        s.last_success_at = time.time()
        
        cfg = SOURCE_CONFIG.get(source, {})
        if cfg.get("circuit_breaker"):
            # HALF_OPEN → CLOSED on success
            if s.cb_state == "HALF_OPEN":
                s.cb_state = "CLOSED"
            s.cb_fail_count = 0
        
        self._save_state()
    
    def record_failure(self, source: str, error: str = "", latency_ms: float = 0):
        """记录一次失败请求"""
        s = self._get_or_create(source)
        s.successes.append(0)
        s.latencies.append(latency_ms)
        s.last_failure_at = time.time()
        s.last_error = error[:200]
        
        cfg = SOURCE_CONFIG.get(source, {})
        if cfg.get("circuit_breaker"):
            threshold = cfg.get("cb_fail_threshold", 3)
            s.cb_fail_count += 1
            
            if s.cb_state == "CLOSED" and s.cb_fail_count >= threshold:
                s.cb_state = "OPEN"
                s.cb_opened_at = time.time()
            elif s.cb_state == "HALF_OPEN":
                s.cb_state = "OPEN"
                s.cb_opened_at = time.time()
        
        self._save_state()
    
    def get_score(self, source: str) -> float:
        """获取数据源健康评分 0-100"""
        if source == "screener_cache":
            return 100.0
        
        s = self._sources.get(source)
        if not s:
            return 0.0
        
        cfg = SOURCE_CONFIG.get(source, {})
        
        # 熔断器OPEN → 0分
        if cfg.get("circuit_breaker"):
            if s.cb_state == "OPEN":
                recovery_sec = cfg.get("cb_recovery_sec", 300)
                if time.time() - s.cb_opened_at > recovery_sec:
                    s.cb_state = "HALF_OPEN"
                else:
                    return 0.0
        
        # 样本不足 → 低分(保守)
        min_samples = cfg.get("min_samples", 5)
        if len(s.successes) < min_samples:
            return 30.0
        
        # 成功率分
        recent = list(s.successes)[-min(min_samples * 2, WINDOW_SIZE):]
        success_rate = sum(recent) / len(recent) if recent else 0
        success_score = success_rate * 100
        
        # 延迟分
        latency_score = 100.0
        if s.latencies:
            recent_lat = list(s.latencies)[-min(min_samples * 2, WINDOW_SIZE):]
            recent_lat = [l for l in recent_lat if l > 0]
            if recent_lat:
                avg_lat = sum(recent_lat) / len(recent_lat)
                ok_ms = cfg.get("latency_ok_ms", 500)
                bad_ms = cfg.get("latency_bad_ms", 3000)
                if avg_lat <= ok_ms:
                    latency_score = 100.0
                elif avg_lat >= bad_ms:
                    latency_score = 0.0
                else:
                    latency_score = 100 * (1 - (avg_lat - ok_ms) / (bad_ms - ok_ms))
        
        w_success = cfg.get("weight_success", 0.7)
        w_latency = cfg.get("weight_latency", 0.3)
        
        return round(w_success * success_score + w_latency * latency_score, 1)
    
    def is_available(self, source: str) -> bool:
        """数据源是否可用(评分≥50)"""
        return self.get_score(source) >= 50.0
    
    def get_best_source(self, candidates: list[str]) -> str:
        """从候选源中选择健康分最高的"""
        if not candidates:
            return ""
        best = max(candidates, key=lambda s: self.get_score(s))
        if self.is_available(best):
            return best
        # 全部不可用 → 返回第一个
        return candidates[0]
    
    def get_all_scores(self) -> dict:
        """获取所有数据源评分摘要"""
        return {
            name: {
                "score": self.get_score(name),
                "available": self.is_available(name),
                "cb_state": self._sources[name].cb_state if name in self._sources else "N/A",
                "last_success_sec": int(time.time() - self._sources[name].last_success_at) if name in self._sources and self._sources[name].last_success_at else -1,
            }
            for name in set(list(SOURCE_CONFIG.keys()) + list(self._sources.keys()))
            if name != "screener_cache"
        }


# 全局单例
health_tracker = HealthTracker()
