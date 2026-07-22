// Types for SQLite database rows (all columns are strings from SQLite TEXT storage)
export interface Report {
  id: number;
  type: string;
  title: string | null;
  content: string;
  metadata: string | null;  // JSON string
  created_at: string;
}

export interface MacroScore {
  date: string;
  score: number;
  position: number;
  indicators: string | null;  // JSON string
  created_at: string;
}

export interface SentimentData {
  date: string;
  score: number;
  limit_up_count: number;
  limit_up_rate: number;
  details: string | null;  // JSON string
  created_at: string;
}

export interface ThemePoolStock {
  theme: string;
  segment: string | null;
  symbol: string;
  name: string;
  price: number;
  change_pct: number;
  volume: number;
  source: string | null;
  comment: string | null;
  update_date: string;
}

export interface ThemePool {
  theme: string;
  stocks: ThemePoolStock[];
}

export interface HsgtStockItem {
  trade_date: string;
  symbol: string;
  name?: string;
  direction: string;
  rank: number;
  net_inflow: number;
  change_pct: number;
}

export interface HsgtSectorItem {
  sector: string;
  total_net_buy: number;
  buy_count: number;
  sell_count: number;
  top_buy_name: string | null;
  top_buy_value: number | null;
}

export interface EtfFlowItem {
  symbol: string;
  name: string;
  etf_type: string;
  pct_change: number;
  fund_size: number;
}

// ─── Trading Module Types ──────────────────────────────

/** A-share sim position */
export interface SimPosition {
  symbol: string;
  name: string;
  shares: number;
  cost_price: number;
  current_price: number;
  market_value: number;
  pnl: number;
  pnl_pct: number;
  weight_pct: number;
  update_time: string;
}

/** HK sim position */
export interface SimHkPosition {
  symbol: string;
  name: string;
  shares: number;
  cost_price: number;
  current_price: number;
  market_value: number;
  pnl: number;
  pnl_pct: number;
  weight_pct: number;
  update_time: string;
}

/** Sim portfolio snapshot (NAV) */
export interface SimNav {
  trade_date: string;
  nav: number;
  daily_return: number;
  total_pnl: number;
  total_invested: number;
}

/** Sim trade record */
export interface SimTrade {
  trade_date: string;
  symbol: string;
  name: string;
  direction: string;      // buy/sell
  price: number;
  shares: number;
  amount: number;
  pnl: number | null;
}

/** Quant position */
export interface QuantPosition {
  symbol: string;
  name: string;
  weight_pct: number;
  sector: string;
  factor_score: number;
  expected_return: number;
}

/** Quant NAV */
export interface QuantNav {
  trade_date: string;
  nav: number;
  daily_return: number;
  benchmark_return: number;
}

/** Quant trade */
export interface QuantTrade {
  trade_date: string;
  symbol: string;
  name: string;
  direction: string;
  price: number;
  shares: number;
  amount: number;
  reason: string | null;
}

/** Factor IC */
export interface FactorIC {
  date: string;
  factor_name: string;
  ic: number;
  rank_ic: number;
  significance: string;
}

/** Live portfolio (desensitized) */
export interface LivePosition {
  symbol: string;
  weight_pct: number;
  pnl_pct: number;
  sector: string;
}

/** Live portfolio diagnosis */
export interface LiveDiagnosis {
  metric: string;
  value: number;
  status: "good" | "warning" | "danger";
}

/** Risk overview */
export interface RiskOverview {
  total_drawdown_pct: number;
  current_drawdown_pct: number;
  var_95: number;
  volatility: number;
  sharpe: number;
  max_consecutive_losses: number;
  update_time: string;
}

/** Risk event / drawdown alert */
export interface RiskEvent {
  id: number;
  date: string;
  type: string;
  severity: string;
  symbol: string | null;
  message: string;
  metric_value: number;
  threshold: number;
  acknowledged: number;
}

/** Message */
export interface Message {
  id: number;
  type: string;
  title: string;
  content: string;
  is_read: number;
  created_at: string;
  pushed_weixin?: number;
}

/** Benchmark comparison */
export interface BenchmarkComparison {
  period: string;
  portfolio_return: number;
  benchmark_return: number;
  alpha: number;
  tracking_error: number;
  information_ratio: number;
}

/** Recommendation (荐股) from recommendation_history table */
export interface Recommendation {
  id: number;
  symbol: string;
  name: string;
  market: 'A' | 'HK';
  strategy_type: 'long_term' | 'short_term';
  status: string;
  entry_date: string;
  entry_avg_price: number;
  exit_date: string | null;
  exit_reason: string | null;
  pnl: number;
  pnl_pct: number;
  score: number;
  total_shares: number;
  total_amount: number;
  comment: string | null;
}

/** Strategy performance row */
export interface StrategyPerf {
  market: string;
  strategy_type: string;
  total_trades: number;
  wins: number;
  avg_return: number;
  avg_win: number;
  avg_loss: number;
}

/** Paper portfolio summary */
export interface PaperPortfolio {
  total_market_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  position_count: number;
  positions: SimPosition[] | SimHkPosition[];
}
