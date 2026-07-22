import { LRUCache } from "lru-cache";

interface Window {
  timestamps: number[];
}

const rateLimitCache = new LRUCache<string, Window>({
  max: 10000,
  ttl: 1000 * 60 * 5,
});

export function rateLimit(
  key: string,
  limit: number,
  windowMs: number
): { success: boolean; remaining: number; reset: number } {
  const now = Date.now();
  let w = rateLimitCache.get(key);
  if (!w) {
    w = { timestamps: [] };
    rateLimitCache.set(key, w);
  }

  w.timestamps = w.timestamps.filter((t) => now - t < windowMs);

  if (w.timestamps.length >= limit) {
    const oldest = w.timestamps[0];
    const reset = oldest + windowMs;
    return { success: false, remaining: 0, reset };
  }

  w.timestamps.push(now);
  return {
    success: true,
    remaining: limit - w.timestamps.length,
    reset: now + windowMs,
  };
}

export const RATE_LIMITS = {
  LOGIN: { limit: 5, windowMs: 60_000 },
  API_DATA: { limit: 30, windowMs: 60_000 },
  PAGE_VIEW: { limit: 120, windowMs: 60_000 },
};
