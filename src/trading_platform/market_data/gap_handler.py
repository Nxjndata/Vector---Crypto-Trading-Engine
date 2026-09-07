"""Gap detection and REST backfill manager for reconnect recovery."""

from datetime import UTC, datetime
from typing import Any

from trading_platform.core.events import CandleEvent, EventBus, SystemStatusEvent
from trading_platform.core.logging import get_logger
from trading_platform.exchange.adapter import ExchangeAdapter

logger = get_logger("market_data.gap_handler")


class MarketDataGapHandler:
    """Tracks candle continuity and orchestrates REST backfills across WebSocket reconnects."""

    def __init__(
        self,
        adapter: ExchangeAdapter,
        event_bus: EventBus,
    ) -> None:
        self.adapter = adapter
        self.event_bus = event_bus
        # Map (symbol, timeframe) -> last processed closed candle close_time (datetime UTC)
        self._last_closed_candle_times: dict[tuple[str, str], datetime] = {}
        self._backfill_counts: dict[str, int] = {}

    def record_closed_candle(self, symbol: str, timeframe: str, close_time: datetime) -> None:
        """Record the timestamp of a successfully processed closed candle."""
        key = (symbol.upper(), timeframe)
        prev = self._last_closed_candle_times.get(key)
        if prev is None or close_time > prev:
            self._last_closed_candle_times[key] = close_time

    def get_last_closed_time(self, symbol: str, timeframe: str) -> datetime | None:
        """Get the last processed closed candle timestamp."""
        return self._last_closed_candle_times.get((symbol.upper(), timeframe))

    async def backfill_gaps(
        self,
        symbols: list[str],
        timeframe: str = "1m",
    ) -> dict[str, int]:
        """Detect gaps for all symbols and fetch missing candles via REST adapter before resuming live streams.

        Returns:
            Dictionary mapping symbol to number of backfilled candles.
        """
        now = datetime.now(UTC)
        results: dict[str, int] = {}

        for sym in symbols:
            symbol = sym.upper()
            key = (symbol, timeframe)
            last_closed = self._last_closed_candle_times.get(key)

            if not last_closed:
                logger.debug(
                    f"No prior candle close recorded for {symbol} ({timeframe}). Backfill not required."
                )
                results[symbol] = 0
                continue

            gap_seconds = (now - last_closed).total_seconds()
            # If gap is smaller than a typical 1m candle (e.g. < 60s), no closed candles were missed
            if gap_seconds < 60:
                logger.debug(
                    f"No gap detected for {symbol} ({timeframe}). Gap duration: {gap_seconds:.1f}s."
                )
                results[symbol] = 0
                continue

            logger.warning(
                f"Market data gap detected for {symbol} ({timeframe}): "
                f"Last processed={last_closed.isoformat()}, Gap={gap_seconds:.1f}s. Backfilling via REST..."
            )

            try:
                # Fetch missing candles starting from last_closed
                candles = await self.adapter.get_historical_data(
                    symbol=symbol,
                    interval=timeframe,
                    start=last_closed,
                    end=now,
                    limit=500,
                )

                # Filter to only candles newer than our last recorded close_time
                new_candles = [c for c in candles if c.close_time > last_closed]
                backfilled_count = len(new_candles)

                for candle in new_candles:
                    # Dispatch normalized CandleEvent on EventBus so downstream persistence and strategies receive it
                    event = CandleEvent(
                        symbol=candle.symbol,
                        timeframe=candle.timeframe,
                        open_time=candle.open_time,
                        close_time=candle.close_time,
                        open_price=candle.open_price,
                        high_price=candle.high_price,
                        low_price=candle.low_price,
                        close_price=candle.close_price,
                        volume=candle.volume,
                        quote_volume=candle.quote_volume,
                        trades_count=candle.trades_count,
                        is_closed=True,
                    )
                    await self.event_bus.publish(event)
                    self.record_closed_candle(symbol, timeframe, candle.close_time)

                results[symbol] = backfilled_count
                self._backfill_counts[symbol] = (
                    self._backfill_counts.get(symbol, 0) + backfilled_count
                )

                # Publish structured event logging the gap resolution
                await self.event_bus.publish(
                    SystemStatusEvent(
                        component="MarketDataGapHandler",
                        status="GAP_RESOLVED",
                        message=(
                            f"Backfilled {backfilled_count} missing candles for {symbol} ({timeframe}) "
                            f"after {gap_seconds:.1f}s disconnect."
                        ),
                    )
                )
                logger.info(
                    f"Successfully backfilled {backfilled_count} candles for {symbol} ({timeframe})."
                )

            except Exception as e:
                logger.error(f"Failed to backfill gap for {symbol} via REST: {e}", exc_info=True)
                results[symbol] = 0
                await self.event_bus.publish(
                    SystemStatusEvent(
                        component="MarketDataGapHandler",
                        status="BACKFILL_FAILED",
                        message=f"Failed to backfill candles for {symbol}: {e}",
                    )
                )

        return results

    @property
    def metrics(self) -> dict[str, Any]:
        """Return gap handler diagnostics."""
        return {
            "last_processed_candles": {
                f"{k[0]}_{k[1]}": v.isoformat() for k, v in self._last_closed_candle_times.items()
            },
            "total_backfilled_candles": dict(self._backfill_counts),
        }
