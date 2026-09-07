"""Asynchronous consumer persisting closed candles to TimescaleDB."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.events import CandleEvent, EventBus
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.models.candle import Candle

logger = get_logger("market_data.persister")


class CandlePersister:
    """Subscribes to CandleEvent and persists completed (closed) candles into the database."""

    def __init__(
        self,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.event_bus = event_bus
        self.session_factory = session_factory

        self.persisted_count: int = 0
        self.error_count: int = 0

        # Register listener on event bus
        self.event_bus.subscribe(CandleEvent, self.on_candle_event)

    async def on_candle_event(self, event: CandleEvent) -> None:
        """Handle incoming candle event and persist only closed candles."""
        # Non-negotiable rule: only persist closed candles, never in-progress ones
        if not event.is_closed:
            return

        try:
            async with get_db_session(self.session_factory) as session:
                candle = Candle(
                    symbol=event.symbol.upper(),
                    timeframe=event.timeframe,
                    open_time=event.open_time,
                    close_time=event.close_time,
                    open_price=event.open_price,
                    high_price=event.high_price,
                    low_price=event.low_price,
                    close_price=event.close_price,
                    volume=event.volume,
                    quote_volume=event.quote_volume,
                    trades_count=event.trades_count,
                    is_closed=True,
                )
                # Use merge to handle duplicates gracefully
                await session.merge(candle)
                self.persisted_count += 1
                logger.debug(
                    f"Persisted closed candle: {event.symbol} ({event.timeframe}) "
                    f"at {event.open_time.isoformat()} [Close: {event.close_price}]"
                )
        except Exception as e:
            self.error_count += 1
            logger.error(
                f"Failed to persist candle for {event.symbol} ({event.open_time.isoformat()}): {e}",
                exc_info=True,
            )

    @property
    def metrics(self) -> dict[str, Any]:
        """Return persistence metrics."""
        return {
            "persisted_count": self.persisted_count,
            "error_count": self.error_count,
        }
