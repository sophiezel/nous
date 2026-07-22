/**
 * Data Service API client — 统一 REST 客户端
 * 
 * 缓存策略:
 *   SSE: 实时推送 (Client Component, 不走这里)
 *   SWR: stale-while-revalidate, 内存缓存 + 分级 TTL
 *   fetch: 直接请求 (无缓存, 用于风控/持仓等即时数据)
 */

const BASE_URL = process.env.DATA_SERVICE_URL || "http://127.0.0.1:3099";
const API_KEY = process.env.DATA_SERVICE_API_KEY || "";

// ── 分级 TTL ──────────────────────────────────────────
const TTL_MAP: Record<string, number> = {
  "/v1/sentiment": 120_000,      // 2min
  "/v1/macro": 1800_000,         // 30min
  "/v1/index": 300_000,          // 5min
  "/v1/global": 300_000,         // 5min
  "/v1/flow": 300_000,           // 5min
  "/v1/futures": 300_000,        // 5min
  "/v1/theme": 600_000,          // 10min
  "/v1/stock": 600_000,          // 10min
  "/v1/screen": 600_000,         // 10min
  "/v1/portfolio": 120_000,      // 2min
  "/v1/recommendations": 120_000,// 2min
  "/v1/risk": 120_000,           // 2min
  "/v1/messages": 60_000,        // 1min
  "/v1/quant": 300_000,          // 5min
  "default": 300_000,             // 5min
};

// ── Memory cache ──────────────────────────────────────
const cache = new Map<string, { data: unknown; ts: number }>();

function getTTL(path: string): number {
  for (const [prefix, ttl] of Object.entries(TTL_MAP)) {
    if (path.startsWith(prefix)) return ttl;
  }
  return TTL_MAP["default"];
}

// ── Public API ────────────────────────────────────────

/** Fetch with SWR caching */
export async function fetchAPI<T>(path: string, maxAge?: number): Promise<T> {
  const ttl = maxAge ?? getTTL(path);
  const cached = cache.get(path);

  if (cached && Date.now() - cached.ts < ttl) {
    return cached.data as T;
  }

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  const res = await fetch(`${BASE_URL}${path}`, {
    headers,
    signal: AbortSignal.timeout(5000),
  });

  if (!res.ok) {
    // 返回缓存兜底
    if (cached) return cached.data as T;
    throw new Error(`API ${path}: ${res.status}`);
  }

  const data = await res.json();
  cache.set(path, { data, ts: Date.now() });
  return data as T;
}

/** Fetch without caching (实时数据) */
export async function fetchLive<T>(path: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  const res = await fetch(`${BASE_URL}${path}`, {
    headers,
    signal: AbortSignal.timeout(5000),
  });

  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json() as T;
}

/** Get base URL for client-side SSE connections */
export function getSSEUrl(topics: string[]): string {
  return `${BASE_URL}/v1/sse/stream?topics=${topics.join(",")}`;
}

/** Get API base URL (for health checks) */
export function getApiBaseUrl(): string {
  return BASE_URL;
}
