"""Continuous aggregates and retention policy for multi-timeframe candle rollups.

Revision ID: 002_continuous_aggregates
Revises: 001_initial_schema
Create Date: 2026-09-01
"""

import sqlalchemy as sa

from alembic import op

revision: str = "002_continuous_aggregates"
down_revision: str | None = "001_initial_schema"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Check if TimescaleDB extension is active
        has_timescale = False
        try:
            res = bind.execute(
                sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
            ).scalar()
            has_timescale = bool(res)
        except Exception:
            has_timescale = False

        if has_timescale:
            # 1. TimescaleDB Continuous Aggregate for 5m candles
            op.execute("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS candles_5m
            WITH (timescaledb.continuous) AS
            SELECT
                symbol,
                time_bucket('5 minutes', open_time) AS bucket,
                first(open_price, open_time) AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                last(close_price, open_time) AS close_price,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trades_count) AS trades_count
            FROM candles
            GROUP BY symbol, bucket
            WITH NO DATA;
            """)

            # 2. TimescaleDB Continuous Aggregate for 15m candles
            op.execute("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS candles_15m
            WITH (timescaledb.continuous) AS
            SELECT
                symbol,
                time_bucket('15 minutes', open_time) AS bucket,
                first(open_price, open_time) AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                last(close_price, open_time) AS close_price,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trades_count) AS trades_count
            FROM candles
            GROUP BY symbol, bucket
            WITH NO DATA;
            """)

            # 3. TimescaleDB Continuous Aggregate for 1h candles
            op.execute("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS candles_1h
            WITH (timescaledb.continuous) AS
            SELECT
                symbol,
                time_bucket('1 hour', open_time) AS bucket,
                first(open_price, open_time) AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                last(close_price, open_time) AS close_price,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trades_count) AS trades_count
            FROM candles
            GROUP BY symbol, bucket
            WITH NO DATA;
            """)

            # 4. Refresh & Retention Policies
            try:
                op.execute("""
                SELECT add_continuous_aggregate_policy('candles_5m',
                    start_offset => INTERVAL '1 day',
                    end_offset => INTERVAL '1 minute',
                    schedule_interval => INTERVAL '1 minute',
                    if_not_exists => TRUE);
                """)
                op.execute("""
                SELECT add_continuous_aggregate_policy('candles_15m',
                    start_offset => INTERVAL '3 days',
                    end_offset => INTERVAL '1 minute',
                    schedule_interval => INTERVAL '5 minutes',
                    if_not_exists => TRUE);
                """)
                op.execute("""
                SELECT add_continuous_aggregate_policy('candles_1h',
                    start_offset => INTERVAL '7 days',
                    end_offset => INTERVAL '1 minute',
                    schedule_interval => INTERVAL '15 minutes',
                    if_not_exists => TRUE);
                """)
                # 90-Day Raw Data Retention Policy:
                # Raw 1-minute tick data older than 90 days is automatically pruned,
                # saving ~95% disk storage while preserving 5m/15m/1h rollups permanently.
                op.execute("""
                SELECT add_retention_policy('candles', INTERVAL '90 days', if_not_exists => TRUE);
                """)
            except Exception:
                pass
        else:
            # Standard PostgreSQL Fallback Views
            op.execute("""
            CREATE OR REPLACE VIEW candles_5m AS
            SELECT
                symbol,
                to_timestamp(floor(extract(epoch from open_time) / 300) * 300) AS bucket,
                (array_agg(open_price ORDER BY open_time ASC))[1] AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                (array_agg(close_price ORDER BY open_time DESC))[1] AS close_price,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trades_count) AS trades_count
            FROM candles
            GROUP BY symbol, to_timestamp(floor(extract(epoch from open_time) / 300) * 300);
            """)

            op.execute("""
            CREATE OR REPLACE VIEW candles_15m AS
            SELECT
                symbol,
                to_timestamp(floor(extract(epoch from open_time) / 900) * 900) AS bucket,
                (array_agg(open_price ORDER BY open_time ASC))[1] AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                (array_agg(close_price ORDER BY open_time DESC))[1] AS close_price,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trades_count) AS trades_count
            FROM candles
            GROUP BY symbol, to_timestamp(floor(extract(epoch from open_time) / 900) * 900);
            """)

            op.execute("""
            CREATE OR REPLACE VIEW candles_1h AS
            SELECT
                symbol,
                date_trunc('hour', open_time) AS bucket,
                (array_agg(open_price ORDER BY open_time ASC))[1] AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                (array_agg(close_price ORDER BY open_time DESC))[1] AS close_price,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trades_count) AS trades_count
            FROM candles
            GROUP BY symbol, date_trunc('hour', open_time);
            """)
    else:
        # SQLite / Unit-test fallback views
        op.execute("""
        CREATE VIEW IF NOT EXISTS candles_5m AS
        SELECT
            symbol,
            datetime((strftime('%s', open_time) / 300) * 300, 'unixepoch') AS bucket,
            min(open_price) AS open_price,
            max(high_price) AS high_price,
            min(low_price) AS low_price,
            max(close_price) AS close_price,
            sum(volume) AS volume,
            sum(quote_volume) AS quote_volume,
            sum(trades_count) AS trades_count
        FROM candles
        GROUP BY symbol, (strftime('%s', open_time) / 300);
        """)

        op.execute("""
        CREATE VIEW IF NOT EXISTS candles_15m AS
        SELECT
            symbol,
            datetime((strftime('%s', open_time) / 900) * 900, 'unixepoch') AS bucket,
            min(open_price) AS open_price,
            max(high_price) AS high_price,
            min(low_price) AS low_price,
            max(close_price) AS close_price,
            sum(volume) AS volume,
            sum(quote_volume) AS quote_volume,
            sum(trades_count) AS trades_count
        FROM candles
        GROUP BY symbol, (strftime('%s', open_time) / 900);
        """)

        op.execute("""
        CREATE VIEW IF NOT EXISTS candles_1h AS
        SELECT
            symbol,
            strftime('%Y-%m-%d %H:00:00', open_time) AS bucket,
            min(open_price) AS open_price,
            max(high_price) AS high_price,
            min(low_price) AS low_price,
            max(close_price) AS close_price,
            sum(volume) AS volume,
            sum(quote_volume) AS quote_volume,
            sum(trades_count) AS trades_count
        FROM candles
        GROUP BY symbol, strftime('%Y-%m-%d %H:00:00', open_time);
        """)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("DROP MATERIALIZED VIEW IF EXISTS candles_1h CASCADE;")
        op.execute("DROP MATERIALIZED VIEW IF EXISTS candles_15m CASCADE;")
        op.execute("DROP MATERIALIZED VIEW IF EXISTS candles_5m CASCADE;")
        op.execute("DROP VIEW IF EXISTS candles_1h CASCADE;")
        op.execute("DROP VIEW IF EXISTS candles_15m CASCADE;")
        op.execute("DROP VIEW IF EXISTS candles_5m CASCADE;")
    else:
        op.execute("DROP VIEW IF EXISTS candles_1h;")
        op.execute("DROP VIEW IF EXISTS candles_15m;")
        op.execute("DROP VIEW IF EXISTS candles_5m;")
