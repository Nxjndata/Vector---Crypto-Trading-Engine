"""Binance USDT-M Perpetual Futures User Data WebSocket Stream client."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from trading_platform.core.config import AppConfig
from trading_platform.core.constants import OrderSide, OrderStatus, OrderType, TradingMode
from trading_platform.core.events import EventBus, FillEvent, OrderEvent, SystemStatusEvent
from trading_platform.core.logging import get_logger
from trading_platform.exchange.binance_futures import BinanceFuturesAdapter

logger = get_logger("exchange.user_stream")


class BinanceUserDataStreamClient:
    """Manages real-time User Data WebSocket stream (fills, orders, positions, account balance)."""

    TESTNET_WS_URL = "wss://stream.binancefuture.com/ws"
    PRODUCTION_WS_URL = "wss://fstream.binance.com/ws"

    def __init__(
        self,
        config: AppConfig,
        event_bus: EventBus,
        adapter: BinanceFuturesAdapter,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.adapter = adapter
        self.strategy_id = config.strategy_id

        if config.environment == TradingMode.LIVE and config.live_trading_enabled:
            self.base_ws_url = self.PRODUCTION_WS_URL
        else:
            self.base_ws_url = self.TESTNET_WS_URL

        self._running: bool = False
        self._connected: bool = False
        self._listen_key: str | None = None
        self._ws_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._disconnect_count: int = 0
        self._messages_received: int = 0
        self._last_keepalive_time: datetime | None = None

    @property
    def is_connected(self) -> bool:
        """Return true if WebSocket connection is active."""
        return self._connected

    @property
    def listen_key(self) -> str | None:
        """Return the current active listenKey."""
        return self._listen_key

    async def start(self) -> None:
        """Acquire listenKey and start background WebSocket stream and keepalive tasks."""
        if self._running:
            return
        self._running = True

        # 1. Acquire initial listenKey
        try:
            self._listen_key = await self.adapter.create_listen_key()
        except Exception as e:
            logger.error(f"Failed to acquire initial listenKey for user data stream: {e}")
            raise

        # 2. Launch background tasks
        self._ws_task = asyncio.create_task(self._run_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logger.info("BinanceUserDataStreamClient started.")

    async def stop(self) -> None:
        """Stop background stream and delete listenKey."""
        self._running = False

        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        if self._listen_key:
            try:
                await self.adapter.close_listen_key(self._listen_key)
            except Exception as e:
                logger.warning(f"Failed to close listenKey cleanly: {e}")
            self._listen_key = None

        self._connected = False
        logger.info("BinanceUserDataStreamClient stopped.")

    async def _keepalive_loop(self) -> None:
        """Periodically refresh listenKey every 30 minutes to prevent expiration (valid for 60m)."""
        while self._running:
            try:
                await asyncio.sleep(1800)  # 30 minutes
                if self._running and self._listen_key:
                    await self.adapter.keepalive_listen_key(self._listen_key)
                    self._last_keepalive_time = datetime.now(UTC)
                    logger.debug("Refreshed user data stream listenKey.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error during listenKey keepalive: {e}")

    async def _run_loop(self) -> None:
        """Main connection and auto-reconnect loop."""
        reconnect_delay = 1.0
        max_reconnect_delay = 30.0

        while self._running:
            if not self._listen_key:
                try:
                    self._listen_key = await self.adapter.create_listen_key()
                except Exception as e:
                    logger.warning(f"Could not create listenKey: {e}. Retrying in 5s...")
                    await asyncio.sleep(5.0)
                    continue

            url = f"{self.base_ws_url}/{self._listen_key}"
            logger.info("Connecting to Binance User Data WebSocket stream...")

            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._connected = True
                    reconnect_delay = 1.0
                    logger.info("Connected to Binance User Data stream.")

                    await self.event_bus.publish(
                        SystemStatusEvent(
                            component="BinanceUserDataStreamClient",
                            status="CONNECTED",
                            message="Connected to Binance Futures User Data Stream",
                        )
                    )

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        self._messages_received += 1
                        await self._process_message(raw_msg)

            except (ConnectionClosed, OSError, Exception) as e:
                self._connected = False
                self._disconnect_count += 1

                if not self._running:
                    break

                logger.warning(
                    f"User Data WebSocket disconnected ({e}). Reconnecting in {reconnect_delay:.1f}s..."
                )

                await self.event_bus.publish(
                    SystemStatusEvent(
                        component="BinanceUserDataStreamClient",
                        status="DISCONNECTED",
                        message=f"User Data stream disconnected ({e})",
                    )
                )

                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2.0, max_reconnect_delay)

    async def _process_message(self, raw_msg: str | bytes) -> None:
        """Parse raw User Data Stream message and publish events."""
        try:
            msg = json.loads(raw_msg) if isinstance(raw_msg, (str, bytes)) else raw_msg
        except Exception as e:
            logger.error(f"Failed to parse User Data Stream JSON: {e}")
            return

        event_type = msg.get("e")

        if event_type == "ORDER_TRADE_UPDATE":
            await self._handle_order_trade_update(msg)
        elif event_type == "ACCOUNT_UPDATE":
            await self._handle_account_update(msg)
        elif event_type == "listenKeyExpired":
            logger.warning("Binance User Data listenKey expired. Triggering reconnect...")
            self._listen_key = None
            if self._ws_task:
                self._ws_task.cancel()

    async def _handle_order_trade_update(self, msg: dict[str, Any]) -> None:
        """Handle ORDER_TRADE_UPDATE message and emit FillEvent and OrderEvent on execution."""
        o = msg.get("o", {})
        execution_type = o.get("x", "")  # e.g., "NEW", "TRADE", "CANCELED", "EXPIRED"
        binance_status = o.get("X", "")  # e.g., "NEW", "PARTIALLY_FILLED", "FILLED"
        symbol = o.get("s", "")
        client_order_id = o.get("c", "")
        exchange_order_id = str(o.get("i", ""))
        side_str = o.get("S", "BUY")
        side = OrderSide(side_str)

        orig_qty = float(o.get("q", 0.0))
        cum_filled_qty = float(o.get("z", 0.0))
        last_filled_qty = float(o.get("l", 0.0))
        avg_price = float(o.get("ap", 0.0))
        last_filled_price = float(o.get("L", 0.0))
        order_price = float(o.get("p", 0.0))
        commission = float(o.get("n", 0.0))
        commission_asset = o.get("N", "USDT")
        trade_id = str(o.get("t", ""))
        trade_time_ms = o.get("T", int(datetime.now(UTC).timestamp() * 1000))
        trade_time = datetime.fromtimestamp(trade_time_ms / 1000.0, tz=UTC)
        order_type_str = o.get("o", "MARKET")
        order_type = OrderType.LIMIT if order_type_str == "LIMIT" else OrderType.MARKET

        status_map = {
            "NEW": OrderStatus.ACKNOWLEDGED,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "EXPIRED": OrderStatus.EXPIRED,
            "REJECTED": OrderStatus.REJECTED,
        }
        order_status = status_map.get(binance_status, OrderStatus.UNKNOWN)

        logger.info(
            f"User Data ORDER_TRADE_UPDATE: {symbol} {side.value} | "
            f"Status: {binance_status} ({order_status.value}), ExecType: {execution_type} | "
            f"Filled: {cum_filled_qty}/{orig_qty} (Last: {last_filled_qty} @ {last_filled_price}) "
            f"(Fee: {commission} {commission_asset})"
        )

        # 1. When a trade execution fill occurs, publish FillEvent
        if execution_type == "TRADE" and last_filled_qty > 0:
            fill_event = FillEvent(
                strategy_id=self.strategy_id,
                symbol=symbol,
                client_order_id=client_order_id,
                exchange_trade_id=trade_id or f"binance_{exchange_order_id}_{trade_time_ms}",
                side=side,
                quantity=last_filled_qty,
                price=last_filled_price or avg_price,
                fee=commission,
                fee_asset=commission_asset,
                timestamp=trade_time,
            )
            await self.event_bus.publish(fill_event)

        # 2. Publish OrderEvent to update OMS and order history
        if order_status != OrderStatus.UNKNOWN:
            order_event = OrderEvent(
                client_order_id=client_order_id,
                exchange_order_id=exchange_order_id,
                strategy_id=self.strategy_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=orig_qty or cum_filled_qty,
                price=order_price if order_price > 0 else None,
                status=order_status,
                filled_qty=cum_filled_qty,
                avg_fill_price=avg_price or last_filled_price,
                timestamp=trade_time,
            )
            await self.event_bus.publish(order_event)

    async def _handle_account_update(self, msg: dict[str, Any]) -> None:
        """Handle ACCOUNT_UPDATE message (balances and positions)."""
        a = msg.get("a", {})
        event_reason = a.get("m", "")  # e.g., "ORDER", "FUNDING_FEE", "DEPOSIT"
        balances = a.get("B", [])
        positions = a.get("P", [])

        logger.debug(
            f"User Data ACCOUNT_UPDATE (reason: {event_reason}): "
            f"{len(balances)} balances, {len(positions)} positions updated."
        )

    @property
    def metrics(self) -> dict[str, Any]:
        """Return diagnostic metrics."""
        return {
            "is_connected": self._connected,
            "disconnect_count": self._disconnect_count,
            "messages_received": self._messages_received,
            "has_listen_key": self._listen_key is not None,
            "last_keepalive_time": (
                self._last_keepalive_time.isoformat() if self._last_keepalive_time else None
            ),
        }
