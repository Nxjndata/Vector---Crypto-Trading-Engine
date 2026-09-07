"""Native Binance USDT-M Perpetual Futures WebSocket client with combined stream support."""

import asyncio
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from trading_platform.core.config import AppConfig
from trading_platform.core.constants import TradingMode
from trading_platform.core.events import CandleEvent, EventBus, SystemStatusEvent
from trading_platform.core.logging import get_logger
from trading_platform.market_data.gap_handler import MarketDataGapHandler
from trading_platform.market_data.normalizer import BinanceDataNormalizer

logger = get_logger("market_data.ws_client")


class BinanceWebSocketClient:
    """Manages resilient WebSocket connectivity to Binance Futures combined market streams."""

    TESTNET_WS_URL = "wss://stream.binancefuture.com/stream"
    PRODUCTION_WS_URL = "wss://fstream.binance.com/stream"

    def __init__(
        self,
        config: AppConfig,
        event_bus: EventBus,
        gap_handler: MarketDataGapHandler | None = None,
        normalizer: BinanceDataNormalizer | None = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.gap_handler = gap_handler
        self.normalizer = normalizer or BinanceDataNormalizer()

        # Target symbols and timeframe
        self.symbols = [s.lower() for s in config.market_data.active_symbols]
        self.timeframe = config.market_data.candle_timeframe

        # URL selection
        if config.environment == TradingMode.LIVE and config.live_trading_enabled:
            self.base_ws_url = self.PRODUCTION_WS_URL
        else:
            self.base_ws_url = self.TESTNET_WS_URL

        # State tracking
        self._running: bool = False
        self._connected: bool = False
        self._ws_task: asyncio.Task[None] | None = None
        self._disconnect_count: int = 0
        self._messages_received: int = 0

    @property
    def is_connected(self) -> bool:
        """Return true if WebSocket connection is active."""
        return self._connected

    def build_stream_url(self) -> str:
        """Construct Binance combined stream URL for active symbols."""
        streams = []
        for sym in self.symbols:
            streams.append(f"{sym}@kline_{self.timeframe}")
            streams.append(f"{sym}@markPrice@1s")
            streams.append(f"{sym}@ticker")
        stream_param = "/".join(streams)
        return f"{self.base_ws_url}?streams={stream_param}"

    async def start(self) -> None:
        """Start the WebSocket connection and consumer loop in the background."""
        if self._running:
            return
        self._running = True
        self._ws_task = asyncio.create_task(self._run_loop())
        logger.info("BinanceWebSocketClient started background connection loop.")

    async def stop(self) -> None:
        """Stop the WebSocket connection and wait for shutdown."""
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
        self._connected = False
        logger.info("BinanceWebSocketClient stopped.")

    async def connect(self) -> None:
        """Alias for start() to match PlatformEngine interface."""
        await self.start()

    async def disconnect(self) -> None:
        """Alias for stop() to match PlatformEngine interface."""
        await self.stop()

    async def _run_loop(self) -> None:
        """Main connection and auto-reconnect loop with exponential backoff."""
        reconnect_delay = 1.0
        max_reconnect_delay = 30.0

        while self._running:
            url = self.build_stream_url()
            logger.info(
                f"Connecting to Binance combined WebSocket streams ({len(self.symbols)} symbols)..."
            )

            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._connected = True
                    reconnect_delay = 1.0  # Reset backoff on successful connection
                    logger.info("Connected to Binance WebSocket market data stream.")

                    await self.event_bus.publish(
                        SystemStatusEvent(
                            component="BinanceWebSocketClient",
                            status="CONNECTED",
                            message=f"Connected to Binance Futures stream ({len(self.symbols)} symbols)",
                        )
                    )

                    # If reconnecting after a prior disconnect, trigger REST gap backfill
                    if self._disconnect_count > 0 and self.gap_handler:
                        logger.info(
                            "Executing gap backfill after reconnect before resuming live message consumption..."
                        )
                        await self.gap_handler.backfill_gaps(
                            symbols=self.symbols,
                            timeframe=self.timeframe,
                        )

                    # Process incoming messages
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
                    f"WebSocket disconnected ({e}). Reconnecting in {reconnect_delay:.1f}s "
                    f"(Disconnect count: {self._disconnect_count})..."
                )

                await self.event_bus.publish(
                    SystemStatusEvent(
                        component="BinanceWebSocketClient",
                        status="DISCONNECTED",
                        message=f"WebSocket disconnected ({e}). Retrying in {reconnect_delay:.1f}s",
                    )
                )

                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2.0, max_reconnect_delay)

    async def _process_message(self, raw_msg: str | bytes) -> None:
        """Normalize raw WebSocket message and publish domain events."""
        events = self.normalizer.normalize(raw_msg)
        for event in events:
            # If closed candle, update gap handler tracker
            if isinstance(event, CandleEvent) and event.is_closed and self.gap_handler:
                self.gap_handler.record_closed_candle(
                    symbol=event.symbol,
                    timeframe=event.timeframe,
                    close_time=event.close_time,
                )

            # Publish normalized event on EventBus
            await self.event_bus.publish(event)

    @property
    def metrics(self) -> dict[str, Any]:
        """Return WebSocket client metrics."""
        return {
            "is_connected": self._connected,
            "disconnect_count": self._disconnect_count,
            "messages_received": self._messages_received,
            "active_symbols": self.symbols,
            "timeframe": self.timeframe,
        }
