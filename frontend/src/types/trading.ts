export type TradingMode = 'BACKTEST' | 'PAPER' | 'TESTNET' | 'LIVE';
export type OrderSide = 'BUY' | 'SELL';
export type OrderType = 'LIMIT' | 'MARKET' | 'STOP_MARKET' | 'TAKE_PROFIT_MARKET';
export type OrderStatus =
  | 'CREATED'
  | 'SUBMITTED'
  | 'ACKNOWLEDGED'
  | 'PARTIALLY_FILLED'
  | 'FILLED'
  | 'CANCELLED'
  | 'REJECTED'
  | 'EXPIRED'
  | 'UNKNOWN';

export type RiskDecisionType = 'APPROVED' | 'RESIZED' | 'REJECTED';

export interface Portfolio {
  strategy_id: string;
  equity: number;
  wallet_balance: number;
  available_balance: number;
  margin_used: number;
  unrealized_pnl: number;
  realized_pnl: number;
  funding_pnl: number;
  total_pnl: number;
  peak_equity: number;
  drawdown_pct: number;
  total_exposure: number;
  effective_leverage: number;
  position_count: number;
  open_orders_count: number;
  timestamp: string;
}

export interface Position {
  strategy_id: string;
  symbol: string;
  side: 'FLAT' | 'LONG' | 'SHORT';
  quantity: number;
  entry_price: number;
  mark_price: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  notional: number;
  initial_margin: number;
  maintenance_margin: number;
  liquidation_price: number | null;
  liquidation_distance_pct: number | null;
  leverage: number;
  margin_mode: 'ISOLATED' | 'CROSSED';
  is_open: boolean;
  last_updated: string;
}

export interface Order {
  client_order_id: string;
  exchange_order_id: string | null;
  strategy_id: string;
  symbol: string;
  side: OrderSide;
  order_type: OrderType;
  time_in_force: string;
  quantity: number;
  price: number | null;
  stop_price: number | null;
  status: OrderStatus;
  filled_qty: number;
  remaining_qty: number;
  avg_fill_price: number;
  cum_quote: number;
  fee_paid: number;
  fee_asset: string;
  latency_ms: number | null;
  created_at: string;
  updated_at: string;
}

export interface Strategy {
  strategy_id: string;
  strategy_name: string;
  is_active: boolean;
  environment: TradingMode;
  current_signal: string | null;
  signal_strength: number;
  total_signals: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  sharpe_ratio: number | null;
  max_drawdown_pct: number;
  parameters: Record<string, any>;
  last_signal_time: string | null;
}

export interface RiskLimitMetric {
  current: number;
  limit: number;
  utilization_pct: number;
  unit: string;
}

export interface RiskDecisionLog {
  signal_id: string;
  strategy_id: string;
  symbol: string;
  decision: RiskDecisionType;
  original_size: number;
  approved_size: number;
  reject_reason: string | null;
  evaluated_at: string;
}

export interface RiskStatus {
  trading_enabled: boolean;
  kill_switch_active: boolean;
  kill_switch_reason: string | null;
  max_position_size_usd: RiskLimitMetric;
  max_portfolio_exposure_usd: RiskLimitMetric;
  max_leverage: RiskLimitMetric;
  max_drawdown_pct: RiskLimitMetric;
  recent_decisions: RiskDecisionLog[];
}

export interface SystemComponentHealth {
  name: string;
  status: 'ONLINE' | 'DEGRADED' | 'HALTED' | 'SIMULATED' | 'LOCAL_MEMORY' | 'OFFLINE';
  details: string;
  last_heartbeat: string | null;
}

export interface SystemStatus {
  environment: TradingMode;
  live_trading_enabled: boolean;
  server_time_utc: string;
  clock_drift_ms: number;
  rtt_latency_ms: number | null;
  rate_limit_used_1m: number;
  rate_limit_max_1m: number;
  reconciliation_status: string;
  last_reconciliation_time: string | null;
  reconciliation_mismatches_count: number;
  components: SystemComponentHealth[];
}

export interface MarketTicker {
  symbol: string;
  mark_price: number;
  index_price: number | null;
  last_price: number | null;
  funding_rate: number | null;
  next_funding_time: string | null;
  price_change_percent_24h: number;
  high_price_24h: number;
  low_price_24h: number;
  volume_24h: number;
  quote_volume_24h: number;
  tick_size: number;
  step_size: number;
  min_notional: number;
  last_updated: string;
}

export interface FillMessage {
  fill_id: string;
  client_order_id: string;
  exchange_trade_id: string;
  strategy_id: string;
  symbol: string;
  side: OrderSide;
  price: number;
  quantity: number;
  fee: number;
  fee_asset: string;
}

export interface WebSocketMessage {
  event_type: string;
  timestamp: string;
  data: any;
}

export interface KlineCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  fast_sma?: number | null;
  slow_sma?: number | null;
}
