"""Tests for Phase 3 Market Data Engine: normalization, gap recovery, WS client, and persistence."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.config import AppConfig
from trading_platform.core.constants import TradingMode
from trading_platform.core.events import CandleEvent, EventBus, MarketDataEvent
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.market_data.candle_persister import CandlePersister
from trading_platform.market_data.gap_handler import MarketDataGapHandler
from trading_platform.market_data.normalizer import BinanceDataNormalizer
from trading_platform.market_data.ws_client import BinanceWebSocketClient
from trading_platform.models.candle import Candle

# ==============================================================================
# 1. Normalizer Tests
# ==============================================================================


def test_normalizer_kline_combined_stream():
    """Verify BinanceDataNormalizer parses combined stream kline JSON."""
    normalizer = BinanceDataNormalizer()
    raw_payload = {
        "stream": "btcusdt@kline_1m",
        "data": {
            "e": "kline",
            "E": 1725062460000,
            "s": "BTCUSDT",
            "k": {
                "t": 1725062400000,
                "T": 1725062459999,
                "s": "BTCUSDT",
                "i": "1m",
                "o": "64000.0",
                "c": "64150.5",
                "h": "64200.0",
                "l": "63980.0",
                "v": "15.5",
                "n": 420,
                "x": True,  # Candle closed
                "q": "993500.0",
            },
        },
    }

    events = normalizer.normalize(raw_payload)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, CandleEvent)
    assert event.symbol == "BTCUSDT"
    assert event.timeframe == "1m"
    assert event.open_price == 64000.0
    assert event.close_price == 64150.5
    assert event.high_price == 64200.0
    assert event.low_price == 63980.0
    assert event.volume == 15.5
    assert event.trades_count == 420
    assert event.is_closed is True


def test_normalizer_in_progress_kline():
    """Verify in-progress candle has is_closed=False."""
    normalizer = BinanceDataNormalizer()
    raw_payload = {
        "stream": "ethusdt@kline_1m",
        "data": {
            "e": "kline",
            "s": "ETHUSDT",
            "k": {
                "t": 1725062400000,
                "T": 1725062459999,
                "s": "ETHUSDT",
                "i": "1m",
                "o": "3400.0",
                "c": "3410.0",
                "h": "3415.0",
                "l": "3395.0",
                "v": "50.0",
                "n": 80,
                "x": False,  # In-progress
                "q": "170500.0",
            },
        },
    }

    events = normalizer.normalize(raw_payload)
    assert len(events) == 1
    assert isinstance(events[0], CandleEvent)
    assert events[0].is_closed is False


def test_normalizer_mark_price_combined_stream():
    """Verify BinanceDataNormalizer parses markPriceUpdate with funding rate."""
    normalizer = BinanceDataNormalizer()
    raw_payload = {
        "stream": "btcusdt@markPrice@1s",
        "data": {
            "e": "markPriceUpdate",
            "E": 1725062400000,
            "s": "BTCUSDT",
            "p": "64120.25",  # Mark price
            "i": "64115.00",  # Index price
            "r": "0.00010000",  # Funding rate
            "T": 1725091200000,  # Next funding time
        },
    }

    events = normalizer.normalize(raw_payload)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, MarketDataEvent)
    assert event.symbol == "BTCUSDT"
    assert event.mark_price == 64120.25
    assert event.index_price == 64115.00
    assert event.funding_rate == 0.0001
    assert event.next_funding_time is not None


# ==============================================================================
# 2. Candle Persister Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_persister_closed_vs_in_progress_candles(async_test_engine):
    """Verify that only closed candles are written to the database, ignoring in-progress candles."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    persister = CandlePersister(event_bus=event_bus, session_factory=session_factory)

    open_dt = datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC)
    close_dt = datetime(2026, 8, 30, 14, 1, 0, tzinfo=UTC)

    # 1. Publish in-progress candle (is_closed=False)
    in_progress = CandleEvent(
        symbol="BTCUSDT",
        timeframe="1m",
        open_time=open_dt,
        close_time=close_dt,
        open_price=64000.0,
        high_price=64100.0,
        low_price=63950.0,
        close_price=64050.0,
        volume=10.0,
        quote_volume=640000.0,
        trades_count=150,
        is_closed=False,
    )
    await event_bus.publish(in_progress)

    async with session_factory() as session:
        result = await session.execute(select(Candle).where(Candle.symbol == "BTCUSDT"))
        assert result.scalar_one_or_none() is None
        assert persister.persisted_count == 0

    # 2. Publish closed candle (is_closed=True)
    closed = CandleEvent(
        symbol="BTCUSDT",
        timeframe="1m",
        open_time=open_dt,
        close_time=close_dt,
        open_price=64000.0,
        high_price=64100.0,
        low_price=63950.0,
        close_price=64080.0,
        volume=12.5,
        quote_volume=800000.0,
        trades_count=200,
        is_closed=True,
    )
    await event_bus.publish(closed)

    async with session_factory() as session:
        result = await session.execute(select(Candle).where(Candle.symbol == "BTCUSDT"))
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved.close_price == 64080.0
        assert saved.volume == 12.5
        assert persister.persisted_count == 1


# ==============================================================================
# 3. Gap Detection & REST Backfill Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_gap_handler_backfill_flow():
    """Verify gap handler detects missing candles on reconnect and fetches them via REST adapter."""
    event_bus = EventBus()
    published_candles: list[CandleEvent] = []

    async def on_candle(event: CandleEvent) -> None:
        published_candles.append(event)

    event_bus.subscribe(CandleEvent, on_candle)

    # Mock REST adapter
    mock_adapter = AsyncMock(spec=ExchangeAdapter)

    # Historical candles to return on backfill
    t1 = datetime(2026, 8, 30, 12, 1, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 30, 12, 2, 0, tzinfo=UTC)
    mock_adapter.get_historical_data.return_value = [
        Candle(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=t1,
            close_time=datetime(2026, 8, 30, 12, 2, 0, tzinfo=UTC),
            open_price=64000.0,
            high_price=64050.0,
            low_price=63990.0,
            close_price=64020.0,
            volume=5.0,
            quote_volume=320000.0,
            trades_count=50,
            is_closed=True,
        ),
        Candle(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=t2,
            close_time=datetime(2026, 8, 30, 12, 3, 0, tzinfo=UTC),
            open_price=64020.0,
            high_price=64090.0,
            low_price=64010.0,
            close_price=64080.0,
            volume=7.0,
            quote_volume=448000.0,
            trades_count=65,
            is_closed=True,
        ),
    ]

    gap_handler = MarketDataGapHandler(adapter=mock_adapter, event_bus=event_bus)

    # 1. Record that last candle close was 10 minutes ago
    ten_mins_ago = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    gap_handler.record_closed_candle("BTCUSDT", "1m", ten_mins_ago)

    # 2. Trigger backfill
    results = await gap_handler.backfill_gaps(symbols=["BTCUSDT"], timeframe="1m")

    assert results["BTCUSDT"] == 2
    assert len(published_candles) == 2
    assert published_candles[0].open_price == 64000.0
    assert published_candles[1].close_price == 64080.0
    assert gap_handler.metrics["total_backfilled_candles"]["BTCUSDT"] == 2


# ==============================================================================
# 4. WebSocket Client Mock Tests
# ==============================================================================


def test_ws_client_stream_url_construction():
    """Verify BinanceWebSocketClient constructs correct combined stream URL."""
    config = AppConfig(
        environment=TradingMode.PAPER,
        market_data={"active_symbols": ["BTCUSDT", "ETHUSDT"], "candle_timeframe": "1m"},
    )
    event_bus = EventBus()
    ws_client = BinanceWebSocketClient(config=config, event_bus=event_bus)

    url = ws_client.build_stream_url()
    assert "stream.binancefuture.com/stream?streams=" in url
    assert "btcusdt@kline_1m" in url
    assert "btcusdt@markPrice@1s" in url
    assert "ethusdt@kline_1m" in url
    assert "ethusdt@markPrice@1s" in url
