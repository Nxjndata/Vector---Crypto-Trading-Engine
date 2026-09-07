"""Signal deduplication, persistence, and EventBus emission manager."""

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import SignalType
from trading_platform.core.events import EventBus, SignalEvent
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.models.signal import Signal

logger = get_logger("strategy.signal_manager")


class SignalManager:
    """Guarantees signal idempotency, persists signals to PostgreSQL, and publishes to EventBus."""

    def __init__(
        self,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        max_cache_size: int = 5000,
    ) -> None:
        self.event_bus = event_bus
        self.session_factory = session_factory
        self.max_cache_size = max_cache_size

        # In-memory deduplication cache: (strategy_id, symbol, timestamp_iso, signal_type)
        self._dedup_cache: set[tuple[str, str, str, SignalType]] = set()

        # Metrics
        self.processed_count: int = 0
        self.emitted_count: int = 0
        self.deduplicated_count: int = 0
        self.error_count: int = 0

    def _get_dedup_key(self, signal: SignalEvent) -> tuple[str, str, str, SignalType]:
        """Generate deduplication tuple for a signal."""
        # Truncate timestamp to seconds to guard against sub-millisecond clock variations on replay
        ts_key = signal.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return (signal.strategy_id, signal.symbol.upper(), ts_key, signal.signal_type)

    async def process_signal(self, signal: SignalEvent) -> bool:
        """Evaluate, deduplicate, persist, and publish a strategy signal.

        Returns:
            True if signal was newly processed and published, False if dropped as duplicate.
        """
        self.processed_count += 1
        dedup_key = self._get_dedup_key(signal)

        # 1. In-memory deduplication check
        if dedup_key in self._dedup_cache:
            self.deduplicated_count += 1
            logger.warning(
                f"Dropping duplicate signal (in-memory dedup): Strategy={signal.strategy_id} | "
                f"Symbol={signal.symbol} | Type={signal.signal_type.value} | Time={signal.timestamp.isoformat()}"
            )
            return False

        # 2. Database persistence & DB-level unique constraint check
        if self.session_factory:
            try:
                async with get_db_session(self.session_factory) as session:
                    db_signal = Signal(
                        id=signal.signal_id,
                        strategy_id=signal.strategy_id,
                        symbol=signal.symbol.upper(),
                        signal_type=signal.signal_type,
                        target_exposure=signal.target_exposure,
                        mark_price=signal.mark_price,
                        timestamp=signal.timestamp,
                        metadata_json=signal.metadata,
                    )
                    session.add(db_signal)
                    await session.flush()
            except IntegrityError:
                self.deduplicated_count += 1
                logger.warning(
                    f"Dropping duplicate signal (DB unique constraint): Strategy={signal.strategy_id} | "
                    f"Symbol={signal.symbol} | Time={signal.timestamp.isoformat()}"
                )
                self._dedup_cache.add(dedup_key)
                return False
            except Exception as e:
                self.error_count += 1
                logger.error(f"Failed to persist signal to database: {e}", exc_info=True)

        # 3. Add to memory cache (with bound maintenance)
        if len(self._dedup_cache) >= self.max_cache_size:
            # Drop oldest elements when capacity reached
            self._dedup_cache.clear()
        self._dedup_cache.add(dedup_key)

        # 4. Dispatch signal to EventBus for Risk Engine interception
        self.emitted_count += 1
        logger.info(
            f"[SIGNAL EMITTED] Strategy: {signal.strategy_id} | Symbol: {signal.symbol} | "
            f"Type: {signal.signal_type.value} | Target: ${signal.target_exposure:,.2f} | Mark: ${signal.mark_price:,.2f}"
        )
        await self.event_bus.publish(signal)
        return True

    @property
    def metrics(self) -> dict[str, Any]:
        """Return signal processing metrics."""
        return {
            "processed_count": self.processed_count,
            "emitted_count": self.emitted_count,
            "deduplicated_count": self.deduplicated_count,
            "error_count": self.error_count,
            "cache_size": len(self._dedup_cache),
        }
