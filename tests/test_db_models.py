"""Tests for SQLAlchemy ORM models, relationships, and constraints."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from trading_platform.core.constants import (
    ContractType,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    ReconciliationEventType,
    RiskDecisionType,
    SignalType,
    TimeInForce,
)
from trading_platform.models import (
    AccountBalance,
    Candle,
    Fill,
    Instrument,
    Order,
    OrderEventLog,
    PortfolioSnapshot,
    Position,
    ReconciliationEvent,
    RiskDecisionAudit,
    Signal,
)


@pytest.mark.asyncio
async def test_instrument_model(async_db_session: AsyncSession):
    """Verify instrument model creation and retrieval."""
    inst = Instrument(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        contract_type=ContractType.PERPETUAL,
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5.0"),
        funding_rate=Decimal("0.00010000"),
    )
    async_db_session.add(inst)
    await async_db_session.flush()

    res = await async_db_session.execute(select(Instrument).where(Instrument.symbol == "BTCUSDT"))
    fetched = res.scalar_one()
    assert fetched.symbol == "BTCUSDT"
    assert fetched.tick_size == Decimal("0.10")
    assert fetched.is_active is True


@pytest.mark.asyncio
async def test_signal_model_and_deduplication(async_db_session: AsyncSession):
    """Verify signal creation and unique deduplication constraint (strategy, symbol, timestamp, type)."""
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    sig1 = Signal(
        strategy_id="strat_v1",
        symbol="ETHUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.15,
        mark_price=3500.0,
        timestamp=now,
    )
    async_db_session.add(sig1)
    await async_db_session.flush()

    # Attempt to insert identical signal (duplicate on process restart mid-candle)
    sig2 = Signal(
        strategy_id="strat_v1",
        symbol="ETHUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.15,
        mark_price=3500.0,
        timestamp=now,
    )
    async_db_session.add(sig2)
    with pytest.raises(IntegrityError):
        await async_db_session.flush()


@pytest.mark.asyncio
async def test_order_and_event_relationships(async_db_session: AsyncSession):
    """Verify order lifecycle tracking with linked OrderEventLog and Fills."""
    order = Order(
        client_order_id="test_ord_1001",
        strategy_id="strat_v1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        quantity=0.1,
        price=60000.0,
        status=OrderStatus.SUBMITTED,
    )
    async_db_session.add(order)
    await async_db_session.flush()

    # Add Event Log
    log = OrderEventLog(
        client_order_id=order.client_order_id,
        event_status=OrderStatus.PARTIALLY_FILLED,
        filled_qty_delta=0.05,
        fill_price=60000.0,
        fee=0.03,
    )
    # Add Fill
    fill = Fill(
        client_order_id=order.client_order_id,
        exchange_trade_id="trade_9999",
        strategy_id="strat_v1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=60000.0,
        quantity=0.05,
        fee=0.03,
        fee_asset="USDT",
    )
    async_db_session.add(log)
    async_db_session.add(fill)
    await async_db_session.flush()

    res = await async_db_session.execute(
        select(Order).where(Order.client_order_id == "test_ord_1001")
    )
    fetched_order = res.scalar_one()
    assert len(fetched_order.events) == 1
    assert len(fetched_order.fills) == 1
    assert fetched_order.fills[0].exchange_trade_id == "trade_9999"


@pytest.mark.asyncio
async def test_position_model_and_constraint(async_db_session: AsyncSession):
    """Verify position unique constraint per strategy and symbol."""
    pos1 = Position(
        strategy_id="strat_v1",
        symbol="BTCUSDT",
        size=0.5,
        entry_price=61000.0,
        mark_price=62000.0,
        leverage=5.0,
        unrealized_pnl=500.0,
        margin_mode=MarginMode.ISOLATED,
    )
    async_db_session.add(pos1)
    await async_db_session.flush()

    pos2 = Position(
        strategy_id="strat_v1",
        symbol="BTCUSDT",
        size=1.0,
        entry_price=62000.0,
        mark_price=62000.0,
    )
    async_db_session.add(pos2)
    with pytest.raises(IntegrityError):
        await async_db_session.flush()


@pytest.mark.asyncio
async def test_portfolio_snapshot_and_balance(async_db_session: AsyncSession):
    """Verify portfolio snapshots and account balances persist correctly."""
    snap = PortfolioSnapshot(
        strategy_id="strat_v1",
        total_wallet_balance=10000.0,
        total_unrealized_pnl=250.0,
        total_margin_balance=10250.0,
        total_initial_margin=2000.0,
        total_maintenance_margin=1000.0,
        total_exposure=10000.0,
        effective_leverage=1.0,
    )
    balance = AccountBalance(
        strategy_id="strat_v1",
        asset="USDT",
        wallet_balance=10000.0,
        available_balance=8000.0,
        locked_balance=2000.0,
    )
    async_db_session.add(snap)
    async_db_session.add(balance)
    await async_db_session.flush()

    res = await async_db_session.execute(
        select(PortfolioSnapshot).where(PortfolioSnapshot.strategy_id == "strat_v1")
    )
    fetched_snap = res.scalar_one()
    assert fetched_snap.total_wallet_balance == 10000.0


@pytest.mark.asyncio
async def test_risk_decision_audit_and_reconciliation(async_db_session: AsyncSession):
    """Verify immutable risk audit logs and reconciliation events."""
    audit = RiskDecisionAudit(
        strategy_id="strat_v1",
        symbol="BTCUSDT",
        decision=RiskDecisionType.RESIZED,
        original_target_exposure=0.50,
        approved_target_exposure=0.10,
        reason="Exceeds max single position limit of 10% equity",
        rule_triggered="MAX_POSITION_LIMIT",
    )
    recon = ReconciliationEvent(
        event_type=ReconciliationEventType.POSITION_MISMATCH,
        symbol="ETHUSDT",
        local_state={"size": 1.0},
        remote_state={"size": 0.0},
        discrepancy_details="Local position shows 1.0 ETH but exchange reports 0",
        action_taken="Synced local position to 0; alerted operator",
        is_resolved=True,
    )
    async_db_session.add(audit)
    async_db_session.add(recon)
    await async_db_session.flush()

    res_audit = await async_db_session.execute(
        select(RiskDecisionAudit).where(RiskDecisionAudit.strategy_id == "strat_v1")
    )
    assert res_audit.scalar_one().decision == RiskDecisionType.RESIZED

    res_recon = await async_db_session.execute(
        select(ReconciliationEvent).where(ReconciliationEvent.symbol == "ETHUSDT")
    )
    assert res_recon.scalar_one().is_resolved is True


@pytest.mark.asyncio
async def test_candle_model(async_db_session: AsyncSession):
    """Verify market data candle model."""
    now = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)
    close_time = datetime(2026, 8, 30, 10, 1, 0, tzinfo=UTC)
    candle = Candle(
        symbol="BTCUSDT",
        timeframe="1m",
        open_time=now,
        close_time=close_time,
        open_price=64000.0,
        high_price=64100.0,
        low_price=63950.0,
        close_price=64050.0,
        volume=12.5,
        quote_volume=800000.0,
        trades_count=350,
        is_closed=True,
    )
    async_db_session.add(candle)
    await async_db_session.flush()

    res = await async_db_session.execute(select(Candle).where(Candle.symbol == "BTCUSDT"))
    fetched = res.scalar_one()
    assert fetched.close_price == 64050.0
    assert fetched.trades_count == 350
