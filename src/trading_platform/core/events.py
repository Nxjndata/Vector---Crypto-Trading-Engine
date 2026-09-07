"""Typed event models and asynchronous in-process EventBus."""

import asyncio
import inspect
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from trading_platform.core.constants import (
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    ReconciliationEventType,
    RiskDecisionType,
    SignalType,
)
from trading_platform.core.logging import get_logger

logger = get_logger("core.events")


class Event(BaseModel):
    """Base model for all internal trading events."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def event_type(self) -> str:
        return self.__class__.__name__


# ==============================================================================
# Domain Events
# ==============================================================================


class CandleEvent(Event):
    """Normalized OHLCV candle close or update."""

    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    quote_volume: float
    trades_count: int
    is_closed: bool = True


class MarketDataEvent(Event):
    """Real-time market tick, mark price, funding rate, or 24h ticker update."""

    symbol: str
    mark_price: float
    last_price: float | None = None
    index_price: float | None = None
    funding_rate: float | None = None
    next_funding_time: datetime | None = None
    price_change_percent_24h: float | None = None
    high_price_24h: float | None = None
    low_price_24h: float | None = None
    volume_24h: float | None = None
    quote_volume_24h: float | None = None


class SignalEvent(Event):
    """Pure trading intent emitted exclusively by the Strategy Engine.

    Note on target_exposure:
    target_exposure represents the intended portfolio exposure expressed as a SIGNED FRACTION
    of total portfolio equity in the range [-1.0, 1.0].
    Examples:
      +0.05 -> 5% of portfolio equity in LONG exposure
      -0.05 -> 5% of portfolio equity in SHORT exposure
       0.00 -> 0% exposure (FLAT / exit position)
    """

    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str
    symbol: str
    signal_type: SignalType
    target_exposure: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Target portfolio exposure as a signed fraction of equity in range [-1.0, 1.0]",
    )
    mark_price: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskDecisionEvent(Event):
    """Risk engine evaluation outcome for a given Signal."""

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    signal_id: str
    strategy_id: str
    symbol: str
    decision_type: RiskDecisionType
    original_target_exposure: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Original target exposure as a fraction of equity in range [-1.0, 1.0]",
    )
    approved_target_exposure: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Approved target exposure as a fraction of equity in range [-1.0, 1.0]",
    )
    reason: str
    rule_triggered: str | None = None
    snapshot_data: dict[str, Any] = Field(default_factory=dict)


class OrderEvent(Event):
    """Order state transition emitted by OMS or Exchange Adapter."""

    client_order_id: str
    exchange_order_id: str | None = None
    strategy_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None
    stop_price: float | None = None
    status: OrderStatus
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    cum_quote: float = 0.0
    error_message: str | None = None


class FillEvent(Event):
    """Individual trade execution fill emitted by OMS/User Stream."""

    fill_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_order_id: str
    exchange_trade_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    fee: float = 0.0
    fee_asset: str = "USDT"


class PositionUpdateEvent(Event):
    """Position state modification emitted by Portfolio Manager."""

    strategy_id: str
    symbol: str
    size: float
    entry_price: float
    mark_price: float
    liquidation_price: float | None = None
    leverage: float
    unrealized_pnl: float
    margin_mode: MarginMode = MarginMode.ISOLATED


class ReconciliationMismatchEvent(Event):
    """Discrepancy detected between local state and exchange ground-truth."""

    discrepancy_type: ReconciliationEventType
    symbol: str | None = None
    local_state: dict[str, Any]
    remote_state: dict[str, Any]
    discrepancy_details: str
    action_taken: str


class SystemStatusEvent(Event):
    """System health, kill-switch, or connection status change."""

    component: str
    status: str  # e.g. "HEALTHY", "DEGRADED", "HALTED", "CONNECTED", "DISCONNECTED"
    message: str


class KillSwitchTriggeredEvent(Event):
    """Emergency kill-switch engagement event."""

    reason: str
    trigger_source: str = "OPERATOR"
    metadata: dict[str, Any] = Field(default_factory=dict)


class KillSwitchResetEvent(Event):
    """Emergency kill-switch reset/disengagement event."""

    reason: str
    operator: str = "OPERATOR"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PortfolioUpdateEvent(Event):
    """Portfolio equity, wallet balance, and PnL state update."""

    strategy_id: str
    equity: float
    wallet_balance: float
    available_balance: float
    unrealized_pnl: float
    realized_pnl: float
    total_exposure: float
    leverage: float


class ReconciliationResultEvent(Event):
    """Reconciliation audit outcome event."""

    strategy_id: str = "all"
    is_breached: bool = False
    mismatches: list[Any] = Field(default_factory=list)
    action_taken: str = "NONE"


# Type variable for typed subscription
E = TypeVar("E", bound=Event)
EventHandler = Callable[[E], Awaitable[None]]


class EventBus:
    """Asynchronous in-memory Event Bus with typed pub-sub and error isolation."""

    def __init__(self) -> None:
        self._subscribers: dict[type[Event], list[Callable[[Any], Awaitable[None]]]] = defaultdict(
            list
        )
        self._published_counts: dict[str, int] = defaultdict(int)
        self._error_counts: dict[str, int] = defaultdict(int)

    def subscribe(self, event_type: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        """Register an async handler function for a specific Event subclass.

        Args:
            event_type: Subclass of Event to listen for.
            handler: Async callable taking the event instance as its sole argument.
        """
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"Handler {handler} must be an async coroutine function (async def)")

        if handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)
            logger.debug(f"Subscribed {handler.__name__} to {event_type.__name__}")

    def unsubscribe(self, event_type: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        """Remove a previously registered handler."""
        if event_type in self._subscribers and handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)
            logger.debug(f"Unsubscribed {handler.__name__} from {event_type.__name__}")

    async def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers concurrently.

        Exceptions raised by individual handlers are caught, logged, and isolated
        so they do not crash the caller or prevent other subscribers from executing.
        """
        event_cls = type(event)
        self._published_counts[event_cls.__name__] += 1

        # Match exact subscribers + subscribers to parent event classes (e.g. Event base)
        handlers: list[Callable[[Any], Awaitable[None]]] = []
        for registered_type, registered_handlers in self._subscribers.items():
            if issubclass(event_cls, registered_type):
                handlers.extend(registered_handlers)

        if not handlers:
            return

        # Execute handlers concurrently
        tasks = [self._safe_execute(handler, event) for handler in handlers]
        await asyncio.gather(*tasks)

    async def _safe_execute(self, handler: Callable[[Any], Awaitable[None]], event: Event) -> None:
        """Execute a single handler with error isolation."""
        try:
            await handler(event)
        except Exception as e:
            self._error_counts[type(event).__name__] += 1
            logger.error(
                f"Error executing event handler {handler.__name__} on {type(event).__name__}: {e}",
                exc_info=True,
            )

    @property
    def metrics(self) -> dict[str, Any]:
        """Return event bus diagnostics."""
        return {
            "subscribers": {k.__name__: len(v) for k, v in self._subscribers.items()},
            "published": dict(self._published_counts),
            "errors": dict(self._error_counts),
        }
