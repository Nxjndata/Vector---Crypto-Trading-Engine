"""Tests for Phase 7: Order Management System (OMS), Execution Engine, State Machine, and Timeout Recovery."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.config import AppConfig
from trading_platform.core.constants import (
    ContractType,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskDecisionType,
    SignalType,
    TimeInForce,
    TradingMode,
)
from trading_platform.core.events import (
    EventBus,
    RiskDecisionEvent,
    SignalEvent,
)
from trading_platform.core.exceptions import (
    DuplicateOrderError,
    ExchangeConnectionError,
    InvalidOrderStateTransitionError,
    OrderNotFoundError,
)
from trading_platform.db.session import get_db_session
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.models.instrument import Instrument
from trading_platform.models.order import Fill, Order, OrderEventLog
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.oms.state_machine import OrderStateMachine
from trading_platform.oms.translator import ApprovedIntentTranslator
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.risk.engine import RiskEngine

# ==============================================================================
# 1. State Machine Transition Tests
# ==============================================================================


def test_order_state_machine_valid_transitions():
    """Verify valid order lifecycle transitions succeed without raising errors."""
    cid = "test_ord_1"
    # CREATED -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED
    OrderStateMachine.validate_transition(cid, OrderStatus.CREATED, OrderStatus.SUBMITTED)
    OrderStateMachine.validate_transition(cid, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED)
    OrderStateMachine.validate_transition(
        cid, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED
    )
    OrderStateMachine.validate_transition(
        cid, OrderStatus.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED
    )
    OrderStateMachine.validate_transition(cid, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED)

    assert OrderStateMachine.is_terminal(OrderStatus.FILLED) is True
    assert OrderStateMachine.is_terminal(OrderStatus.CANCELLED) is True
    assert OrderStateMachine.is_terminal(OrderStatus.REJECTED) is True


def test_order_state_machine_invalid_transitions():
    """Verify illegal transitions raise InvalidOrderStateTransitionError."""
    cid = "test_ord_2"

    # 1. Cannot transition from terminal state FILLED to SUBMITTED
    with pytest.raises(InvalidOrderStateTransitionError, match="Illegal order state transition"):
        OrderStateMachine.validate_transition(cid, OrderStatus.FILLED, OrderStatus.SUBMITTED)

    # 2. Cannot transition from CANCELLED to FILLED
    with pytest.raises(InvalidOrderStateTransitionError, match="Illegal order state transition"):
        OrderStateMachine.validate_transition(cid, OrderStatus.CANCELLED, OrderStatus.FILLED)

    # 3. Cannot skip directly from CREATED to FILLED
    with pytest.raises(InvalidOrderStateTransitionError, match="Illegal order state transition"):
        OrderStateMachine.validate_transition(cid, OrderStatus.CREATED, OrderStatus.FILLED)


# ==============================================================================
# 2. ApprovedIntentTranslator Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_intent_to_order_translator_precision(async_test_engine):
    """Verify ApprovedIntentTranslator converts risk decision to precision-rounded OrderRequest."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_markets.return_value = [
        Instrument(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            contract_type=ContractType.PERPETUAL,
            tick_size=Decimal("0.10"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("100.0"),
        )
    ]
    config = AppConfig()
    config.market_data.active_symbols = ["BTCUSDT"]
    im = InstrumentManager(config=config, adapter=mock_adapter)
    await im.initialize()

    translator = ApprovedIntentTranslator(portfolio_manager=pm, instrument_manager=im)

    # Approved decision for 10% exposure on $10,000 equity at $60,000 mark price
    # Notional = $1,000 -> Quantity = 1000 / 60000 = 0.0166666... -> rounded to 0.016 (step 0.001)
    decision = RiskDecisionEvent(
        signal_id="sig_1",
        strategy_id="strat_test",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Approved",
        snapshot_data={"mark_price": 60000.0},
    )

    order_req = translator.translate_to_order_request(decision)
    assert order_req is not None
    assert order_req.symbol == "BTCUSDT"
    assert order_req.side == OrderSide.BUY
    assert order_req.quantity == 0.016
    assert order_req.order_type == OrderType.MARKET


# ==============================================================================
# 3. Timeout Recovery & No Blind Retries
# ==============================================================================


@pytest.mark.asyncio
async def test_timeout_recovery_no_blind_retry_order_found_on_exchange(async_test_engine):
    """Verify timeout recovery queries get_order without blind retries and syncs state."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    # 1. place_order throws a timeout exception
    mock_adapter.place_order.side_effect = ExchangeConnectionError("HTTP 504 Gateway Timeout")

    # 2. get_order returns that the exchange actually filled the order
    mock_adapter.get_order.return_value = Order(
        client_order_id="test_cid",
        exchange_order_id="ex_999",
        strategy_id="strat_timeout",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        price=60000.0,
        quantity=0.1,
        filled_qty=0.1,
        avg_fill_price=60000.0,
        status=OrderStatus.FILLED,
        fee=3.0,
    )

    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    decision = RiskDecisionEvent(
        signal_id="sig_timeout",
        strategy_id="strat_timeout",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Approved",
        snapshot_data={"mark_price": 60000.0},
    )

    # Execute
    order = await oms.process_approved_decision(decision)

    assert order is not None
    # Verify place_order was called exactly ONCE (never blindly retried)
    assert mock_adapter.place_order.call_count == 1
    # Verify get_order was called with the client order ID
    assert mock_adapter.get_order.call_count == 1
    assert order.status == OrderStatus.FILLED
    assert order.exchange_order_id == "ex_999"


@pytest.mark.asyncio
async def test_timeout_recovery_order_not_found_on_exchange(async_test_engine):
    """Verify timeout recovery marks order REJECTED when exchange confirms order not found."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.place_order.side_effect = ExchangeConnectionError("Socket disconnected")
    mock_adapter.get_order.side_effect = OrderNotFoundError("Order not found on exchange (-2011)")

    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    decision = RiskDecisionEvent(
        signal_id="sig_nf",
        strategy_id="strat_nf",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Approved",
        snapshot_data={"mark_price": 60000.0},
    )

    order = await oms.process_approved_decision(decision)
    assert order is not None
    assert mock_adapter.place_order.call_count == 1
    assert order.status == OrderStatus.REJECTED
    assert "not on exchange" in order.error_message


# ==============================================================================
# 4. In-Flight Duplicate Protection Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_in_flight_duplicate_protection(async_test_engine):
    """Verify submitting a second order for the same (strategy, symbol) while one is in-flight is rejected."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )

    mock_adapter = AsyncMock(spec=ExchangeAdapter)

    async def mock_place(req):
        return Order(
            client_order_id=req.client_order_id,
            exchange_order_id="ex_ack",
            strategy_id="strat_dup",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            price=59000.0,
            quantity=0.1,
            filled_qty=0.0,
            avg_fill_price=0.0,
            status=OrderStatus.ACKNOWLEDGED,
        )

    mock_adapter.place_order.side_effect = mock_place

    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    decision = RiskDecisionEvent(
        signal_id="sig_dup",
        strategy_id="strat_dup",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Approved",
        snapshot_data={"mark_price": 60000.0},
    )

    # 1. First order submitted and acknowledged (in-flight)
    order_1 = await oms.process_approved_decision(decision)
    assert order_1.status == OrderStatus.ACKNOWLEDGED
    assert oms.metrics["in_flight_count"] == 1

    # 2. Second concurrent signal arrives while order 1 is in-flight -> DuplicateOrderError
    with pytest.raises(DuplicateOrderError, match="Duplicate order rejected"):
        await oms.process_approved_decision(decision)


# ==============================================================================
# 5. End-to-End Integration: Signal -> Risk -> OMS -> Fill -> Portfolio
# ==============================================================================


@pytest.mark.asyncio
async def test_end_to_end_signal_to_portfolio_integration(async_test_engine):
    """Verify complete end-to-end event chain from Strategy Signal to Portfolio fill accounting."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()

    # 1. Portfolio Manager
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )

    # 2. Risk Engine
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        session_factory=session_factory,
    )
    risk_engine.record_market_data_tick("BTCUSDT")

    # 3. Mock Exchange Adapter returning immediate fill
    mock_adapter = AsyncMock(spec=ExchangeAdapter)

    async def mock_place_order(req):
        return Order(
            client_order_id=req.client_order_id,
            exchange_order_id="ex_e2e",
            strategy_id="e2e_strat",
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            time_in_force=TimeInForce.GTC,
            price=60000.0,
            quantity=req.quantity,
            filled_qty=req.quantity,
            avg_fill_price=60000.0,
            status=OrderStatus.FILLED,
            fee=3.0,
        )

    mock_adapter.place_order.side_effect = mock_place_order

    # 4. OMS
    OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    # Emit pure intent Signal
    signal = SignalEvent(
        strategy_id="e2e_strat",
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.10,  # 10% equity = $1,000 / $60,000 mark = 0.01666 BTC
        mark_price=60000.0,
        timestamp=datetime.now(UTC),
    )

    # Dispatch signal to EventBus
    await event_bus.publish(signal)

    # Assert portfolio state was updated via emitted FillEvent
    pos = pm.get_position("e2e_strat", "BTCUSDT")
    assert pos.is_open is True
    assert pos.size == pytest.approx(0.016666, abs=0.001)
    assert pos.entry_price == 60000.0

    port = pm.get_portfolio("e2e_strat")
    assert port.wallet_balance == 10000.0 - 3.0  # $10,000 - $3.00 fee
    assert port.realized_pnl == -3.0

    # Verify order and fill persisted in DB
    async with session_factory() as session:
        orders = (await session.execute(select(Order))).scalars().all()
        assert len(orders) >= 1
        assert orders[0].status == OrderStatus.FILLED

        fills = (await session.execute(select(Fill))).scalars().all()
        assert len(fills) >= 1
        assert fills[0].symbol == "BTCUSDT"
        assert fills[0].fee == 3.0

        logs = (await session.execute(select(OrderEventLog))).scalars().all()
        assert len(logs) >= 2  # CREATED, SUBMITTED, FILLED


# ==============================================================================
# 6. Intermediate State Timeout Recovery & True Concurrency Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_timeout_recovery_intermediate_open_state(async_test_engine):
    """Verify timeout recovery when exchange returns an intermediate live state (ACKNOWLEDGED / PARTIALLY_FILLED).

    Confirms order remains in-flight and active, rather than treated as terminal/done.
    """
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    # 1. place_order throws timeout
    mock_adapter.place_order.side_effect = ExchangeConnectionError("HTTP 504 Gateway Timeout")

    # 2. get_order returns an active open order (ACKNOWLEDGED)
    mock_adapter.get_order.return_value = Order(
        client_order_id="test_cid_live",
        exchange_order_id="ex_live_123",
        strategy_id="strat_live",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        price=58000.0,
        quantity=0.1,
        filled_qty=0.0,
        avg_fill_price=0.0,
        status=OrderStatus.ACKNOWLEDGED,
    )

    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    decision = RiskDecisionEvent(
        signal_id="sig_live",
        strategy_id="strat_live",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Approved",
        snapshot_data={"mark_price": 58000.0},
    )

    # 3. Recover from timeout
    order = await oms.process_approved_decision(decision)

    assert order is not None
    assert order.status == OrderStatus.ACKNOWLEDGED
    assert OrderStateMachine.is_terminal(order.status) is False
    # Crucial: order must remain in in-flight registry
    assert oms.metrics["in_flight_count"] == 1

    # 4. Subsequent concurrent signal for the same symbol must be blocked because the order is still live
    with pytest.raises(DuplicateOrderError, match="Duplicate order rejected"):
        await oms.process_approved_decision(decision)


@pytest.mark.asyncio
async def test_in_flight_duplicate_protection_true_concurrent_execution(async_test_engine):
    """Verify in-flight duplicate guard is atomic under genuine concurrent execution using asyncio.gather."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )

    mock_adapter = AsyncMock(spec=ExchangeAdapter)

    # Simulate realistic network execution delay to test race condition
    async def mock_slow_place_order(req):
        import asyncio

        await asyncio.sleep(0.05)
        return Order(
            client_order_id=req.client_order_id,
            exchange_order_id=f"ex_{req.client_order_id}",
            strategy_id=req.strategy_id,
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            time_in_force=TimeInForce.GTC,
            price=60000.0,
            quantity=req.quantity,
            filled_qty=0.0,
            avg_fill_price=0.0,
            status=OrderStatus.ACKNOWLEDGED,
        )

    mock_adapter.place_order.side_effect = mock_slow_place_order

    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    decision = RiskDecisionEvent(
        signal_id="sig_concurrent",
        strategy_id="strat_conc",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Approved",
        snapshot_data={"mark_price": 60000.0},
    )

    # Fire two identical decisions truly concurrently
    import asyncio

    results = await asyncio.gather(
        oms.process_approved_decision(decision),
        oms.process_approved_decision(decision),
        return_exceptions=True,
    )

    # Exactly one succeeded and one raised DuplicateOrderError
    successful_orders = [r for r in results if isinstance(r, Order)]
    duplicate_errors = [r for r in results if isinstance(r, DuplicateOrderError)]

    assert len(successful_orders) == 1
    assert len(duplicate_errors) == 1
    assert "Duplicate order rejected" in str(duplicate_errors[0])
    assert len(oms._orders) == 1


@pytest.mark.asyncio
async def test_in_flight_guard_blocks_duplicate_while_order_is_acknowledged(async_test_engine):
    """Verify that an order resting in ACKNOWLEDGED status strictly blocks subsequent signals for the same symbol."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.place_order.return_value = Order(
        client_order_id="c_ack_1",
        exchange_order_id="ex_ack_1",
        strategy_id="strat_ack",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        quantity=0.01,
        status=OrderStatus.ACKNOWLEDGED,  # Resting on exchange
    )

    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    decision = RiskDecisionEvent(
        signal_id="sig_ack_1",
        strategy_id="strat_ack",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Approved",
        snapshot_data={"mark_price": 60000.0},
    )

    # 1. First submission succeeds and settles in ACKNOWLEDGED state
    first_order = await oms.process_approved_decision(decision)
    assert first_order.status == OrderStatus.ACKNOWLEDGED
    assert ("strat_ack", "BTCUSDT") in oms._in_flight_orders

    # 2. Second submission for same symbol while first is ACKNOWLEDGED must be rejected with DuplicateOrderError
    decision_2 = RiskDecisionEvent(
        signal_id="sig_ack_2",
        strategy_id="strat_ack",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.15,
        approved_target_exposure=0.15,
        reason="Second Signal",
        snapshot_data={"mark_price": 60000.0},
    )

    with pytest.raises(DuplicateOrderError, match="Duplicate order rejected"):
        await oms.process_approved_decision(decision_2)

    # Verify adapter was only called once
    assert mock_adapter.place_order.call_count == 1


@pytest.mark.asyncio
async def test_in_flight_guard_blocks_duplicate_after_db_restoration(async_test_engine):
    """Verify that an ACKNOWLEDGED order restored from database on server reboot blocks duplicate signals."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )

    # 1. Insert an existing ACKNOWLEDGED order into DB (simulating prior server session)
    async with get_db_session(session_factory) as session:
        restored_order = Order(
            client_order_id="c_persisted_ack",
            exchange_order_id="ex_persisted_ack",
            strategy_id="strat_persisted",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            quantity=0.02,
            price=60000.0,
            status=OrderStatus.ACKNOWLEDGED,
            filled_qty=0.0,
            avg_fill_price=0.0,
        )
        session.add(restored_order)

    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    # 2. Hydrate OMS from DB on server startup
    await oms.restore_from_db()
    assert ("strat_persisted", "BTCUSDT") in oms._in_flight_orders

    # 3. New signal arrives: must be blocked by in-flight duplicate guard
    new_decision = RiskDecisionEvent(
        signal_id="sig_after_boot",
        strategy_id="strat_persisted",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Post reboot signal",
        snapshot_data={"mark_price": 60000.0},
    )

    with pytest.raises(DuplicateOrderError, match="Duplicate order rejected"):
        await oms.process_approved_decision(new_decision)

    assert mock_adapter.place_order.call_count == 0


@pytest.mark.asyncio
async def test_manual_and_organic_signals_share_same_submission_lock_and_in_flight_guard(
    async_test_engine,
):
    """Verify that manual operator test signals and organic autonomous strategy signals
    route through the exact same in-process OMS instance, sharing the identical
    _submission_lock and _in_flight_orders dictionary to prevent concurrent duplicate orders."""
    from trading_platform.api.container import PlatformContainer
    from trading_platform.strategy.runner import StrategyRunner
    from trading_platform.strategy.signal_manager import SignalManager
    from trading_platform.strategy.sma_momentum import SMAMomentumStrategy

    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    config = AppConfig(environment=TradingMode.PAPER)

    pm = PortfolioManager(
        event_bus=event_bus, session_factory=session_factory, default_deposit_usd=10000.0
    )
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    im = InstrumentManager(config=config, adapter=mock_adapter)

    # Risk Engine & OMS
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )
    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )

    # Strategy Runner
    sig_mgr = SignalManager(event_bus=event_bus, session_factory=session_factory)
    strat_runner = StrategyRunner(event_bus=event_bus, signal_manager=sig_mgr)
    strategy = SMAMomentumStrategy(strategy_id=config.strategy_id, symbols=["BTCUSDT"])
    strat_runner.register_strategy(strategy)

    container = PlatformContainer(
        config=config,
        event_bus=event_bus,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        oms=oms,
        strategy_runner=strat_runner,
        signal_manager=sig_mgr,
        instrument_manager=im,
        exchange_adapter=mock_adapter,
        session_factory=session_factory,
    )

    # Mock adapter returning ACKNOWLEDGED (resting) order
    mock_adapter.place_order.return_value = Order(
        client_order_id="c_unified_1",
        exchange_order_id="ex_unified_1",
        strategy_id=config.strategy_id,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        quantity=0.01,
        status=OrderStatus.ACKNOWLEDGED,
    )

    # 1. Dispatch manual test signal via EventBus (same pathway as POST /api/strategy/test-signal)
    manual_signal = SignalEvent(
        strategy_id=config.strategy_id,
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.10,
        mark_price=60000.0,
        metadata={"source": "MANUAL_TEST_SIGNAL"},
    )
    await container.event_bus.publish(manual_signal)
    await asyncio.sleep(0.05)  # Allow async processing

    # Verify manual order was placed and is registered in-flight
    assert (config.strategy_id, "BTCUSDT") in container.oms._in_flight_orders
    assert mock_adapter.place_order.call_count == 1

    # 2. Concurrently emit organic autonomous strategy signal on the same container EventBus
    organic_signal = SignalEvent(
        strategy_id=config.strategy_id,
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.15,
        mark_price=60000.0,
        metadata={"source": "SMA_CROSSOVER"},
    )

    # Processing the organic signal must encounter the exact same _submission_lock and be blocked
    # (Risk engine approves it, but OMS throws DuplicateOrderError because manual signal is in-flight)
    with pytest.raises(DuplicateOrderError, match="Duplicate order rejected"):
        decision = await container.risk_engine.evaluate_signal(organic_signal)
        await container.oms.process_approved_decision(decision)

    # Verify adapter was never called a second time
    assert mock_adapter.place_order.call_count == 1


