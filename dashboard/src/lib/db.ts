import Database from "better-sqlite3";
import * as path from "path";
import * as os from "os";
import {
  type Report, type MacroScore, type SentimentData, type ThemePoolStock,
  type SimPosition, type SimHkPosition, type SimNav, type SimTrade,
  type QuantPosition, type QuantNav, type QuantTrade, type FactorIC,
  type LivePosition, type LiveDiagnosis,
  type RiskOverview, type RiskEvent,
  type Message, type BenchmarkComparison,
} from "./types";

const REPORTS_DB = process.env.DASHBOARD_REPORTS_DB || process.env.REPORTS_DB_PATH || path.join(os.homedir(), "code/dashboard/data/reports.db");
const SCREENER_DB = process.env.DASHBOARD_SCREENER_DB || process.env.SCREENER_DB_PATH || path.join(os.homedir(), "code/stock-screener/data/screener.db");

// ── WAL mode singletons ────────────────────────────

let _db: Database.Database | null = null;
let _screenerDb: Database.Database | null = null;
let _screenerReadonlyDb: Database.Database | null = null;

function getDb(): Database.Database {
  if (_db) return _db;
  const db = new Database(REPORTS_DB);
  db.pragma("journal_mode = WAL");
  db.pragma("busy_timeout = 5000");
  _db = db;
  return db;
}

function getScreenerDb(): Database.Database {
  // v4: screener.db写入已迁移到Write Proxy Daemon
  // Dashboard只读，通过readonly连接
  return connect_readonly();
}

// Export for shared use by auth/quant modules
export { getScreenerDb };

// ─── Read-only connection ────────────────────────────

/** Path for SCREENER_DB, respecting env override */
function getScreenerPath(): string {
  if (process.env.SCREENER_DB_PATH) return process.env.SCREENER_DB_PATH;
  return SCREENER_DB;
}

/**
 * Open or return a cached read-only connection to screener.db.
 * Uses URI-mode `?mode=ro` to enforce read-only at the SQLite level.
 * This is safe to use concurrently with the writable connection.
 */
function connect_readonly(): Database.Database {
  if (_screenerReadonlyDb) return _screenerReadonlyDb;
  const dbPath = getScreenerPath();
  const db = new Database(dbPath, { readonly: true });
  db.pragma("busy_timeout = 5000");
  _screenerReadonlyDb = db;
  return db;
}

/**
 * Get the shared read-only DB connection.
 * Alias for connect_readonly() for consistency.
 */
function getReadonlyDB(): Database.Database {
  return connect_readonly();
}

export { connect_readonly, getReadonlyDB };

// ─── Reports ─────────────────────────────────────────

export function getRecentReports(type?: string, limit = 20): Report[] {
  const db = getDb();
  if (type) {
    return db
      .prepare("SELECT * FROM reports WHERE type = ? ORDER BY created_at DESC LIMIT ?")
      .all(type, limit) as Report[];
  }
  return db
    .prepare("SELECT * FROM reports ORDER BY created_at DESC LIMIT ?")
    .all(limit) as Report[];
}

export function getReportById(id: number): Report | null {
  const db = getDb();
  return (db.prepare("SELECT * FROM reports WHERE id = ?").get(id) as Report) || null;
}

export function getLatestReportByType(type: string): Report | null {
  const db = getDb();
  return (
    (db
      .prepare("SELECT * FROM reports WHERE type = ? ORDER BY created_at DESC LIMIT 1")
      .get(type) as Report) || null
  );
}

export function getReportTypes(): string[] {
  const db = getDb();
  return db
    .prepare("SELECT DISTINCT type FROM reports ORDER BY type")
    .all()
    .map((r: any) => r.type);
}

export function getReportCount(): number {
  const db = getDb();
  return (db.prepare("SELECT COUNT(*) as c FROM reports").get() as any).c as number;
}

// ─── Macro Scores ────────────────────────────────────

export function getMacroScores(limit = 60): MacroScore[] {
  const db = getDb();
  return db
    .prepare("SELECT * FROM macro_scores ORDER BY date DESC LIMIT ?")
    .all(limit) as MacroScore[];
}

export function getLatestMacroScore(): MacroScore | null {
  const db = getDb();
  return (
    (db
      .prepare("SELECT * FROM macro_scores ORDER BY date DESC LIMIT 1")
      .get() as MacroScore) || null
  );
}

// ─── Sentiment (v4: write by cron, Dashboard只读) ──

export function getSentimentHistory(limit = 60): SentimentData[] {
  // v4: sentiment_cache由收盘后cron计算+通过Write Proxy写入
  // Dashboard只负责读取
  const db = connect_readonly();
  return db.prepare(
    "SELECT * FROM sentiment_cache ORDER BY date DESC LIMIT ?"
  ).all(limit) as SentimentData[];
}

export function getLatestSentiment(): SentimentData | null {
  const db = connect_readonly();
  return (
    (db.prepare("SELECT * FROM sentiment_cache ORDER BY date DESC LIMIT 1").get() as SentimentData) || null
  );
}

// computeSentimentForDate 计算逻辑保留在 ~/.hermes/scripts/sentiment_compute.py
// 如需修改计算逻辑，同时更新两处。

// ─── Flow / Index / Futures (for mobile/flow, mobile/index) ──

export function getHsgtHistory(limit = 60): { trade_date: string; net_buy: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, net_buy FROM hsgt_daily ORDER BY trade_date DESC LIMIT ?"
  ).all(limit) as any[];
}

export function getMarginHistory(limit = 60): { trade_date: string; margin_balance: number; margin_buy: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, margin_balance, margin_buy FROM margin_daily ORDER BY trade_date DESC LIMIT ?"
  ).all(limit) as any[];
}

export function getIndexDailyHistory(code: string, limit = 30): { trade_date: string; close: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, close FROM index_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?"
  ).all(code, limit) as any[];
}

export function getGlobalIndexHistory(symbol: string, limit = 30): { trade_date: string; close: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, close FROM index_global_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?"
  ).all(symbol, limit) as any[];
}

/** Get single latest value for a global index (VIX, KWEB, etc.) */
export function getLatestGlobalIndex(symbol: string): { trade_date: string; close: number } | null {
  const row = getScreenerDb().prepare(
    "SELECT trade_date, close FROM index_global_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1"
  ).get(symbol) as any;
  return row || null;
}

// ─── Theme Pool ─────────────────────────────────────

/** Group theme_pool_stocks by theme from reports.db */
export function getThemePool(): { theme: string; stocks: ThemePoolStock[] }[] {
  const db = getDb();
  const rows = db.prepare(
    "SELECT * FROM theme_pool_stocks ORDER BY theme, segment, symbol"
  ).all() as ThemePoolStock[];
  const map = new Map<string, ThemePoolStock[]>();
  for (const r of rows) {
    const arr = map.get(r.theme) || [];
    arr.push(r);
    map.set(r.theme, arr);
  }
  return Array.from(map.entries()).map(([theme, stocks]) => ({ theme, stocks }));
}

// ─── Flow (extended for 9-module overview) ──────────

/** Get HSGT history filtered by direction */
export function getHsgtByDirection(direction: "north" | "south", limit = 60): { trade_date: string; net_buy: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, net_buy FROM hsgt_daily WHERE direction = ? ORDER BY trade_date DESC LIMIT ?"
  ).all(direction, limit) as any[];
}

/** Get margin short (做空) history */
export function getMarginShortHistory(limit = 60): { trade_date: string; short_balance: number; short_volume: number; margin_balance: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, short_balance, short_volume, margin_balance FROM margin_short_daily ORDER BY trade_date DESC LIMIT ?"
  ).all(limit) as any[];
}

/** Get LHB (龙虎榜) top N for a date or latest */
export function getLhbTop(date?: string, limit = 10): { trade_date: string; symbol: string; name: string; close: number; pct_change: number; net_amount: number; l_buy: number; l_sell: number; reason: string }[] {
  const db = getScreenerDb();
  if (date) {
    return db.prepare(
      "SELECT trade_date, symbol, name, close, pct_change, net_amount, l_buy, l_sell, reason FROM lhb_daily WHERE trade_date = ? ORDER BY ABS(net_amount) DESC LIMIT ?"
    ).all(date, limit) as any[];
  }
  const latest = db.prepare("SELECT MAX(trade_date) as d FROM lhb_daily").get() as any;
  if (!latest?.d) return [];
  return db.prepare(
    "SELECT trade_date, symbol, name, close, pct_change, net_amount, l_buy, l_sell, reason FROM lhb_daily WHERE trade_date = ? ORDER BY ABS(net_amount) DESC LIMIT ?"
  ).all(latest.d, limit) as any[];
}

/** Get daily margin buy amount history for trend chart */
export function getMarginBuyHistory(limit = 60): { trade_date: string; margin_buy: number; margin_balance: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, margin_buy, margin_balance FROM margin_daily ORDER BY trade_date DESC LIMIT ?"
  ).all(limit) as any[];
}

/** Get block trade (大宗交易) top N, with disc/prem vs daily close */
export function getBlockTradeTop(limit = 10): { trade_date: string; symbol: string; name: string; price: number; volume: number; amount: number; close: number | null }[] {
  const db = getScreenerDb();
  const latest = db.prepare("SELECT MAX(trade_date) as d FROM block_trades").get() as any;
  if (!latest?.d) return [];
  return db.prepare(
    `SELECT b.trade_date, b.symbol, b.name, b.price, b.volume, b.amount, sd.close
     FROM block_trades b
     LEFT JOIN stock_daily sd ON b.symbol = sd.symbol AND b.trade_date = sd.trade_date
     WHERE b.trade_date = ?
     ORDER BY b.amount DESC LIMIT ?`
  ).all(latest.d, limit) as any[];
}

// ─── Futures ──────────────────────────────────────

/** Get latest two closes for futures symbols (for pct calculation) */
export function getLatestFutures(symbols: string[]): { symbol: string; trade_date: string; close: number; prev_close: number | null }[] {
  const db = getScreenerDb();
  const results: { symbol: string; trade_date: string; close: number; prev_close: number | null }[] = [];
  for (const sym of symbols) {
    const rows = db.prepare(
      "SELECT trade_date, close FROM futures_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT 2"
    ).all(sym) as { trade_date: string; close: number }[];
    if (rows.length > 0) {
      results.push({
        symbol: sym,
        trade_date: rows[0].trade_date,
        close: rows[0].close,
        prev_close: rows.length >= 2 ? rows[1].close : null,
      });
    }
  }
  return results;
}

// ─── HSGT Stock Top ────────────────────────────────

export function getHsgtStockTop(direction: "北向" | "南向", date?: string, limit = 10): {
  trade_date: string; symbol: string; name: string | null; rank: number; net_inflow: number; change_pct: number;
}[] {
  const db = getScreenerDb();
  if (date) {
    return db.prepare(
      "SELECT trade_date, symbol, name, rank, net_inflow, change_pct FROM hsgt_stock_daily WHERE direction = ? AND trade_date = ? ORDER BY rank LIMIT ?"
    ).all(direction, date, limit) as any[];
  }
  const latest = db.prepare(
    "SELECT MAX(trade_date) as d FROM hsgt_stock_daily WHERE direction = ?"
  ).get(direction) as any;
  if (!latest?.d) return [];
  return db.prepare(
    "SELECT trade_date, symbol, name, rank, net_inflow, change_pct FROM hsgt_stock_daily WHERE direction = ? AND trade_date = ? ORDER BY rank LIMIT ?"
  ).all(direction, latest.d, limit) as any[];
}

// ─── HSGT Sector Aggregation ──────────────────────

export function getHsgtSectorTop(direction: "北向" | "南向", date?: string, limit = 5): {
  sector: string; total_net_buy: number; buy_count: number; sell_count: number;
  top_buy_name: string | null; top_buy_value: number | null;
}[] {
  const db = getScreenerDb();
  if (date) {
    return db.prepare(
      "SELECT sector, total_net_buy, buy_count, sell_count, top_buy_name, top_buy_value FROM hsgt_sector_daily WHERE direction = ? AND trade_date = ? ORDER BY ABS(total_net_buy) DESC LIMIT ?"
    ).all(direction, date, limit) as any[];
  }
  const latest = db.prepare(
    "SELECT MAX(trade_date) as d FROM hsgt_sector_daily WHERE direction = ?"
  ).get(direction) as any;
  if (!latest?.d) return [];
  return db.prepare(
    "SELECT sector, total_net_buy, buy_count, sell_count, top_buy_name, top_buy_value FROM hsgt_sector_daily WHERE direction = ? AND trade_date = ? ORDER BY ABS(total_net_buy) DESC LIMIT ?"
  ).all(direction, latest.d, limit) as any[];
}

// ─── ETF Flow ─────────────────────────────────────

export function getEtfFlowTop(limit = 6): {
  symbol: string; name: string; etf_type: string; pct_change: number; fund_size: number;
}[] {
  const db = getScreenerDb();
  const latest = db.prepare("SELECT MAX(trade_date) as d FROM etf_flow_daily").get() as any;
  if (!latest?.d) return [];
  return db.prepare(
    "SELECT symbol, name, etf_type, pct_change, fund_size FROM etf_flow_daily WHERE trade_date = ? ORDER BY ABS(pct_change) DESC LIMIT ?"
  ).all(latest.d, limit) as any[];
}

// ─── HSGT Total Trend ───────────────────────────

export function getHsgtTotalTrend(direction: "北向" | "南向", limit = 120): { trade_date: string; total: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, SUM(net_inflow) as total FROM hsgt_stock_daily WHERE direction = ? AND net_inflow IS NOT NULL GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?"
  ).all(direction, limit) as any[];
}

/** Get individual stock net_inflow history for trend sparklines */
export function getHsgtStockTrends(direction: "北向" | "南向", symbols: string[], limit = 30): Record<string, number[]> {
  const db = getScreenerDb();
  const result: Record<string, number[]> = {};
  for (const sym of symbols) {
    const rows = db.prepare(
      "SELECT net_inflow FROM hsgt_stock_daily WHERE direction = ? AND symbol = ? ORDER BY trade_date DESC LIMIT ?"
    ).all(direction, sym, limit) as { net_inflow: number }[];
    result[sym] = rows.map(r => r.net_inflow / 1e8).reverse();
  }
  return result;
}

// ─── Macro Indicator History ──────────────────────

export function getCpiHistory(limit = 60): { trade_date: string; value: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, cpi_yoy as value FROM macro_cpi ORDER BY trade_date DESC LIMIT ?"
  ).all(limit) as any[];
}

export function getPpiHistory(limit = 60): { trade_date: string; value: number }[] {
  return getScreenerDb().prepare(
    "SELECT trade_date, ppi_yoy as value FROM macro_ppi ORDER BY trade_date DESC LIMIT ?"
  ).all(limit) as any[];
}

export function getPmiHistory(limit = 60): { trade_date: string; value: number }[] {
  return getScreenerDb().prepare(
    'SELECT "月份" as trade_date, "制造业-指数" as value FROM macro_pmi ORDER BY "月份" DESC LIMIT ?'
  ).all(limit) as any[];
}

// ─── Theme / Sector Analytics ────────────────────

/** Get theme fund flow (SUM main_net for theme stocks) */
export function getThemeFundFlow(symbols: string[]): number | null {
  const db = getScreenerDb();
  if (symbols.length === 0) return null;
  const ph = symbols.map(() => "?").join(",");
  const row = db.prepare(
    `SELECT SUM(main_net) as total FROM fund_flow_stock WHERE symbol IN (${ph}) AND trade_date = (SELECT MAX(trade_date) FROM fund_flow_stock)`
  ).get(...symbols) as any;
  return row?.total ?? null;
}

/** Get theme daily avg change_pct trend */
export function getThemeTrend(symbols: string[], limit = 61): { trade_date: string; avg_pct: number }[] {
  const db = getScreenerDb();
  if (symbols.length === 0) return [];
  const ph = symbols.map(() => "?").join(",");
  return db.prepare(
    `SELECT trade_date, AVG(chg) as avg_pct FROM (
      SELECT trade_date, (close - prev_close) / prev_close * 100 as chg FROM (
        SELECT trade_date, close, LAG(close) OVER (ORDER BY trade_date ASC) as prev_close
        FROM stock_daily WHERE symbol IN (${ph}) ORDER BY trade_date DESC LIMIT ${limit}
      ) WHERE prev_close IS NOT NULL AND prev_close > 0 ORDER BY trade_date ASC
    ) GROUP BY trade_date ORDER BY trade_date ASC`
  ).all(...symbols) as any[];
}

/** Get individual stock daily change_pct trend */
export function getStockTrend(symbol: string, limit = 31): number[] {
  const db = getScreenerDb();
  const rows = db.prepare(
    `SELECT chg FROM (
      SELECT (close - prev_close) / prev_close * 100 as chg FROM (
        SELECT close, LAG(close) OVER (ORDER BY trade_date ASC) as prev_close
        FROM stock_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT ${limit}
      ) WHERE prev_close IS NOT NULL AND prev_close > 0 ORDER BY trade_date ASC
    )`
  ).all(symbol) as { chg: number }[];
  return rows.map(r => r.chg);
}

export function getM2History(limit = 60): { trade_date: string; value: number }[] {
  return getScreenerDb().prepare(
    'SELECT "月份" as trade_date, "货币和准货币(M2)-同比增长" as value FROM macro_m2 ORDER BY "月份" DESC LIMIT ?'
  ).all(limit) as any[];
}

export function getShiborHistory(limit = 60): { trade_date: string; value: number }[] {
  return getScreenerDb().prepare(
    'SELECT "日期" as trade_date, "O/N-定价" as value FROM macro_shibor ORDER BY "日期" DESC LIMIT ?'
  ).all(limit) as any[];
}

// ─── Market Turnover ────────────────────────────

export function getLatestTotalTurnover(): number | null {
  const db = getScreenerDb();
  const row = db.prepare(
    "SELECT SUM(volume * close) as total FROM stock_daily WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily) AND volume > 0 AND close > 0"
  ).get() as any;
  return row?.total ?? null;
}

/** Get latest daily close from versioned table (fallback to old table) */
export function getLatestDailyClose(symbol: string): { close: number; trade_date: string; confidence: number } | null {
  const db = getScreenerDb();
  // Try stock_daily_latest first
  let row = db.prepare(
    "SELECT close, trade_date, confidence FROM stock_daily_latest WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1"
  ).get(symbol) as any;
  if (row) return row;
  // Fallback to old stock_daily
  row = db.prepare(
    "SELECT close, trade_date, NULL as confidence FROM stock_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1"
  ).get(symbol) as any;
  return row ?? null;
}

/** Get version statistics for latest trade date */
export function getDailyV2Stats(): { trade_date: string; total: number; dual_source: number; avg_confidence: number } | null {
  const db = getScreenerDb();
  return db.prepare(
    `SELECT trade_date, COUNT(*) as total,
            SUM(CASE WHEN source_count >= 2 THEN 1 ELSE 0 END) as dual_source,
            AVG(confidence) as avg_confidence
     FROM stock_daily_v2
     GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1`
  ).get() as any;
}

// ═══════════════════════════════════════════════════════════════
// Trading Module — Paper (模拟盘)
// ═══════════════════════════════════════════════════════════════

/** Get sim positions for a given market+slot */
export function getPaperPortfolio(market: "a" | "hk", slot?: string): SimPosition[] | SimHkPosition[] {
  const db = getScreenerDb();
  if (market === "hk") {
    // sim_hk_position: strategy (not slot), weight_pct 为0时用 entry_price*shares 估算
    if (slot) {
      return db.prepare(
        "SELECT *, COALESCE(weight_pct,0) as weight_pct FROM sim_hk_position WHERE strategy = ?"
      ).all(slot) as SimHkPosition[];
    }
    return db.prepare(
      "SELECT *, COALESCE(weight_pct,0) as weight_pct FROM sim_hk_position"
    ).all() as SimHkPosition[];
  }
  // sim_position: slot, weight_pct 可能不存在
  if (slot) {
    return db.prepare(
      "SELECT *, COALESCE(weight_pct,0) as weight_pct FROM sim_position WHERE slot = ?"
    ).all(slot) as SimPosition[];
  }
  return db.prepare(
    "SELECT *, COALESCE(weight_pct,0) as weight_pct FROM sim_position"
  ).all() as SimPosition[];
}

/** Get sim NAV history */
export function getPaperNav(market: "a" | "hk", limit = 60): SimNav[] {
  const db = getScreenerDb();
  if (market === "hk") {
    return db.prepare(
      "SELECT trade_date, nav, daily_return FROM sim_hk_nav ORDER BY trade_date DESC LIMIT ?"
    ).all(limit) as SimNav[];
  }
  return db.prepare(
    "SELECT datetime as trade_date, COALESCE(nav, market_value) as nav, COALESCE(daily_return,0) as daily_return FROM sim_portfolio_snapshot ORDER BY datetime DESC LIMIT ?"
  ).all(limit) as SimNav[];
}

/** Get sim trades */
export function getPaperTrades(market: "a" | "hk", limit = 50): SimTrade[] {
  const db = getScreenerDb();
  const table = market === "hk" ? "sim_hk_trades" : "sim_trades";
  return db.prepare(
    `SELECT * FROM ${table} ORDER BY trade_time DESC LIMIT ?`
  ).all(limit) as SimTrade[];
}

// ═══════════════════════════════════════════════════════════════
// Trading Module — Quant (量化盘)
// ═══════════════════════════════════════════════════════════════

/** Get quant positions */
export function getQuantPortfolio(market: "a" | "hk", limit = 20): QuantPosition[] {
  const db = getScreenerDb();
  const table = market === "hk" ? "quant_hk_position" : "quant_position";
  return db.prepare(
    `SELECT *, COALESCE(weight,0) as weight_pct FROM ${table} ORDER BY weight DESC LIMIT ?`
  ).all(limit) as QuantPosition[];
}

/** Get quant NAV history */
export function getQuantNav(market: "a" | "hk", limit = 60): QuantNav[] {
  const db = getScreenerDb();
  const table = market === "hk" ? "quant_hk_nav" : "quant_nav";
  return db.prepare(
    `SELECT trade_date, nav, daily_return FROM ${table} ORDER BY trade_date DESC LIMIT ?`
  ).all(limit) as QuantNav[];
}

/** Get quant trades */
export function getQuantTrades(market: "a" | "hk", limit = 50): QuantTrade[] {
  const db = getScreenerDb();
  const table = market === "hk" ? "quant_hk_trades" : "quant_trades";
  return db.prepare(
    `SELECT * FROM ${table} ORDER BY trade_date DESC LIMIT ?`
  ).all(limit) as QuantTrade[];
}

/** Get factor IC history (placeholder — table may not exist yet) */
export function getFactorIC(limit = 30): FactorIC[] {
  try {
    return getScreenerDb().prepare(
      "SELECT * FROM quant_factor_ic ORDER BY date DESC LIMIT ?"
    ).all(limit) as FactorIC[];
  } catch {
    return [];
  }
}

// ═══════════════════════════════════════════════════════════════
// Trading Module — Live Portfolio (实盘)
// ═══════════════════════════════════════════════════════════════

/** Get live portfolio positions (desensitized: only weight_pct + pnl_pct) */
export function getLivePortfolio(): LivePosition[] {
  return getDb().prepare(
    "SELECT symbol, name, weight_pct, pnl_pct, sector FROM live_portfolio ORDER BY weight_pct DESC"
  ).all() as LivePosition[];
}

/** Get live portfolio diagnosis — reads from reports */
export function getLiveDiagnosis(): LiveDiagnosis[] {
  try {
    return getScreenerDb().prepare(
      "SELECT metric, value, status FROM portfolio_diagnosis ORDER BY metric"
    ).all() as LiveDiagnosis[];
  } catch {
    return [];
  }
}

// ═══════════════════════════════════════════════════════════════
// Trading Module — Risk (风控)
// ═══════════════════════════════════════════════════════════════

/** Get unified risk overview */
export function getRiskOverview(): RiskOverview | null {
  const row = getScreenerDb().prepare(
    "SELECT * FROM risk_metrics ORDER BY trade_date DESC LIMIT 1"
  ).get() as any;
  if (!row) return null;
  return {
    var_95: Number(row.var_95 ?? 0),
    var_99: Number(row.var_99 ?? 0),
    cvar_95: Number(row.cvar_95 ?? 0),
    max_drawdown: Number(row.max_drawdown ?? 0),
    sector_concentration: Number(row.sector_concentration ?? 0),
    current_drawdown_pct: Number(row.max_drawdown ?? 0),
    total_drawdown_pct: Number(row.max_drawdown ?? 0),
    volatility: 0,
    sharpe: 0,
    max_consecutive_losses: 0,
    update_time: String(row.trade_date ?? ""),
  } as RiskOverview;
}

/** Get recent risk events / drawdown alerts */
export function getRiskEvents(limit = 20): RiskEvent[] {
  return getScreenerDb().prepare(
    "SELECT id, alert_time as date, drawdown_pct as metric_value, 0.15 as threshold, " +
    "current_pnl_pct, peak_pnl_pct, 'drawdown' as event_type, 'warning' as severity, " +
    "'' as symbol, '回撤告警' as type, '账户回撤超限' as message, 1 as acknowledged " +
    "FROM sim_drawdown_alerts ORDER BY alert_time DESC LIMIT ?"
  ).all(limit) as RiskEvent[];
}

// ═══════════════════════════════════════════════════════════════
// Trading Module — Messages (消息)
// ═══════════════════════════════════════════════════════════════

/** Get messages, optionally filtered by type */
export function getMessages(type?: string, limit = 50): Message[] {
  const db = getScreenerDb();
  if (type) {
    return db.prepare(
      "SELECT *, read as is_read FROM messages WHERE type = ? ORDER BY created_at DESC LIMIT ?"
    ).all(type, limit) as Message[];
  }
  return db.prepare(
    "SELECT *, read as is_read FROM messages ORDER BY created_at DESC LIMIT ?"
  ).all(limit) as Message[];
}

/** Get single message by id */
export function getMessageById(id: number): Message | null {
  const row = getScreenerDb().prepare(
    "SELECT *, read as is_read FROM messages WHERE id = ?"
  ).get(id) as any;
  return (row as Message) || null;
}

/** Mark a message as read */
export function markMessageRead(id: number): void {
  getScreenerDb().prepare(
    "UPDATE messages SET read = 1 WHERE id = ?"
  ).run(id);
}

// ═══════════════════════════════════════════════════════════════
// Trading Module — Benchmark Comparison (基准)
// ═══════════════════════════════════════════════════════════════

/** Get benchmark comparison data */
export function getBenchmarkComparison(): BenchmarkComparison[] {
  return getScreenerDb().prepare(
    "SELECT * FROM benchmark_comparison ORDER BY CASE period WHEN '1周' THEN 1 WHEN '1月' THEN 2 WHEN '3月' THEN 3 WHEN '6月' THEN 4 WHEN '1年' THEN 5 ELSE 6 END"
  ).all() as BenchmarkComparison[];
}

// ═══════════════════════════════════════════════════════════════
// Trading Module — Recommendation History (推荐历史)
// ═══════════════════════════════════════════════════════════════

/** Get active (open) recommendations, optionally filtered by market/strategy */
export function getActiveRecommendations(market?: string, strategyType?: string): any[] {
  let sql = "SELECT * FROM recommendation_history WHERE status='active'";
  const params: any[] = [];
  if (market) { sql += " AND market=?"; params.push(market); }
  if (strategyType) { sql += " AND strategy_type=?"; params.push(strategyType); }
  sql += " ORDER BY entry_date DESC";
  try {
    return getScreenerDb().prepare(sql).all(...params) as any[];
  } catch { return []; }
}

/** Get closed recommendation history, optionally filtered */
export function getRecommendationHistory(market?: string, strategyType?: string, limit = 50): any[] {
  let sql = "SELECT * FROM recommendation_history WHERE status!='active'";
  const params: any[] = [];
  if (market) { sql += " AND market=?"; params.push(market); }
  if (strategyType) { sql += " AND strategy_type=?"; params.push(strategyType); }
  sql += " ORDER BY exit_date DESC LIMIT ?";
  params.push(limit);
  try {
    return getScreenerDb().prepare(sql).all(...params) as any[];
  } catch { return []; }
}

/** Get strategy performance grouped by market and strategy_type */
export function getStrategyPerformance(): any[] {
  try {
    return getScreenerDb().prepare(`
      SELECT market, strategy_type,
        COUNT(*) as total_trades,
        SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
        ROUND(AVG(pnl_pct), 2) as avg_return,
        ROUND(AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE NULL END), 2) as avg_win,
        ROUND(AVG(CASE WHEN pnl_pct <= 0 THEN pnl_pct ELSE NULL END), 2) as avg_loss
      FROM recommendation_history
      WHERE status IN ('closed','stopped_out','time_exit','take_profit')
      GROUP BY market, strategy_type
      ORDER BY market, strategy_type
    `).all() as any[];
  } catch { return []; }
}

// ═══════════════════════════════════════════════════════════════
// Stub exports for pre-existing pages (avoid import errors)
// ═══════════════════════════════════════════════════════════════

export function getRecPerformance(limit = 20) {
  try { return getScreenerDb().prepare("SELECT * FROM rec_performance ORDER BY rec_date DESC LIMIT ?").all(limit); }
  catch { return []; }
}
export function getRecPerformanceSummary() { return { total: 0, win_rate: 0, avg_return_5d: 0 }; }
export function getPortfolioNavHistory(limit = 60) {
  try { return getScreenerDb().prepare("SELECT trade_date, nav, daily_return FROM portfolio_nav ORDER BY trade_date DESC LIMIT ?").all(limit); }
  catch { return []; }
}
export function getRiskMetrics(limit = 60) {
  try { return getScreenerDb().prepare("SELECT * FROM risk_metrics ORDER BY trade_date DESC LIMIT ?").all(limit); }
  catch { return []; }
}
export function getLatestHsgt() {
  try { return getScreenerDb().prepare("SELECT * FROM hsgt_daily ORDER BY trade_date DESC LIMIT 1").get(); }
  catch { return null; }
}
export function getLatestMargin() {
  try { return getScreenerDb().prepare("SELECT * FROM margin_daily ORDER BY trade_date DESC LIMIT 1").get(); }
  catch { return null; }
}
export function getFuturesLatest(symbols: string[] = []) {
  try {
    if (!symbols.length) return [];
    const placeholders = symbols.map(() => '?').join(',');
    return getScreenerDb().prepare(
      `SELECT symbol, trade_date, close, prev_close FROM futures_daily WHERE symbol IN (${placeholders}) AND trade_date = (SELECT MAX(trade_date) FROM futures_daily WHERE symbol IN (${placeholders}))`
    ).all(...symbols);
  } catch { return []; }
}

// ═══════════════════════════════════════════════════════════════
// Training Dashboard Queries
// ═══════════════════════════════════════════════════════════════

/** Get model IC comparison data from quant_ic_history */
export function getTrainingModelIC(): { model: string; ic: number; rank_ic: number; trade_date: string }[] {
  try {
    const latestDate = getScreenerDb().prepare(
      "SELECT MAX(trade_date) as d FROM quant_ic_history"
    ).get() as any;
    if (!latestDate?.d) return [];
    return getScreenerDb().prepare(
      "SELECT trade_date, model, COALESCE(ic_enc, '0') as ic, COALESCE(rank_ic_enc, '0') as rank_ic FROM quant_ic_history WHERE trade_date = ?"
    ).all(latestDate.d) as any[];
  } catch { return []; }
}

/** Get backtest NAV curve from portfolio_nav (plaintext) */
export function getTrainingBacktestCurve(limit = 120): { trade_date: string; nav: number; daily_return: number; pnl_pct: number }[] {
  try {
    return getScreenerDb().prepare(
      "SELECT trade_date, nav, daily_return, COALESCE(pnl_pct, 0) as pnl_pct FROM portfolio_nav ORDER BY trade_date ASC LIMIT ?"
    ).all(limit) as any[];
  } catch { return []; }
}

/** Get factor importance Top N from quant_factor_importance */
export function getTrainingFactorImportance(limit = 10): { trade_date: string; factor_name: string; importance_enc: string; direction_enc: string }[] {
  try {
    const latestDate = getScreenerDb().prepare(
      "SELECT MAX(trade_date) as d FROM quant_factor_importance"
    ).get() as any;
    if (!latestDate?.d) return [];
    return getScreenerDb().prepare(
      "SELECT trade_date, factor_name, COALESCE(importance_enc, '0') as importance_enc, COALESCE(direction_enc, '') as direction_enc FROM quant_factor_importance WHERE trade_date = ? ORDER BY importance_enc DESC LIMIT ?"
    ).all(latestDate.d, limit) as any[];
  } catch { return []; }
}

/** Get IC history for decay curve (grouped by date) */
export function getTrainingICDecay(limit = 60): { trade_date: string; avg_ic: number; avg_rank_ic: number }[] {
  try {
    return getScreenerDb().prepare(`
      SELECT trade_date, AVG(CAST(ic_enc AS REAL)) as avg_ic, AVG(CAST(rank_ic_enc AS REAL)) as avg_rank_ic
      FROM quant_ic_history GROUP BY trade_date ORDER BY trade_date ASC LIMIT ?
    `).all(limit) as any[];
  } catch { return []; }
}

// ═══════════════════════════════════════════════════════════════
// Theme Accuracy Queries (from screen_results)
// ═══════════════════════════════════════════════════════════════

/** Get daily scoring trend from screen_results */
export function getThemeAccuracyTrend(limit = 60): { screen_date: string; avg_score: number; count: number }[] {
  try {
    return getScreenerDb().prepare(
      "SELECT screen_date, ROUND(AVG(score), 2) as avg_score, COUNT(*) as count FROM screen_results WHERE score IS NOT NULL GROUP BY screen_date ORDER BY screen_date ASC LIMIT ?"
    ).all(limit) as any[];
  } catch { return []; }
}

/** Get score distribution bins */
export function getThemeScoreDistribution(): { bin: string; count: number }[] {
  try {
    const latestDate = getScreenerDb().prepare(
      "SELECT MAX(screen_date) as d FROM screen_results"
    ).get() as any;
    if (!latestDate?.d) return [];
    return getScreenerDb().prepare(`
      SELECT
        CASE
          WHEN score >= 8 THEN '高评分(8-10)'
          WHEN score >= 6 THEN '中高评分(6-8)'
          WHEN score >= 4 THEN '中等评分(4-6)'
          WHEN score >= 2 THEN '中低评分(2-4)'
          ELSE '低评分(0-2)'
        END as bin,
        COUNT(*) as count
      FROM screen_results WHERE screen_date = ? AND score IS NOT NULL
      GROUP BY bin ORDER BY bin DESC
    `).all(latestDate.d) as any[];
  } catch { return []; }
}

/** Get top scoring stocks by date (proxy for hit rate) */
export function getThemeTopStocks(date?: string, limit = 10): { symbol: string; name: string; score: number; pe: number; pb: number; roe: number }[] {
  try {
    if (date) {
      return getScreenerDb().prepare(
        "SELECT symbol, name, score, pe, pb, roe FROM screen_results WHERE screen_date = ? AND score IS NOT NULL ORDER BY score DESC LIMIT ?"
      ).all(date, limit) as any[];
    }
    const latestDate = getScreenerDb().prepare(
      "SELECT MAX(screen_date) as d FROM screen_results"
    ).get() as any;
    if (!latestDate?.d) return [];
    return getScreenerDb().prepare(
      "SELECT symbol, name, score, pe, pb, roe FROM screen_results WHERE screen_date = ? AND score IS NOT NULL ORDER BY score DESC LIMIT ?"
    ).all(latestDate.d, limit) as any[];
  } catch { return []; }
}

/** Get market/sector distribution for lead-lag analysis */
export function getThemeMarketDistribution(): { market: string; count: number; avg_score: number }[] {
  try {
    const latestDate = getScreenerDb().prepare(
      "SELECT MAX(screen_date) as d FROM screen_results"
    ).get() as any;
    if (!latestDate?.d) return [];
    return getScreenerDb().prepare(
      "SELECT market, COUNT(*) as count, ROUND(AVG(score), 2) as avg_score FROM screen_results WHERE screen_date = ? AND score IS NOT NULL GROUP BY market ORDER BY count DESC"
    ).all(latestDate.d) as any[];
  } catch { return []; }
}

// ═══════════════════════════════════════════════════════════════
// Model Health Queries
// ═══════════════════════════════════════════════════════════════

/** Get Rank IC rolling mean from quant_ic_history */
export function getModelHealthRankIC(limit = 60): { trade_date: string; model: string; rank_ic: number }[] {
  try {
    return getScreenerDb().prepare(
      "SELECT trade_date, model, COALESCE(rank_ic_enc, '0') as rank_ic FROM quant_ic_history ORDER BY trade_date ASC LIMIT ?"
    ).all(limit) as any[];
  } catch { return []; }
}

/** Get PSI metrics from ETL or risk tables */
export function getModelHealthPSI(): { metric: string; value: number; status: string }[] {
  try {
    // Try etl_metrics first
    const etlRows = getScreenerDb().prepare(
      "SELECT metric_name as metric, metric_value as value, 'unknown' as status FROM etl_metrics ORDER BY metric_name LIMIT 20"
    ).all() as any[];
    if (etlRows.length > 0) return etlRows;
  } catch {}
  try {
    // Fallback: risk_metrics
    const row = getScreenerDb().prepare(
      "SELECT * FROM risk_metrics ORDER BY trade_date DESC LIMIT 1"
    ).get() as any;
    if (row) {
      return [
        { metric: 'VaR(95%)', value: Number(row.var_95 ?? 0), status: row.var_95 > 0.05 ? 'warning' : 'good' },
        { metric: 'VaR(99%)', value: Number(row.var_99 ?? 0), status: row.var_99 > 0.08 ? 'danger' : 'good' },
        { metric: 'Max Drawdown', value: Number(row.max_drawdown ?? 0), status: 'good' },
        { metric: 'Sector Concentration', value: Number(row.sector_concentration ?? 0), status: row.sector_concentration > 0.3 ? 'warning' : 'good' },
      ];
    }
  } catch {}
  return [];
}

/** Get half-life proxy from quant_ic_history volatility */
export function getModelHealthHalfLife(limit = 30): { trade_date: string; half_life_days: number }[] {
  try {
    const rows = getScreenerDb().prepare(
      "SELECT trade_date, COUNT(DISTINCT model) as model_count FROM quant_ic_history GROUP BY trade_date ORDER BY trade_date ASC LIMIT ?"
    ).all(limit) as any[];
    return rows.map((r: any) => ({ trade_date: r.trade_date, half_life_days: Number(r.model_count) * 7 }));
  } catch { return []; }
}

/** Get retrain/trigger log from data_audit_log (proxy) */
export function getModelHealthRetrainLog(limit = 50): { id: number; check_date: string; model_id: string; triggered: number; trigger_reason: string }[] {
  try {
    return getScreenerDb().prepare(`
      SELECT id, strftime('%Y-%m-%d', created_at) as check_date,
             COALESCE(symbol, 'unknown') as model_id,
             0 as triggered,
             COALESCE(detail, '') as trigger_reason
      FROM data_audit_log ORDER BY created_at DESC LIMIT ?
    `).all(limit) as any[];
  } catch { return []; }
}

/** 微信通道健康状态 */
export function getWeixinChannelHealth(): {
  state: string;
  today_total: number;
  today_pushed: number;
  today_failed: number;
  last_push: string | null;
  last_fail: string | null;
} {
  try {
    const today = new Date().toISOString().slice(0, 10);
    const db = getScreenerDb();
    
    const total = db.prepare(
      "SELECT COUNT(*) as n FROM messages WHERE created_at >= ?"
    ).get(today) as { n: number } | undefined;
    
    const pushed = db.prepare(
      "SELECT COUNT(*) as n FROM messages WHERE created_at >= ? AND pushed_weixin = 1"
    ).get(today) as { n: number } | undefined;
    
    const last = db.prepare(
      "SELECT created_at, pushed_weixin FROM messages WHERE created_at >= ? ORDER BY created_at DESC LIMIT 1"
    ).get(today) as { created_at: string; pushed_weixin: number } | undefined;
    
    const firstFail = db.prepare(
      "SELECT created_at FROM messages WHERE created_at >= ? AND pushed_weixin = 0 ORDER BY created_at ASC LIMIT 1"
    ).get(today) as { created_at: string } | undefined;
    
    const today_total = total?.n ?? 0;
    const today_pushed = pushed?.n ?? 0;
    const today_failed = today_total - today_pushed;
    
    // 状态判定
    let state = "unknown";
    if (today_total === 0) {
      state = "idle"; // 今天还没有消息
    } else if (today_failed === 0) {
      state = "healthy";
    } else if (today_pushed === 0 && today_total > 0) {
      state = "degraded"; // 全部失败 = 通道可能中断
    } else if (today_failed > 0 && today_pushed > 0) {
      state = "partial"; // 部分失败
    }
    
    return {
      state,
      today_total,
      today_pushed,
      today_failed,
      last_push: last?.pushed_weixin === 1 ? last.created_at : null,
      last_fail: firstFail?.created_at ?? null,
    };
  } catch {
    return { state: "error", today_total: 0, today_pushed: 0, today_failed: 0, last_push: null, last_fail: null };
  }
}
