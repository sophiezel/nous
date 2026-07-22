import { LRUCache as LRU } from "lru-cache";

/** Query result cache entry with TTL */
interface CacheEntry {
  data: unknown;
  expiresAt: number;
}

/**
 * Memory cache for DB query results.
 * Uses lru-cache with TTL per table key.
 */
const _cache = new LRU<string, CacheEntry>({
  max: 500,
  ttlAutopurge: true,
});

// Table default TTLs (ms)
const TTL: Record<string, number> = {
  default: 30_000,        // 30s
  stock_daily: 60_000,    // 1min
  stock_daily_all: 60_000,
  stock_daily_latest: 30_000,  // 30s (versioned, more volatile)
  stock_fundamental: 300_000,  // 5min
  stock_fund_flow: 60_000,
  sentiment_cache: 10_000,     // 10s
  hsgt_daily: 60_000,
  margin_daily: 60_000,
  index_daily: 120_000,        // 2min
  index_global_daily: 120_000,
  futures_daily: 60_000,
  lhb_daily: 60_000,
  hsgt_stock_daily: 60_000,
  hsgt_sector_daily: 60_000,
  etf_flow_daily: 60_000,
  macro_cpi: 300_000,
  macro_ppi: 300_000,
  macro_pmi: 300_000,
  macro_m2: 300_000,
  macro_shibor: 300_000,
};

function getTTL(table: string): number {
  return TTL[table] ?? TTL.default;
}

/** Build a deterministic cache key from query parts */
function cacheKey(table: string, params?: unknown[]): string {
  if (!params || params.length === 0) return table;
  return `${table}:${JSON.stringify(params)}`;
}

/**
 * Get cached data for a table+params key.
 * Returns undefined if missing or expired.
 */
export function cacheGet(table: string, params?: unknown[]): unknown | undefined {
  const key = cacheKey(table, params);
  const entry = _cache.get(key);
  if (!entry) return undefined;
  if (Date.now() > entry.expiresAt) {
    _cache.delete(key);
    return undefined;
  }
  return entry.data;
}

/**
 * Store data in cache for a table.
 */
export function cacheSet(table: string, params: unknown[] | undefined, data: unknown): void {
  const key = cacheKey(table, params);
  const ttl = getTTL(table);
  _cache.set(key, {
    data,
    expiresAt: Date.now() + ttl,
  });
}

/**
 * Invalidate all entries for a specific table.
 * Used after data sync to ensure fresh reads.
 */
export function cacheInvalidate(table: string): void {
  const prefix = `${table}:`;
  for (const key of _cache.keys()) {
    if (key === table || key.startsWith(prefix)) {
      _cache.delete(key);
    }
  }
}

/**
 * Invalidate ALL cache entries.
 */
export function cacheClear(): void {
  _cache.clear();
}

/**
 * Get current cache stats.
 */
export function cacheStats(): { size: number; max: number; keys: string[] } {
  return {
    size: _cache.size,
    max: _cache.max,
    keys: [..._cache.keys()],
  };
}
