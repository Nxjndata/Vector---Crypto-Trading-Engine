"""Historical candle data loader with paginated REST backfill, rate limiting, and gap detection."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.models.candle import Candle

logger = get_logger("backtest.data_loader")

TIMEFRAME_TO_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
}


class HistoricalDataLoader:
    """Manages paginated historical data ingestion from exchange adapters and TimescaleDB."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.session_factory = session_factory

    async def backfill_from_adapter(
        self,
        adapter: ExchangeAdapter,
        symbol: str,
        timeframe: str = "1m",
        start: datetime | None = None,
        end: datetime | None = None,
        batch_limit: int = 1000,
    ) -> list[Candle]:
        """Fetch historical candles with pagination and rate-limiting from the exchange adapter.

        Args:
            adapter: ExchangeAdapter instance (reusing built-in rate limiter).
            symbol: Trading pair symbol (e.g. 'BTCUSDT').
            timeframe: Candle interval (e.g. '1m', '1h').
            start: Start timestamp.
            end: End timestamp (defaults to current UTC time).
            batch_limit: Max candles per request (up to 1000).

        Returns:
            List of all fetched Candle models in chronological order.
        """
        sym = symbol.upper()
        curr_start = start or (datetime.now(UTC) - timedelta(days=30))
        target_end = end or datetime.now(UTC)

        all_candles: list[Candle] = []
        logger.info(
            f"Starting historical backfill for {sym} ({timeframe}) "
            f"from {curr_start.isoformat()} to {target_end.isoformat()}..."
        )

        while curr_start < target_end:
            batch = await adapter.get_historical_data(
                symbol=sym,
                interval=timeframe,
                start=curr_start,
                end=target_end,
                limit=batch_limit,
            )

            if not batch:
                logger.info(
                    f"No further historical data returned for {sym} at {curr_start.isoformat()}."
                )
                break

            all_candles.extend(batch)
            last_candle = batch[-1]

            # Advance cursor past the last received candle close time
            next_start = last_candle.close_time + timedelta(milliseconds=1)
            if next_start <= curr_start:
                # Prevent infinite loop if exchange returns same timestamp
                next_start = curr_start + timedelta(seconds=TIMEFRAME_TO_SECONDS.get(timeframe, 60))

            curr_start = next_start

            if len(batch) < batch_limit:
                # Reached the end of available history
                break

        logger.info(
            f"Backfill complete: fetched {len(all_candles)} candles for {sym} ({timeframe})."
        )

        # Persist to database if session factory is configured
        if self.session_factory and all_candles:
            await self.persist_candles(all_candles)

        return all_candles

    async def persist_candles(self, candles: list[Candle]) -> int:
        """Persist candles to PostgreSQL database with upsert / merge."""
        if not self.session_factory or not candles:
            return 0

        async with get_db_session(self.session_factory) as session:
            for candle in candles:
                await session.merge(candle)
            logger.info(f"Persisted {len(candles)} candles to database.")
            return len(candles)

    async def load_from_db(
        self,
        symbol: str,
        timeframe: str = "1m",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """Load candles from PostgreSQL/TimescaleDB sorted in chronological order."""
        if not self.session_factory:
            raise ValueError("session_factory is required to load candles from database.")

        sym = symbol.upper()
        async with get_db_session(self.session_factory) as session:
            stmt = (
                select(Candle)
                .where(
                    Candle.symbol == sym,
                    Candle.timeframe == timeframe,
                )
                .order_by(Candle.open_time.asc())
            )
            if start:
                stmt = stmt.where(Candle.open_time >= start)
            if end:
                stmt = stmt.where(Candle.open_time <= end)

            res = await session.execute(stmt)
            candles = list(res.scalars().all())
            logger.info(f"Loaded {len(candles)} candles from DB for {sym} ({timeframe}).")
            return candles

    @staticmethod
    def detect_gaps(
        candles: list[Candle],
        timeframe: str = "1m",
    ) -> list[tuple[datetime, datetime]]:
        """Identify missing time intervals in historical candle series.

        Returns:
            List of (gap_start, gap_end) tuples.
        """
        if len(candles) < 2:
            return []

        expected_interval_sec = TIMEFRAME_TO_SECONDS.get(timeframe, 60)
        tolerance_sec = expected_interval_sec * 1.5
        gaps: list[tuple[datetime, datetime]] = []

        for i in range(1, len(candles)):
            prev_candle = candles[i - 1]
            curr_candle = candles[i]

            diff_sec = (curr_candle.open_time - prev_candle.open_time).total_seconds()
            if diff_sec > tolerance_sec:
                gaps.append((prev_candle.close_time, curr_candle.open_time))
                logger.warning(
                    f"Historical gap detected: {prev_candle.close_time.isoformat()} -> "
                    f"{curr_candle.open_time.isoformat()} ({diff_sec / 60:.1f} minutes)"
                )

        return gaps
