"""Initial schema for trading platform entities.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-08-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Instruments
    op.create_table(
        "instruments",
        sa.Column("symbol", sa.String(30), primary_key=True),
        sa.Column("base_asset", sa.String(20), nullable=False),
        sa.Column("quote_asset", sa.String(20), nullable=False, server_default="USDT"),
        sa.Column(
            "contract_type",
            sa.Enum("PERPETUAL", "CURRENT_QUARTER", "NEXT_QUARTER", name="contracttype"),
            nullable=False,
        ),
        sa.Column("tick_size", sa.Numeric(18, 8), nullable=False),
        sa.Column("step_size", sa.Numeric(18, 8), nullable=False),
        sa.Column("min_qty", sa.Numeric(18, 8), nullable=False),
        sa.Column("min_notional", sa.Numeric(18, 8), nullable=False),
        sa.Column("maker_fee", sa.Numeric(8, 6), nullable=False, server_default="0.0002"),
        sa.Column("taker_fee", sa.Numeric(8, 6), nullable=False, server_default="0.0005"),
        sa.Column("funding_rate", sa.Numeric(10, 8), nullable=True),
        sa.Column("next_funding_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. Signals
    op.create_table(
        "signals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("strategy_id", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("signal_type", sa.Enum("BUY", "SELL", "FLAT", name="signaltype"), nullable=False),
        sa.Column("target_exposure", sa.Float(), nullable=False),
        sa.Column("mark_price", sa.Float(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "strategy_id", "symbol", "timestamp", "signal_type", name="uq_signal_dedup"
        ),
    )
    op.create_index("ix_signals_strategy_id", "signals", ["strategy_id"])
    op.create_index("ix_signals_symbol", "signals", ["symbol"])
    op.create_index("ix_signals_timestamp", "signals", ["timestamp"])
    op.create_index("ix_signals_strategy_symbol", "signals", ["strategy_id", "symbol"])

    # 3. Orders
    op.create_table(
        "orders",
        sa.Column("client_order_id", sa.String(64), primary_key=True),
        sa.Column("exchange_order_id", sa.String(64), nullable=True),
        sa.Column("strategy_id", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("side", sa.Enum("BUY", "SELL", name="orderside"), nullable=False),
        sa.Column(
            "order_type",
            sa.Enum("LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET", name="ordertype"),
            nullable=False,
        ),
        sa.Column(
            "time_in_force",
            sa.Enum("GTC", "IOC", "FOK", "GTX", name="timeinforce"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "CREATED",
                "SUBMITTED",
                "ACKNOWLEDGED",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELLED",
                "REJECTED",
                "EXPIRED",
                "UNKNOWN",
                name="orderstatus",
            ),
            nullable=False,
        ),
        sa.Column("filled_qty", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("avg_fill_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("cum_quote", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("fee", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("fee_asset", sa.String(20), nullable=False, server_default="USDT"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_orders_exchange_order_id", "orders", ["exchange_order_id"])
    op.create_index("ix_orders_strategy_id", "orders", ["strategy_id"])
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_status", "orders", ["status"])

    # 4. Order Event Logs
    op.create_table(
        "order_event_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_order_id",
            sa.String(64),
            sa.ForeignKey("orders.client_order_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_status",
            sa.Enum(
                "CREATED",
                "SUBMITTED",
                "ACKNOWLEDGED",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELLED",
                "REJECTED",
                "EXPIRED",
                "UNKNOWN",
                name="orderstatus",
            ),
            nullable=False,
        ),
        sa.Column("filled_qty_delta", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("fee", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_order_event_logs_client_order_id", "order_event_logs", ["client_order_id"])
    op.create_index("ix_order_event_logs_timestamp", "order_event_logs", ["timestamp"])

    # 5. Fills
    op.create_table(
        "fills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_order_id",
            sa.String(64),
            sa.ForeignKey("orders.client_order_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("exchange_trade_id", sa.String(64), nullable=True),
        sa.Column("strategy_id", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("side", sa.Enum("BUY", "SELL", name="orderside"), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("fee_asset", sa.String(20), nullable=False, server_default="USDT"),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fills_client_order_id", "fills", ["client_order_id"])
    op.create_index("ix_fills_exchange_trade_id", "fills", ["exchange_trade_id"])
    op.create_index("ix_fills_strategy_id", "fills", ["strategy_id"])
    op.create_index("ix_fills_symbol", "fills", ["symbol"])
    op.create_index("ix_fills_timestamp", "fills", ["timestamp"])
    op.create_index("ix_fills_strategy_symbol", "fills", ["strategy_id", "symbol"])

    # 6. Positions
    op.create_table(
        "positions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("strategy_id", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("size", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("entry_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("mark_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("liquidation_price", sa.Float(), nullable=True),
        sa.Column("leverage", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("funding_pnl", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("margin_mode", sa.Enum("ISOLATED", "CROSSED", name="marginmode"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("strategy_id", "symbol", name="uq_strategy_symbol_position"),
    )
    op.create_index("ix_positions_strategy_id", "positions", ["strategy_id"])
    op.create_index("ix_positions_symbol", "positions", ["symbol"])
    op.create_index("ix_positions_strategy_symbol", "positions", ["strategy_id", "symbol"])

    # 7. Portfolio Snapshots
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("strategy_id", sa.String(50), nullable=False),
        sa.Column("total_wallet_balance", sa.Float(), nullable=False),
        sa.Column("total_unrealized_pnl", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_realized_pnl", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_funding_pnl", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_margin_balance", sa.Float(), nullable=False),
        sa.Column("total_initial_margin", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_maintenance_margin", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_exposure", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("effective_leverage", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_portfolio_snapshots_strategy_id", "portfolio_snapshots", ["strategy_id"])
    op.create_index("ix_portfolio_snapshots_timestamp", "portfolio_snapshots", ["timestamp"])
    op.create_index(
        "ix_portfolio_snapshots_strat_time", "portfolio_snapshots", ["strategy_id", "timestamp"]
    )

    # 8. Account Balances
    op.create_table(
        "account_balances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("strategy_id", sa.String(50), nullable=False),
        sa.Column("asset", sa.String(20), nullable=False, server_default="USDT"),
        sa.Column("wallet_balance", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("available_balance", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("locked_balance", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("strategy_id", "asset", name="uq_strategy_asset_balance"),
    )
    op.create_index("ix_account_balances_strategy_id", "account_balances", ["strategy_id"])

    # 9. Risk Decision Audit Log
    op.create_table(
        "risk_decision_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "signal_id",
            sa.String(36),
            sa.ForeignKey("signals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("strategy_id", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column(
            "decision",
            sa.Enum("APPROVED", "RESIZED", "REJECTED", name="riskdecisiontype"),
            nullable=False,
        ),
        sa.Column("original_target_exposure", sa.Float(), nullable=False),
        sa.Column("approved_target_exposure", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("rule_triggered", sa.String(100), nullable=True),
        sa.Column("snapshot_data", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_risk_decision_audit_signal_id", "risk_decision_audit", ["signal_id"])
    op.create_index("ix_risk_decision_audit_strategy_id", "risk_decision_audit", ["strategy_id"])
    op.create_index("ix_risk_decision_audit_symbol", "risk_decision_audit", ["symbol"])
    op.create_index("ix_risk_decision_audit_decision", "risk_decision_audit", ["decision"])
    op.create_index("ix_risk_decision_audit_timestamp", "risk_decision_audit", ["timestamp"])
    op.create_index("ix_risk_audit_strat_time", "risk_decision_audit", ["strategy_id", "timestamp"])

    # 10. Reconciliation Events
    op.create_table(
        "reconciliation_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "BALANCE_MISMATCH",
                "POSITION_MISMATCH",
                "ORDER_MISMATCH",
                "FILL_MISMATCH",
                "STATE_RESYNC",
                name="reconciliationeventtype",
            ),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(30), nullable=True),
        sa.Column("local_state", sa.JSON(), nullable=False),
        sa.Column("remote_state", sa.JSON(), nullable=False),
        sa.Column("discrepancy_details", sa.Text(), nullable=False),
        sa.Column("action_taken", sa.Text(), nullable=False),
        sa.Column("is_resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reconciliation_events_event_type", "reconciliation_events", ["event_type"])
    op.create_index("ix_reconciliation_events_symbol", "reconciliation_events", ["symbol"])
    op.create_index(
        "ix_reconciliation_events_is_resolved", "reconciliation_events", ["is_resolved"]
    )
    op.create_index("ix_reconciliation_events_timestamp", "reconciliation_events", ["timestamp"])
    op.create_index(
        "ix_reconciliation_type_time", "reconciliation_events", ["event_type", "timestamp"]
    )

    # 11. Candles
    op.create_table(
        "candles",
        sa.Column("symbol", sa.String(30), primary_key=True),
        sa.Column("timeframe", sa.String(10), primary_key=True),
        sa.Column("open_time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_price", sa.Float(), nullable=False),
        sa.Column("high_price", sa.Float(), nullable=False),
        sa.Column("low_price", sa.Float(), nullable=False),
        sa.Column("close_price", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("quote_volume", sa.Float(), nullable=False),
        sa.Column("trades_count", sa.Integer(), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index("ix_candles_symbol_tf_time", "candles", ["symbol", "timeframe", "open_time"])

    # 12. Convert candles to TimescaleDB hypertable if on PostgreSQL with TimescaleDB
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        try:
            res = bind.execute(
                sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
            ).scalar()
            if res:
                op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
                op.execute(
                    "SELECT create_hypertable('candles', 'open_time', if_not_exists => TRUE, migrate_data => TRUE);"
                )
        except Exception:
            pass


def downgrade() -> None:
    op.drop_table("candles")
    op.drop_table("reconciliation_events")
    op.drop_table("risk_decision_audit")
    op.drop_table("account_balances")
    op.drop_table("portfolio_snapshots")
    op.drop_table("positions")
    op.drop_table("fills")
    op.drop_table("order_event_logs")
    op.drop_table("orders")
    op.drop_table("signals")
    op.drop_table("instruments")
