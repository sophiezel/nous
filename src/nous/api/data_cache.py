"""写穿透缓存 v2 — TTL过期 + 后台清理 + clear-on-read

策略:
  1. clear-on-read: Dashboard 轮询命中→返回+清空 (瞬时通知)
  2. TTL 过期: 默认 12h, cron 推送后若 Dashboard 未轮询→自动清理
  3. 后台清理: 每 600s 扫描一次, 清除过期条目
  4. 容量上限: max 10000 条, 超出则 LRU 淘汰最旧
"""
import threading
import time

DEFAULT_TTL = 43200  # 12 hours

_cache: dict[str, tuple[dict, float]] = {}
_lock = threading.Lock()
_cleaner_started = False


def cache_put(key: str, data: dict, ttl: int = DEFAULT_TTL):
    """Put data into cache with TTL."""
    with _lock:
        _cache[key] = (data, time.time() + ttl)

        # LRU eviction: if over capacity, sort by expiry and drop oldest 2000
        if len(_cache) > 10000:
            sorted_keys = sorted(_cache.items(), key=lambda x: x[1][1])
            for old_key, _ in sorted_keys[:2000]:
                del _cache[old_key]


def cache_get_clear(key: str) -> dict | None:
    """Get and remove data from cache. Returns None if not found or expired."""
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None

        data, expiry = entry

        # Check TTL expiry
        if time.time() > expiry:
            del _cache[key]
            return None

        # Clear-on-read: delete entry after reading
        del _cache[key]
        return data


def _cleaner_loop():
    """Background loop: scan and remove expired entries every 600s."""
    while True:
        time.sleep(600)

        with _lock:
            now = time.time()
            expired = [k for k, (_, exp) in _cache.items() if now > exp]
            for k in expired:
                del _cache[k]


def start_cleaner():
    """Start the background cleaner thread (daemon). Idempotent."""
    global _cleaner_started
    if _cleaner_started:
        return
    _cleaner_started = True
    t = threading.Thread(target=_cleaner_loop, daemon=True)
    t.start()
