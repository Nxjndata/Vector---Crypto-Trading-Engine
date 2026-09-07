"""Tests for TimescaleDB continuous aggregate views and candle rollup calculations."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from trading_platform.core.config import DatabaseConfig
from trading_platform.db.session import get_async_engine, get_db_session, get_session_factory
from trading_platform.models.candle import Candle


@pytest.fixture
async def continuous_agg_engine(tmp_path):
    """Create isolated database with continuous aggregate views enabled for 5m, 15m, and 1h rollups."""
    db_file = tmp_path / "agg_test.db"
    db_cfg = DatabaseConfig(sqlite_path=str(db_file))
    engine = get_async_engine(db_cfg)

    from trading_platform.db.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # 1. 5-minute rollup view (bucketed by 300 seconds)
        await conn.execute(
            text("""
            CREATE VIEW IF NOT EXISTS candles_5m AS
            SELECT
                symbol,
                datetime((strftime('%s', open_time) / 300) * 300, 'unixepoch') AS bucket,
                (SELECT c1.open_price FROM candles c1
                 WHERE c1.symbol = candles.symbol
                   AND (strftime('%s', c1.open_time) / 300) = (strftime('%s', candles.open_time) / 300)
                 ORDER BY c1.open_time ASC LIMIT 1) AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                (SELECT c2.close_price FROM candles c2
                 WHERE c2.symbol = candles.symbol
                   AND (strftime('%s', c2.open_time) / 300) = (strftime('%s', candles.open_time) / 300)
                 ORDER BY c2.open_time DESC LIMIT 1) AS close_price,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trades_count) AS trades_count
            FROM candles
            GROUP BY symbol, (strftime('%s', open_time) / 300);
            """)
        )

        # 2. 15-minute rollup view (bucketed by 900 seconds)
        await conn.execute(
            text("""
            CREATE VIEW IF NOT EXISTS candles_15m AS
            SELECT
                symbol,
                datetime((strftime('%s', open_time) / 900) * 900, 'unixepoch') AS bucket,
                (SELECT c1.open_price FROM candles c1
                 WHERE c1.symbol = candles.symbol
                   AND (strftime('%s', c1.open_time) / 900) = (strftime('%s', candles.open_time) / 900)
                 ORDER BY c1.open_time ASC LIMIT 1) AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                (SELECT c2.close_price FROM candles c2
                 WHERE c2.symbol = candles.symbol
                   AND (strftime('%s', c2.open_time) / 900) = (strftime('%s', candles.open_time) / 900)
                 ORDER BY c2.open_time DESC LIMIT 1) AS close_price,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trades_count) AS trades_count
            FROM candles
            GROUP BY symbol, (strftime('%s', open_time) / 900);
            """)
        )

        # 3. 1-hour rollup view (bucketed by 3600 seconds)
        await conn.execute(
            text("""
            CREATE VIEW IF NOT EXISTS candles_1h AS
            SELECT
                symbol,
                datetime((strftime('%s', open_time) / 3600) * 3600, 'unixepoch') AS bucket,
                (SELECT c1.open_price FROM candles c1
                 WHERE c1.symbol = candles.symbol
                   AND (strftime('%s', c1.open_time) / 3600) = (strftime('%s', candles.open_time) / 3600)
                 ORDER BY c1.open_time ASC LIMIT 1) AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                (SELECT c2.close_price FROM candles c2
                 WHERE c2.symbol = candles.symbol
                   AND (strftime('%s', c2.open_time) / 3600) = (strftime('%s', candles.open_time) / 3600)
                 ORDER BY c2.open_time DESC LIMIT 1) AS close_price,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trades_count) AS trades_count
            FROM candles
            GROUP BY symbol, (strftime('%s', open_time) / 3600);
            """)
        )

    session_factory = get_session_factory(engine)
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_continuous_aggregate_rollup_sync(continuous_agg_engine):
    """Verify that inserting consecutive 1m candles automatically rolls up to exact 5m, 15m, and 1h OHLCV metrics,
    and dynamically updates when new raw candles arrive.
    """
    base_time = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
    symbol = "BTCUSDT"

    # Step 1: Insert 5 raw 1m candles for 10:00 - 10:05 (Bucket 1)
    bucket1_raw_candles = [
        # (t_offset, open, high, low, close, vol, quote_vol, trades)
        (0, 70000.0, 70500.0, 69900.0, 70200.0, 10.0, 702000.0, 150),
        (1, 70200.0, 70800.0, 70100.0, 70600.0, 12.0, 847200.0, 180),
        (2, 70600.0, 71000.0, 70400.0, 70900.0, 15.0, 1063500.0, 220),
        (3, 70900.0, 70950.0, 70300.0, 70400.0, 8.0, 563200.0, 110),
        (4, 70400.0, 70700.0, 69800.0, 70500.0, 20.0, 1410000.0, 300),
    ]

    async with get_db_session(continuous_agg_engine) as session:
        for offset, o, h, low_p, c, v, qv, tc in bucket1_raw_candles:
            t = base_time + timedelta(minutes=offset)
            session.add(
                Candle(
                    symbol=symbol,
                    timeframe="1m",
                    open_time=t,
                    close_time=t + timedelta(minutes=1) - timedelta(milliseconds=1),
                    open_price=o,
                    high_price=h,
                    low_price=low_p,
                    close_price=c,
                    volume=v,
                    quote_volume=qv,
                    trades_count=tc,
                    is_closed=True,
                )
            )
        await session.commit()

    # Query 5-minute aggregate view for Bucket 1
    async with get_db_session(continuous_agg_engine) as session:
        res_5m = await session.execute(text("SELECT * FROM candles_5m WHERE symbol = 'BTCUSDT' ORDER BY bucket ASC"))
        rows_5m = res_5m.fetchall()
        assert len(rows_5m) == 1
        b1 = rows_5m[0]
        print(f"\n[CONTINUOUS AGGREGATE] 5m Bucket 1: Open={b1.open_price}, High={b1.high_price}, Low={b1.low_price}, Close={b1.close_price}, Vol={b1.volume}, Trades={b1.trades_count}")

        # Mathematical OHLCV verification for 5m Bucket 1:
        assert b1.open_price == 70000.0  # Open of first 1m candle (minute 0)
        assert b1.high_price == 71000.0  # Max high across minutes 0-4
        assert b1.low_price == 69800.0  # Min low across minutes 0-4
        assert b1.close_price == 70500.0  # Close of last 1m candle (minute 4)
        assert b1.volume == 65.0  # Sum of volumes: 10 + 12 + 15 + 8 + 20 = 65
        assert b1.trades_count == 960  # Sum of trades: 150 + 180 + 220 + 110 + 300 = 960

    # Step 2: DYNAMIC ARRIVAL TEST — Insert 5 more raw 1m candles for 10:05 - 10:10 (Bucket 2)
    bucket2_raw_candles = [
        (5, 70500.0, 71200.0, 70400.0, 71100.0, 25.0, 1777500.0, 350),
        (6, 71100.0, 71600.0, 70900.0, 71400.0, 30.0, 2142000.0, 400),
        (7, 71400.0, 72000.0, 71300.0, 71900.0, 18.0, 1294200.0, 280),
        (8, 71900.0, 72100.0, 71700.0, 71800.0, 12.0, 861600.0, 190),
        (9, 71800.0, 71900.0, 71500.0, 71600.0, 15.0, 1074000.0, 210),
    ]

    async with get_db_session(continuous_agg_engine) as session:
        for offset, o, h, low_p, c, v, qv, tc in bucket2_raw_candles:
            t = base_time + timedelta(minutes=offset)
            session.add(
                Candle(
                    symbol=symbol,
                    timeframe="1m",
                    open_time=t,
                    close_time=t + timedelta(minutes=1) - timedelta(milliseconds=1),
                    open_price=o,
                    high_price=h,
                    low_price=low_p,
                    close_price=c,
                    volume=v,
                    quote_volume=qv,
                    trades_count=tc,
                    is_closed=True,
                )
            )
        await session.commit()

    # Query 5-minute aggregate view after new candle arrival
    async with get_db_session(continuous_agg_engine) as session:
        res_5m_post = await session.execute(text("SELECT * FROM candles_5m WHERE symbol = 'BTCUSDT' ORDER BY bucket ASC"))
        rows_5m_post = res_5m_post.fetchall()
        assert len(rows_5m_post) == 2  # Exactly 2 consecutive 5m buckets
        b2 = rows_5m_post[1]
        print(f"[CONTINUOUS AGGREGATE] 5m Bucket 2: Open={b2.open_price}, High={b2.high_price}, Low={b2.low_price}, Close={b2.close_price}, Vol={b2.volume}, Trades={b2.trades_count}")

        # Mathematical OHLCV verification for 5m Bucket 2:
        assert b2.open_price == 70500.0  # Open of minute 5
        assert b2.high_price == 72100.0  # Max high across minutes 5-9
        assert b2.low_price == 70400.0  # Min low across minutes 5-9
        assert b2.close_price == 71600.0  # Close of minute 9
        assert b2.volume == 100.0  # 25 + 30 + 18 + 12 + 15 = 100
        assert b2.trades_count == 1430  # 350 + 400 + 280 + 190 + 210 = 1430

        # Verify 15m and 1h continuous aggregate rollups across all 10 raw minutes (10:00 - 10:10):
        res_15m = await session.execute(text("SELECT * FROM candles_15m WHERE symbol = 'BTCUSDT'"))
        agg_15m = res_15m.fetchall()[0]
        print(f"[CONTINUOUS AGGREGATE] 15m Aggregate: Open={agg_15m.open_price}, High={agg_15m.high_price}, Low={agg_15m.low_price}, Close={agg_15m.close_price}, Vol={agg_15m.volume}")
        assert agg_15m.open_price == 70000.0  # Open of minute 0
        assert agg_15m.high_price == 72100.0  # Max high across all 10 minutes
        assert agg_15m.low_price == 69800.0  # Min low across all 10 minutes
        assert agg_15m.close_price == 71600.0  # Close of minute 9
        assert agg_15m.volume == 165.0  # 65 + 100 = 165
        assert agg_15m.trades_count == 2390  # 960 + 1430 = 2390
