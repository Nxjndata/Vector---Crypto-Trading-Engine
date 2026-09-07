"""Tests for Phase 8: State Reconciliation Engine, Classification, and Kill-Switch Integration."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import (
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    ReconciliationEventType,
    RiskDecisionType,
    SignalType,
    TimeInForce,
)
from trading_platform.core.events import EventBus, SignalEvent
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.models.order import Order
from trading_platform.models.portfolio import AccountBalance
from trading_platform.models.position import Position
from trading_platform.models.reconciliation import ReconciliationEvent
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.portfolio.state import PositionState
from trading_platform.reconciliation.classifier import ReconciliationClassifier
from trading_platform.reconciliation.engine import ReconciliationEngine
from trading_platform.reconciliation.models import (
    DiscrepancySeverity,
    ReconciliationToleranceConfig,
)
from trading_platform.risk.engine import RiskEngine

# ==============================================================================
# 1. Independent Classifier Tests
# ==============================================================================


def test_classify_balance_minor_drift_within_tolerance():
    """Verify minor balance drift (<= $1.00) is classified as LOG_ONLY / auto-sync and does not halt."""
    config = ReconciliationToleranceConfig(balance_drift_tolerance_usd=1.0)
    report = ReconciliationClassifier.classify_balance(
        strategy_id="strat_test",
        local_balance=10000.0,
        remote_balance=9999.50,  # $0.50 drift
        config=config,
    )
    assert report is not None
    assert report.event_type == ReconciliationEventType.BALANCE_MISMATCH
    assert report.severity == DiscrepancySeverity.LOG_ONLY
    assert "LOG_AND_RESYNC" in report.action_taken


def test_classify_balance_hard_breach():
    """Verify major balance drift (> $100.00) is classified as CRITICAL_HALT."""
    config = ReconciliationToleranceConfig(balance_critical_threshold_usd=100.0)
    report = ReconciliationClassifier.classify_balance(
        strategy_id="strat_test",
        local_balance=10000.0,
        remote_balance=8500.0,  # $1,500 drift
        config=config,
    )
    assert report is not None
    assert report.event_type == ReconciliationEventType.BALANCE_MISMATCH
    assert report.severity == DiscrepancySeverity.CRITICAL_HALT
    assert "CRITICAL_HALT" in report.action_taken


def test_classify_position_mismatch_unmanaged_remote_position():
    """Verify unexpected remote position on exchange triggers CRITICAL_HALT."""
    config = ReconciliationToleranceConfig()
    local_pos = PositionState(strategy_id="strat_test", symbol="BTCUSDT", size=0.0)
    remote_pos = Position(
        strategy_id="strat_test",
        symbol="BTCUSDT",
        size=1.5,
        entry_price=60000.0,
        mark_price=60000.0,
        margin_mode=MarginMode.ISOLATED,
    )

    report = ReconciliationClassifier.classify_position(
        strategy_id="strat_test",
        symbol="BTCUSDT",
        local_pos=local_pos,
        remote_pos=remote_pos,
        config=config,
    )
    assert report is not None
    assert report.event_type == ReconciliationEventType.POSITION_MISMATCH
    assert report.severity == DiscrepancySeverity.CRITICAL_HALT
    assert "Unmanaged position" in report.details


def test_classify_ghost_order():
    """Verify order open on exchange but absent/closed locally triggers GHOST_ORDER CRITICAL_HALT."""
    local_orders = [
        Order(
            client_order_id="cid_closed",
            strategy_id="strat_test",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.1,
            status=OrderStatus.FILLED,
        )
    ]
    remote_open_orders = [
        Order(
            client_order_id="cid_ghost",
            strategy_id="strat_test",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.5,
            status=OrderStatus.ACKNOWLEDGED,
        )
    ]

    reports = ReconciliationClassifier.classify_orders(
        local_orders=local_orders,
        remote_open_orders=remote_open_orders,
    )
    assert len(reports) == 1
    assert reports[0].event_type == ReconciliationEventType.GHOST_ORDER
    assert reports[0].severity == DiscrepancySeverity.CRITICAL_HALT
    assert "GHOST ORDER" in reports[0].details


def test_classify_missing_order():
    """Verify in-flight local order absent on exchange triggers MISSING_ORDER AUTO_RESYNC."""
    local_orders = [
        Order(
            client_order_id="cid_in_flight",
            strategy_id="strat_test",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.1,
            status=OrderStatus.ACKNOWLEDGED,
        )
    ]
    remote_open_orders = []  # Absent from exchange

    reports = ReconciliationClassifier.classify_orders(
        local_orders=local_orders,
        remote_open_orders=remote_open_orders,
    )
    assert len(reports) == 1
    assert reports[0].event_type == ReconciliationEventType.MISSING_ORDER
    assert reports[0].severity == DiscrepancySeverity.AUTO_RESYNC
    assert "MISSING ORDER" in reports[0].details


# ==============================================================================
# 2. Hard-Breach Triggers Kill Switch Integration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_reconciliation_hard_breach_triggers_kill_switch_end_to_end(
    async_test_engine,
):
    """Verify that a reconciliation hard breach (e.g. ghost order) engages the Risk Engine kill switch and halts subsequent signal approval."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        session_factory=session_factory,
    )
    risk_engine.record_market_data_tick("BTCUSDT")

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_balance.return_value = [
        AccountBalance(
            strategy_id="strat_test",
            asset="USDT",
            wallet_balance=10000.0,
            available_balance=10000.0,
            locked_balance=0.0,
        )
    ]
    mock_adapter.get_positions.return_value = []
    # Exchange returns a ghost order that local OMS does not know about
    mock_adapter.get_open_orders.return_value = [
        Order(
            client_order_id="c_untracked_ghost_999",
            strategy_id="strat_test",
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=65000.0,
            quantity=0.5,
            status=OrderStatus.ACKNOWLEDGED,
        )
    ]

    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    reconciliation_engine = ReconciliationEngine(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        oms=oms,
        session_factory=session_factory,
    )

    # 1. Kill switch is inactive initially
    assert risk_engine.is_kill_switch_active is False

    # 2. Run reconciliation cycle -> discovers GHOST_ORDER
    discrepancies = await reconciliation_engine.reconcile_now()
    assert len(discrepancies) == 1
    assert discrepancies[0].event_type == ReconciliationEventType.GHOST_ORDER

    # 3. Verify kill switch is engaged
    assert risk_engine.is_kill_switch_active is True

    # 4. Prove that a subsequent valid strategy signal is REJECTED by Risk Engine
    signal = SignalEvent(
        strategy_id="strat_test",
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.05,
        mark_price=60000.0,
        timestamp=datetime.now(UTC),
    )
    decision = await risk_engine.evaluate_signal(signal)
    assert decision.decision_type == RiskDecisionType.REJECTED
    assert "Kill-Switch is active" in decision.reason

    # 5. Verify audit row was persisted in reconciliation_events
    async with session_factory() as session:
        events = (await session.execute(select(ReconciliationEvent))).scalars().all()
        assert len(events) >= 1
        assert events[0].event_type == ReconciliationEventType.GHOST_ORDER
        assert events[0].is_resolved is False


# ==============================================================================
# 3. Tolerance Auto-Resync Integration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_reconciliation_minor_drift_auto_resync(async_test_engine):
    """Verify minor balance drift within tolerance auto-resyncs local portfolio without triggering kill switch."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    # Remote exchange balance has minor -$0.50 fee drift
    mock_adapter.get_balance.return_value = [
        AccountBalance(
            strategy_id="strat_test",
            asset="USDT",
            wallet_balance=9999.50,
            available_balance=9999.50,
            locked_balance=0.0,
        )
    ]
    mock_adapter.get_positions.return_value = []
    mock_adapter.get_open_orders.return_value = []

    reconciliation_engine = ReconciliationEngine(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        session_factory=session_factory,
        config=ReconciliationToleranceConfig(balance_drift_tolerance_usd=1.0),
    )

    # Initial balance is $10,000.00
    assert pm.get_portfolio("strat_test").wallet_balance == 10000.0

    # Run reconciliation
    discrepancies = await reconciliation_engine.reconcile_now()
    assert len(discrepancies) == 1
    assert discrepancies[0].severity == DiscrepancySeverity.LOG_ONLY

    # Kill switch must NOT be active
    assert risk_engine.is_kill_switch_active is False

    # Portfolio balance must be auto-resynced to $9,999.50
    assert pm.get_portfolio("strat_test").wallet_balance == 9999.50


# ==============================================================================
# 4. Periodic Reconciliation Loop Execution Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_reconciliation_periodic_loop_runs_independently():
    """Verify the periodic reconciliation background loop runs on its configured schedule."""
    event_bus = EventBus()
    pm = PortfolioManager(event_bus=event_bus, session_factory=None, default_deposit_usd=10000.0)
    risk_engine = RiskEngine(event_bus=event_bus, portfolio_manager=pm)

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_balance.return_value = []
    mock_adapter.get_positions.return_value = []
    mock_adapter.get_open_orders.return_value = []

    # Run every 50ms for testing
    config = ReconciliationToleranceConfig(interval_seconds=0.05)
    engine = ReconciliationEngine(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        config=config,
    )

    await engine.start()
    assert engine.metrics["is_running"] is True

    # Sleep for 160ms (should execute ~3 cycles)
    await asyncio.sleep(0.16)

    await engine.stop()
    assert engine.metrics["is_running"] is False
    assert mock_adapter.get_balance.call_count >= 2


# ==============================================================================
# 5. Missed Fill Recovery on MISSING_ORDER Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_reconciliation_missing_order_found_filled_on_exchange(async_test_engine):
    """Verify that a MISSING_ORDER that turns out to be FILLED on exchange queries get_order, emits a FillEvent, and updates PortfolioManager position and PnL correctly."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )
    risk_engine = RiskEngine(
        event_bus=event_bus, portfolio_manager=pm, session_factory=session_factory
    )

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    # Balance and positions empty/standard
    mock_adapter.get_balance.return_value = [
        AccountBalance(
            strategy_id="strat_test",
            asset="USDT",
            wallet_balance=10000.0,
            available_balance=10000.0,
            locked_balance=0.0,
        )
    ]
    mock_adapter.get_positions.return_value = []
    # Exchange open orders is empty (order completed and left the open orders book)
    mock_adapter.get_open_orders.return_value = []

    # But when queried via get_order, exchange confirms the order was FILLED
    mock_adapter.get_order.return_value = Order(
        client_order_id="c_missing_filled_101",
        exchange_order_id="ex_fill_777",
        strategy_id="strat_test",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        price=60000.0,
        quantity=0.25,
        filled_qty=0.25,
        avg_fill_price=60000.0,
        status=OrderStatus.FILLED,
        fee=7.50,
    )

    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    # Local OMS has the order in-flight (ACKNOWLEDGED)
    local_order = Order(
        client_order_id="c_missing_filled_101",
        strategy_id="strat_test",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        quantity=0.25,
        status=OrderStatus.ACKNOWLEDGED,
        filled_qty=0.0,
        avg_fill_price=0.0,
        fee=0.0,
    )
    oms._orders[local_order.client_order_id] = local_order
    oms._in_flight_orders[("strat_test", "BTCUSDT")] = local_order.client_order_id

    reconciliation_engine = ReconciliationEngine(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        oms=oms,
        session_factory=session_factory,
    )

    # Run reconciliation
    discrepancies = await reconciliation_engine.reconcile_now()
    assert len(discrepancies) == 1
    assert discrepancies[0].event_type == ReconciliationEventType.MISSING_ORDER

    # Assert get_order was queried for the exact client order ID
    assert mock_adapter.get_order.call_count == 1

    # Assert local order status was updated to FILLED
    assert local_order.status == OrderStatus.FILLED
    assert local_order.filled_qty == 0.25
    assert local_order.avg_fill_price == 60000.0

    # Assert PortfolioManager received the missed FillEvent and opened the position
    pos = pm.get_position("strat_test", "BTCUSDT")
    assert pos.is_open is True
    assert pos.size == 0.25
    assert pos.entry_price == 60000.0

    port = pm.get_portfolio("strat_test")
    assert port.wallet_balance == 10000.0 - 7.50  # Fee deducted

    # In-flight guard must now be cleared
    assert oms.metrics["in_flight_count"] == 0
