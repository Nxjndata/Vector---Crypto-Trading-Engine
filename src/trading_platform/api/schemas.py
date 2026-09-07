"""Strongly-typed Pydantic schemas for the Trading Platform Monitoring & Control API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from trading_platform.core.constants import (
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskDecisionType,
    TimeInForce,
    TradingMode,
)


class BaseResponse(BaseModel):
    """Base API response wrapper."""

    success: bool = True
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------------------------
# 1. Portfolio Schemas
# ------------------------------------------------------------------------------


class PortfolioResponse(BaseModel):
    """Real-time consolidated portfolio state."""

    strategy_id: str
    equity: float
    wallet_balance: float
    available_balance: float
    margin_used: float
    unrealized_pnl: float
    realized_pnl: float
    funding_pnl: float
    total_pnl: float
    peak_equity: float
    drawdown_pct: float
    total_exposure: float
    effective_leverage: float
    position_count: int
    open_orders_count: int
    timestamp: datetime


# ------------------------------------------------------------------------------
# 2. Position Schemas
# ------------------------------------------------------------------------------


class PositionResponse(BaseModel):
    """Active single position telemetry."""

    strategy_id: str
    symbol: str
    side: str  # FLAT, LONG, SHORT
    quantity: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    notional: float
    initial_margin: float
    maintenance_margin: float
    liquidation_price: float | None
    liquidation_distance_pct: float | None
    leverage: float
    margin_mode: MarginMode
    is_open: bool
    last_updated: datetime


# ------------------------------------------------------------------------------
# 3. Order Schemas
# ------------------------------------------------------------------------------


class OrderResponse(BaseModel):
    """Order status and lifecycle execution metrics."""

    client_order_id: str
    exchange_order_id: str | None
    strategy_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    quantity: float
    price: float | None
    stop_price: float | None
    status: OrderStatus
    filled_qty: float
    remaining_qty: float
    avg_fill_price: float
    cum_quote: float
    fee_paid: float
    fee_asset: str
    latency_ms: float | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------------------
# 4. Strategy Schemas
# ------------------------------------------------------------------------------


class StrategyResponse(BaseModel):
    """Active strategy status, parameters, and performance."""

    strategy_id: str
    strategy_name: str
    is_active: bool
    environment: TradingMode
    current_signal: str | None  # BUY, SELL, HOLD, None
    signal_strength: float
    total_signals: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    sharpe_ratio: float | None
    max_drawdown_pct: float
    parameters: dict[str, Any]
    last_signal_time: datetime | None


class ForceTestSignalRequest(BaseModel):
    """Payload to trigger a manual operator test signal through the Risk Engine and OMS."""

    symbol: str = Field(default="BTCUSDT", description="Symbol to trade (e.g. BTCUSDT)")
    side: OrderSide = Field(default=OrderSide.BUY, description="BUY for LONG exposure, SELL for SHORT exposure")
    target_exposure: float = Field(
        default=0.02,
        ge=0.005,
        le=0.10,
        description="Signed fraction of portfolio equity (e.g. 0.02 for 2%)",
    )
    reason: str = Field(..., min_length=3, description="Audit reason for manual test trade execution")
    confirm: bool = Field(..., description="Explicit confirmation boolean")


class ForceTestSignalResponse(BaseModel):
    """Outcome of manual test signal submission."""

    success: bool
    signal_id: str
    symbol: str
    side: OrderSide
    target_exposure: float
    mark_price: float
    status: str
    message: str
    timestamp: datetime


# ------------------------------------------------------------------------------
# 5. Risk Schemas & Control Actions
# ------------------------------------------------------------------------------


class RiskLimitMetric(BaseModel):
    """Current utilization vs hard threshold."""

    current: float
    limit: float
    utilization_pct: float
    unit: str


class RiskDecisionLogResponse(BaseModel):
    """Audit log entry for a risk engine evaluation."""

    signal_id: str
    strategy_id: str
    symbol: str
    decision: RiskDecisionType
    original_size: float
    approved_size: float
    reject_reason: str | None
    evaluated_at: datetime


class RiskStatusResponse(BaseModel):
    """Risk engine status, limits, and kill-switch state."""

    trading_enabled: bool
    kill_switch_active: bool
    kill_switch_reason: str | None
    max_position_size_usd: RiskLimitMetric
    max_portfolio_exposure_usd: RiskLimitMetric
    max_leverage: RiskLimitMetric
    max_drawdown_pct: RiskLimitMetric
    recent_decisions: list[RiskDecisionLogResponse]


class KillSwitchTriggerRequest(BaseModel):
    """Payload to engage emergency kill switch."""

    reason: str = Field(..., min_length=3, description="Reason for triggering the kill switch")
    confirm: bool = Field(..., description="Explicit confirmation boolean")


class KillSwitchResetRequest(BaseModel):
    """Payload to reset emergency kill switch."""

    reason: str = Field(
        default="Manual admin reset", description="Reason for resetting kill switch"
    )
    confirm: bool = Field(..., description="Explicit confirmation boolean")


class KillSwitchActionResponse(BaseModel):
    """Result of a kill-switch control action."""

    success: bool
    kill_switch_active: bool
    reason: str
    timestamp: datetime


# ------------------------------------------------------------------------------
# 6. System Schemas
# ------------------------------------------------------------------------------


class SystemComponentHealth(BaseModel):
    """Health indicator for an individual subsystem."""

    name: str
    status: str  # ONLINE, DEGRADED, OFFLINE
    details: str
    last_heartbeat: datetime | None = None


class SystemStatusResponse(BaseModel):
    """Comprehensive platform telemetry and component health."""

    environment: TradingMode
    live_trading_enabled: bool
    server_time_utc: datetime
    clock_drift_ms: float
    rtt_latency_ms: float | None
    rate_limit_used_1m: int
    rate_limit_max_1m: int
    reconciliation_status: str
    last_reconciliation_time: datetime | None
    reconciliation_mismatches_count: int
    components: list[SystemComponentHealth]


# ------------------------------------------------------------------------------
# 7. Market Schemas
# ------------------------------------------------------------------------------


class MarketTickerResponse(BaseModel):
    """Active trading pair pricing, volume, and tick rules."""

    symbol: str
    mark_price: float
    index_price: float | None
    last_price: float | None
    funding_rate: float | None
    next_funding_time: datetime | None
    price_change_percent_24h: float
    high_price_24h: float
    low_price_24h: float
    volume_24h: float
    quote_volume_24h: float
    tick_size: float
    step_size: float
    min_notional: float
    last_updated: datetime


class KlineCandleResponse(BaseModel):
    """OHLCV candlestick with strategy moving average overlays."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    fast_sma: float | None = None
    slow_sma: float | None = None


# ------------------------------------------------------------------------------
# 8. WebSocket Push Event Schemas
# ------------------------------------------------------------------------------


class WebSocketMessage(BaseModel):
    """Uniform push frame streamed to frontend over WebSocket."""

    event_type: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: dict[str, Any]
