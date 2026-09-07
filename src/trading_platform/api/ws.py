"""Real-time WebSocket Push Hub subscribing to domain EventBus."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket

from trading_platform.core.events import (
    CandleEvent,
    EventBus,
    FillEvent,
    MarketDataEvent,
    OrderEvent,
    PositionUpdateEvent,
    RiskDecisionEvent,
    SystemStatusEvent,
)
from trading_platform.core.logging import get_logger

logger = get_logger("api.ws")


class WebSocketConnectionManager:
    """Manages active browser connections and broadcasts domain events from EventBus."""

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()
        self._is_subscribed: bool = False
        self._price_history: dict[str, list[float]] = {}

    def subscribe_to_event_bus(self) -> None:
        """Register async listeners on EventBus to forward domain events to WebSocket clients."""
        if self._is_subscribed:
            return

        self.event_bus.subscribe(CandleEvent, self.on_candle_event)
        self.event_bus.subscribe(MarketDataEvent, self.on_market_data_event)
        self.event_bus.subscribe(FillEvent, self.on_fill_event)
        self.event_bus.subscribe(OrderEvent, self.on_order_event)
        self.event_bus.subscribe(PositionUpdateEvent, self.on_position_update_event)
        self.event_bus.subscribe(RiskDecisionEvent, self.on_risk_decision_event)
        self.event_bus.subscribe(SystemStatusEvent, self.on_system_status_event)
        self._is_subscribed = True
        logger.info("WebSocket connection manager subscribed to EventBus domain events.")

    async def connect(self, websocket: WebSocket) -> None:
        """Accept incoming WebSocket connection and register client."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Active clients: {len(self.active_connections)}")

        # Send initial connected handshake message
        await self.send_personal_message(
            {
                "event_type": "connection_ack",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {"status": "CONNECTED", "client_count": len(self.active_connections)},
            },
            websocket,
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Unregister disconnected WebSocket client."""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(
            f"WebSocket client disconnected. Active clients: {len(self.active_connections)}"
        )

    async def send_personal_message(self, message: dict[str, Any], websocket: WebSocket) -> None:
        """Send message frame to a specific client."""
        try:
            await websocket.send_text(json.dumps(message, default=str))
        except Exception:
            pass

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast JSON message to all active connected clients."""
        if not self.active_connections:
            return

        payload = json.dumps(message, default=str)
        dead_connections: list[WebSocket] = []

        async with self._lock:
            for connection in list(self.active_connections):
                try:
                    await connection.send_text(payload)
                except Exception:
                    dead_connections.append(connection)

            for dead in dead_connections:
                if dead in self.active_connections:
                    self.active_connections.remove(dead)

    # --------------------------------------------------------------------------
    # Event Bus Handlers
    # --------------------------------------------------------------------------

    async def on_candle_event(self, event: CandleEvent) -> None:
        """Forward real-time candlestick update with live strategy SMA overlays to browser charts."""
        sym = event.symbol.upper()
        if sym not in self._price_history:
            self._price_history[sym] = []
        history = self._price_history[sym]
        if event.is_closed:
            history.append(event.close_price)
            if len(history) > 100:
                history = history[-100:]
                self._price_history[sym] = history

        # Compute Fast SMA (9) and Slow SMA (21) matching strategy
        current_closes = history + ([] if event.is_closed else [event.close_price])
        fast_sma = (
            sum(current_closes[-9:]) / 9.0
            if len(current_closes) >= 9
            else None
        )
        slow_sma = (
            sum(current_closes[-21:]) / 21.0
            if len(current_closes) >= 21
            else None
        )

        t_sec = int(event.open_time.timestamp())
        await self.broadcast(
            {
                "event_type": "candle",
                "timestamp": event.timestamp.isoformat(),
                "data": {
                    "symbol": event.symbol,
                    "timeframe": event.timeframe,
                    "time": t_sec,
                    "open_time": t_sec,
                    "close_time": int(event.close_time.timestamp()),
                    "open": event.open_price,
                    "high": event.high_price,
                    "low": event.low_price,
                    "close": event.close_price,
                    "volume": event.volume,
                    "quote_volume": event.quote_volume,
                    "is_closed": event.is_closed,
                    "fast_sma": round(fast_sma, 4) if fast_sma is not None else None,
                    "slow_sma": round(slow_sma, 4) if slow_sma is not None else None,
                },
            }
        )

    async def on_market_data_event(self, event: MarketDataEvent) -> None:
        """Forward mark price, funding rate tick, and 24h market stats."""
        tick_data: dict[str, Any] = {
            "symbol": event.symbol,
            "mark_price": event.mark_price,
            "last_price": event.last_price,
            "index_price": event.index_price,
            "funding_rate": event.funding_rate,
        }
        if event.price_change_percent_24h is not None:
            tick_data["price_change_percent_24h"] = event.price_change_percent_24h
        if event.high_price_24h is not None:
            tick_data["high_price_24h"] = event.high_price_24h
        if event.low_price_24h is not None:
            tick_data["low_price_24h"] = event.low_price_24h
        if event.volume_24h is not None:
            tick_data["volume_24h"] = event.volume_24h
        if event.quote_volume_24h is not None:
            tick_data["quote_volume_24h"] = event.quote_volume_24h

        await self.broadcast(
            {
                "event_type": "market_tick",
                "timestamp": event.timestamp.isoformat(),
                "data": tick_data,
            }
        )

    async def on_fill_event(self, event: FillEvent) -> None:
        """Forward live execution fill."""
        await self.broadcast(
            {
                "event_type": "fill",
                "timestamp": event.timestamp.isoformat(),
                "data": {
                    "fill_id": event.fill_id,
                    "client_order_id": event.client_order_id,
                    "exchange_trade_id": event.exchange_trade_id,
                    "strategy_id": event.strategy_id,
                    "symbol": event.symbol,
                    "side": event.side.value,
                    "price": event.price,
                    "quantity": event.quantity,
                    "fee": event.fee,
                    "fee_asset": event.fee_asset,
                },
            }
        )

    async def on_order_event(self, event: OrderEvent) -> None:
        """Forward order status lifecycle transition."""
        await self.broadcast(
            {
                "event_type": "order_update",
                "timestamp": event.timestamp.isoformat(),
                "data": {
                    "client_order_id": event.client_order_id,
                    "exchange_order_id": event.exchange_order_id,
                    "strategy_id": event.strategy_id,
                    "symbol": event.symbol,
                    "side": event.side.value,
                    "order_type": event.order_type.value,
                    "quantity": event.quantity,
                    "price": event.price,
                    "status": event.status.value,
                    "filled_qty": event.filled_qty,
                    "avg_fill_price": event.avg_fill_price,
                },
            }
        )

    async def on_position_update_event(self, event: PositionUpdateEvent) -> None:
        """Forward position modification."""
        await self.broadcast(
            {
                "event_type": "position_update",
                "timestamp": event.timestamp.isoformat(),
                "data": {
                    "strategy_id": event.strategy_id,
                    "symbol": event.symbol,
                    "size": event.size,
                    "entry_price": event.entry_price,
                    "mark_price": event.mark_price,
                    "liquidation_price": event.liquidation_price,
                    "leverage": event.leverage,
                    "unrealized_pnl": event.unrealized_pnl,
                },
            }
        )

    async def on_risk_decision_event(self, event: RiskDecisionEvent) -> None:
        """Forward pre-trade risk decision audit."""
        await self.broadcast(
            {
                "event_type": "risk_decision",
                "timestamp": event.timestamp.isoformat(),
                "data": {
                    "decision_id": event.decision_id,
                    "signal_id": event.signal_id,
                    "strategy_id": event.strategy_id,
                    "symbol": event.symbol,
                    "decision": event.decision_type.value,
                    "original_exposure": event.original_target_exposure,
                    "approved_exposure": event.approved_target_exposure,
                    "reason": event.reason,
                    "rule_triggered": event.rule_triggered,
                },
            }
        )

    async def on_system_status_event(self, event: SystemStatusEvent) -> None:
        """Forward system health or kill switch status modification."""
        await self.broadcast(
            {
                "event_type": "system_status",
                "timestamp": event.timestamp.isoformat(),
                "data": {
                    "component": event.component,
                    "status": event.status,
                    "message": event.message,
                },
            }
        )
