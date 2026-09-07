import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.clock import ClockSync
from trading_platform.core.config import AppConfig
from trading_platform.core.constants import (
    OrderSide,
    OrderStatus,
    OrderType,
    ReconciliationEventType,
    RiskDecisionType,
    SignalType,
    TimeInForce,
)
from trading_platform.core.events import (
    CandleEvent,
    EventBus,
    RiskDecisionEvent,
    SignalEvent,
)
from trading_platform.engine.platform import PlatformEngine
from trading_platform.exchange.adapter import OrderRequest
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.exchange.paper import PaperExchangeAdapter
from trading_platform.models.portfolio import PortfolioSnapshot
from trading_platform.models.reconciliation import ReconciliationEvent
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.reconciliation.engine import ReconciliationEngine
from trading_platform.risk.engine import RiskEngine
from trading_platform.strategy.runner import StrategyRunner
from trading_platform.strategy.signal_manager import SignalManager
from trading_platform.strategy.sma_momentum import SMAMomentumStrategy

# ==============================================================================
# 1. Paper Simulator Matching Engine Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_paper_adapter_market_order_slippage_and_taker_fee():
    """Verify market order fills immediately at mark price with simulated slippage and taker fee."""
    adapter = PaperExchangeAdapter(
        initial_wallet_balance=10000.0,
        slippage_bps=10.0,  # 0.1% slippage
        taker_fee_rate=0.0005,  # 0.05%
    )
    adapter._mark_prices["BTCUSDT"] = 60000.0

    # BUY 0.1 BTC Market Order
    order_req = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.1,
    )

    order = await adapter.place_order(order_req)

    assert order.status == OrderStatus.FILLED
    # Expected fill price: 60000 * (1 + 0.001) = 60060.0
    assert order.avg_fill_price == pytest.approx(60060.0, abs=0.01)
    assert order.filled_qty == 0.1
    # Expected taker fee: 0.1 * 60060 * 0.0005 = $3.003
    assert order.fee == pytest.approx(3.003, abs=0.001)

    # Balance: 10000 - 3.003 = 9996.997
    balances = await adapter.get_balance()
    assert balances[0].wallet_balance == pytest.approx(9996.997, abs=0.001)

    # Position: +0.1 BTC @ 60060.0
    positions = await adapter.get_positions()
    assert len(positions) == 1
    assert positions[0].size == 0.1
    assert positions[0].entry_price == pytest.approx(60060.0, abs=0.01)


@pytest.mark.asyncio
async def test_paper_adapter_limit_order_does_not_fill_until_price_crosses():
    """Verify limit orders rest on simulated order book and fill ONLY when market price crosses limit."""
    adapter = PaperExchangeAdapter(
        initial_wallet_balance=10000.0,
        maker_fee_rate=0.0002,  # 0.02%
    )
    adapter._mark_prices["BTCUSDT"] = 60000.0

    # BUY Limit at $58,000 (below market $60,000)
    order_req = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=58000.0,
        quantity=0.2,
        time_in_force=TimeInForce.GTC,
    )

    order = await adapter.place_order(order_req)

    # 1. Order is NOT filled immediately — rests in open orders
    assert order.status == OrderStatus.ACKNOWLEDGED
    assert order.filled_qty == 0.0
    open_orders = await adapter.get_open_orders("BTCUSDT")
    assert len(open_orders) == 1
    assert open_orders[0].client_order_id == order.client_order_id

    # 2. Market price drops to $59,000 (still above $58,000 limit) -> still no fill
    filled_orders = adapter.set_mark_price("BTCUSDT", 59000.0)
    assert len(filled_orders) == 0
    assert len(await adapter.get_open_orders("BTCUSDT")) == 1

    # 3. Market price drops to $57,500 (crosses limit price) -> FILLS!
    filled_orders = adapter.set_mark_price("BTCUSDT", 57500.0)
    assert len(filled_orders) == 1
    assert filled_orders[0].status == OrderStatus.FILLED
    assert filled_orders[0].avg_fill_price == 58000.0
    # Maker fee: 0.2 * 58000 * 0.0002 = $2.32
    assert filled_orders[0].fee == pytest.approx(2.32, abs=0.01)

    # Order removed from open orders
    assert len(await adapter.get_open_orders("BTCUSDT")) == 0

    # Position opened on simulator
    positions = await adapter.get_positions()
    assert len(positions) == 1
    assert positions[0].size == 0.2
    assert positions[0].entry_price == 58000.0


# ==============================================================================
# 2. Platform Startup Fail-Fast Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_platform_startup_fail_fast_invalid_config():
    """Verify platform startup fails fast and halts if config is missing or invalid."""
    platform = PlatformEngine(config=None)
    platform.config = None  # Force invalid

    with pytest.raises(RuntimeError, match="Platform startup aborted"):
        await platform.startup()

    assert platform.is_running is False


@pytest.mark.asyncio
async def test_platform_startup_fail_fast_clock_drift():
    """Verify platform startup fails fast if NTP clock drift exceeds threshold."""
    config = AppConfig()
    mock_clock = AsyncMock(spec=ClockSync)
    # Clock sync check fails
    mock_clock.check_sync.return_value = False

    platform = PlatformEngine(config=config, clock_sync=mock_clock)

    with pytest.raises(RuntimeError, match="Clock drift check failed"):
        await platform.startup()

    assert platform.is_running is False


# ==============================================================================
# 3. Kill-Switch State Persistence Across Restarts
# ==============================================================================


@pytest.mark.asyncio
async def test_kill_switch_persists_across_platform_restart(async_test_engine):
    """Verify that if the kill switch was active before shutdown, it is restored and active upon restart."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    config = AppConfig()

    # 1. Simulate an unresolved critical reconciliation event in DB
    async with session_factory() as session:
        reconciliation_breach = ReconciliationEvent(
            event_type=ReconciliationEventType.GHOST_ORDER,
            symbol="BTCUSDT",
            local_state={"status": "ABSENT"},
            remote_state={"status": "ACKNOWLEDGED"},
            discrepancy_details="Ghost order detected on exchange",
            action_taken="CRITICAL_HALT",
            is_resolved=False,  # Unresolved!
        )
        session.add(reconciliation_breach)
        await session.commit()

    # 2. Start a fresh PlatformEngine
    adapter = PaperExchangeAdapter()
    platform = PlatformEngine(
        config=config,
        event_bus=event_bus,
        session_factory=session_factory,
        exchange_adapter=adapter,
    )

    await platform.startup()
    assert platform.is_running is True

    # 3. Verify kill switch was restored to ACTIVE from DB
    assert platform.risk_engine.is_kill_switch_active is True
    assert "Restored active halt" in platform.risk_engine._kill_switch_reason

    # 4. Prove incoming strategy signal is REJECTED
    signal = SignalEvent(
        strategy_id="strat_test",
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.05,
        mark_price=60000.0,
        timestamp=datetime.now(UTC),
    )
    decision = await platform.risk_engine.evaluate_signal(signal)
    assert decision.decision_type == RiskDecisionType.REJECTED
    assert "Kill-Switch is active" in decision.reason

    await platform.shutdown()


# ==============================================================================
# 4. Graceful Shutdown & In-Flight Order Handling Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_graceful_shutdown_cleans_in_flight_orders_and_persists_snapshot(
    async_test_engine,
):
    """Verify graceful shutdown cancels open simulator orders and captures final portfolio snapshot."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    config = AppConfig()
    adapter = PaperExchangeAdapter()

    platform = PlatformEngine(
        config=config,
        event_bus=event_bus,
        session_factory=session_factory,
        exchange_adapter=adapter,
    )
    await platform.startup()

    # Place an open limit order (rests in-flight)
    await adapter.place_order(
        OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=50000.0,
            quantity=0.1,
            client_order_id="c_shutdown_test_1",
            strategy_id="paper_strat",
        )
    )
    assert len(await adapter.get_open_orders()) == 1

    # Initiate graceful shutdown
    await platform.shutdown()

    assert platform.is_running is False
    # All open orders must be cleanly cancelled
    assert len(await adapter.get_open_orders()) == 0

    # Final snapshot must be recorded in DB
    async with session_factory() as session:
        snapshots = (await session.execute(select(PortfolioSnapshot))).scalars().all()
        assert len(snapshots) >= 1


@pytest.mark.asyncio
async def test_graceful_shutdown_during_in_flight_submitted_limit_order(
    async_test_engine,
):
    """Verify shutdown behavior when an order is genuinely mid-flight (SUBMITTED but not yet ACKNOWLEDGED).

    Proves shutdown drains the active network request, receives ACKNOWLEDGED,
    cancels the resting order cleanly on the exchange, and records a consistent final snapshot.
    """
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    config = AppConfig()

    class DelayedPaperAdapter(PaperExchangeAdapter):
        """Simulator with artificial network latency on place_order."""

        async def place_order(self, request: OrderRequest):
            # Simulate genuine mid-flight network transmission delay
            await asyncio.sleep(0.15)
            return await super().place_order(request)

    adapter = DelayedPaperAdapter(initial_wallet_balance=10000.0)
    adapter._mark_prices["BTCUSDT"] = 60000.0

    im = InstrumentManager(config=config, adapter=adapter)
    pm = PortfolioManager(event_bus=event_bus, session_factory=session_factory)
    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=adapter,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )
    platform = PlatformEngine(
        config=config,
        event_bus=event_bus,
        session_factory=session_factory,
        exchange_adapter=adapter,
        instrument_manager=im,
        portfolio_manager=pm,
        oms=oms,
    )
    await platform.startup()

    # Create approved decision for a BUY Limit at $50,000 (below market $60,000)
    decision = RiskDecisionEvent(
        signal_id="sig_mid_flight_1",
        strategy_id="strat_mid_flight",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Approved for mid-flight test",
        snapshot_data={"mark_price": 60000.0},
    )

    # 1. Fire order submission asynchronously (takes 0.15s)
    submission_task = asyncio.create_task(
        oms.process_approved_decision(
            decision=decision,
            order_type=OrderType.LIMIT,
            price=50000.0,
        )
    )

    # 2. Wait so OMS generates order, transitions to CREATED/SUBMITTED, and begins place_order
    await asyncio.sleep(0.04)
    assert len(oms._orders) == 1
    mid_flight_order = list(oms._orders.values())[0]
    assert mid_flight_order.status in (OrderStatus.CREATED, OrderStatus.SUBMITTED)

    # 3. Trigger SIGTERM / shutdown EXACTLY while the order is mid-flight
    shutdown_task = asyncio.create_task(platform.shutdown())

    # Wait for both tasks to resolve cleanly
    await asyncio.gather(submission_task, shutdown_task)

    # 4. Verify post-shutdown state:
    assert platform.is_running is False
    # The order was drained to ACKNOWLEDGED, then cancelled cleanly during shutdown
    assert mid_flight_order.status == OrderStatus.CANCELLED
    # Exchange open orders is empty
    assert len(await adapter.get_open_orders()) == 0
    # In-flight guard is clear
    assert len(oms._in_flight_orders) == 0

    # Final consistent portfolio snapshot recorded
    async with session_factory() as session:
        snapshots = (await session.execute(select(PortfolioSnapshot))).scalars().all()
        assert len(snapshots) >= 1


@pytest.mark.asyncio
async def test_graceful_shutdown_during_in_flight_submitted_market_order(
    async_test_engine,
):
    """Verify shutdown behavior when a market order is genuinely mid-flight.

    Proves shutdown drains the active request, executes the fill, updates PortfolioManager
    position and fees, and persists the resulting state in the final snapshot.
    """
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    config = AppConfig()

    class DelayedPaperAdapter(PaperExchangeAdapter):
        async def place_order(self, request: OrderRequest):
            await asyncio.sleep(0.15)
            return await super().place_order(request)

    adapter = DelayedPaperAdapter(
        initial_wallet_balance=10000.0,
        slippage_bps=0.0,
    )
    adapter._mark_prices["BTCUSDT"] = 60000.0

    im = InstrumentManager(config=config, adapter=adapter)
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=adapter,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )
    platform = PlatformEngine(
        config=config,
        event_bus=event_bus,
        session_factory=session_factory,
        exchange_adapter=adapter,
        instrument_manager=im,
        portfolio_manager=pm,
        oms=oms,
    )
    await platform.startup()

    decision = RiskDecisionEvent(
        signal_id="sig_market_mid_flight_1",
        strategy_id="strat_market_mid_flight",
        symbol="BTCUSDT",
        decision_type=RiskDecisionType.APPROVED,
        original_target_exposure=0.10,
        approved_target_exposure=0.10,
        reason="Approved for market mid-flight test",
        snapshot_data={"mark_price": 60000.0},
    )

    # 1. Fire market order submission
    submission_task = asyncio.create_task(
        oms.process_approved_decision(
            decision=decision,
            order_type=OrderType.MARKET,
        )
    )

    # 2. Verify it is mid-flight in active pre-submission/submitted state
    await asyncio.sleep(0.02)
    assert len(oms._orders) == 1
    mid_flight_order = list(oms._orders.values())[0]
    assert mid_flight_order.status in (OrderStatus.CREATED, OrderStatus.SUBMITTED)

    # 3. Concurrently trigger graceful shutdown
    await platform.shutdown()
    await submission_task

    # 4. Verify post-shutdown state:
    assert platform.is_running is False
    # Market order drained and filled cleanly
    assert mid_flight_order.status == OrderStatus.FILLED
    assert mid_flight_order.filled_qty == pytest.approx(0.016, abs=0.002)

    # Portfolio Manager updated with position and fees
    pos = pm.get_position("strat_market_mid_flight", "BTCUSDT")
    assert pos.is_open is True
    assert pos.size == pytest.approx(0.016, abs=0.002)
    assert pos.entry_price == 60000.0

    port = pm.get_portfolio("strat_market_mid_flight")
    # Expected fee: 0.016 * 60000 * 0.0005 = $0.48
    expected_wallet = 10000.0 - (0.016 * 60000.0 * 0.0005)
    assert port.wallet_balance == pytest.approx(expected_wallet, abs=0.01)

    # Final snapshot contains the newly opened position and updated balance
    async with session_factory() as session:
        snapshots = (await session.execute(select(PortfolioSnapshot))).scalars().all()
        assert len(snapshots) >= 1
        assert snapshots[-1].total_wallet_balance == pytest.approx(expected_wallet, abs=0.01)


# ==============================================================================
# 5. Full End-to-End Pipeline Integration Test
# ==============================================================================


@pytest.mark.asyncio
async def test_full_pipeline_end_to_end_paper_trading(async_test_engine):
    """Full End-to-End Integration Test: Injects candle sequence -> SMA Strategy -> Risk Engine -> OMS -> Paper fills -> Portfolio state -> State Reconciliation."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    config = AppConfig()
    config.market_data.active_symbols = ["BTCUSDT"]

    # 1. Paper Exchange Adapter
    adapter = PaperExchangeAdapter(
        initial_wallet_balance=10000.0,
        slippage_bps=0.0,
        taker_fee_rate=0.0005,
    )
    adapter._mark_prices["BTCUSDT"] = 60000.0

    # 2. Instruments
    im = InstrumentManager(config=config, adapter=adapter)

    # 3. Portfolio Manager
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )

    # 4. Risk Engine
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )
    risk_engine.record_market_data_tick("BTCUSDT")

    # 5. OMS
    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=adapter,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )

    # 6. Strategy Runner & SMA Strategy (fast=3, slow=5)
    signal_manager = SignalManager(
        event_bus=event_bus,
        session_factory=session_factory,
    )
    strategy_runner = StrategyRunner(
        event_bus=event_bus,
        signal_manager=signal_manager,
    )
    sma_strategy = SMAMomentumStrategy(
        strategy_id="sma_paper_strat",
        symbols=["BTCUSDT"],
        fast_period=3,
        slow_period=5,
        target_exposure_pct=0.10,  # 10% equity
    )
    strategy_runner.register_strategy(sma_strategy)

    # 7. State Reconciliation Engine
    reconciliation_engine = ReconciliationEngine(
        event_bus=event_bus,
        exchange_adapter=adapter,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        oms=oms,
        session_factory=session_factory,
    )

    # 8. Platform Coordinator
    platform = PlatformEngine(
        config=config,
        event_bus=event_bus,
        session_factory=session_factory,
        exchange_adapter=adapter,
        instrument_manager=im,
        portfolio_manager=pm,
        strategy_runner=strategy_runner,
        risk_engine=risk_engine,
        oms=oms,
        reconciliation_engine=reconciliation_engine,
    )

    # Run 10-step startup
    await platform.startup()
    assert platform.is_running is True

    # 9. Feed sequence of 7 candles to trigger Bullish SMA Golden Cross
    prices = [60000.0, 59800.0, 59600.0, 59400.0, 59200.0, 60000.0, 61500.0]
    for _i, p in enumerate(prices):
        adapter.set_mark_price("BTCUSDT", p)
        risk_engine.record_market_data_tick("BTCUSDT")
        candle_event = CandleEvent(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=datetime.fromtimestamp(1700000000 + _i * 60, tz=UTC),
            close_time=datetime.fromtimestamp(1700000000 + _i * 60 + 59, tz=UTC),
            open_price=p - 50.0,
            high_price=p + 50.0,
            low_price=p - 50.0,
            close_price=p,
            volume=10.0,
            quote_volume=10.0 * p,
            trades_count=100,
            is_closed=True,
        )
        await event_bus.publish(candle_event)

    # 10. Verify Full Pipeline Execution with Exact Mathematical Assertions:
    # SMA Strategy produced SignalEvent -> RiskEngine APPROVED -> OMS submitted to PaperAdapter -> Filled

    # Position Assertions:
    pos = pm.get_position("sma_paper_strat", "BTCUSDT")
    assert pos.is_open is True
    # 10% target exposure on $10,000 equity = $1,000 notional / $61,500 mark price = 0.01626 -> rounded to 0.016 BTC
    assert pos.size == 0.016
    assert pos.entry_price == 61500.0
    assert pos.notional == pytest.approx(0.016 * 61500.0, abs=0.01)  # $984.00
    assert pos.unrealized_pnl == pytest.approx(0.0, abs=0.01)

    # Portfolio Assertions:
    port = pm.get_portfolio("sma_paper_strat")
    # Expected fee: 0.016 * 61500 * 0.0005 (taker fee) = $0.492
    expected_fee = 0.016 * 61500.0 * 0.0005
    expected_wallet_balance = 10000.0 - expected_fee  # $9,999.508
    assert port.wallet_balance == pytest.approx(expected_wallet_balance, abs=0.001)
    assert port.equity == pytest.approx(expected_wallet_balance, abs=0.001)
    # Available margin = equity - initial_margin ($984.00) = $9,015.508
    expected_available = expected_wallet_balance - 984.0
    assert port.available_balance == pytest.approx(expected_available, abs=0.001)

    # 11. Run State Reconciliation against PaperExchangeAdapter
    discrepancies = await reconciliation_engine.reconcile_now()
    # Confirms local state and paper exchange state match 100%
    assert len(discrepancies) == 0

    # Clean shutdown
    await platform.shutdown()
    assert platform.is_running is False
