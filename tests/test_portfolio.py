"""Tests for Phase 5: Portfolio State Machine, PnL Decomposition, Restart Recovery, and Multi-Strategy Isolation."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import OrderSide
from trading_platform.core.events import EventBus, FillEvent, MarketDataEvent
from trading_platform.models.portfolio import PortfolioSnapshot
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.portfolio.state import PositionState


@pytest.mark.asyncio
async def test_pnl_decomposition_unrealized_realized_funding(async_test_engine):
    """Verify independent and correct calculation of unrealized PnL, realized PnL, and funding PnL."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    strat_id = "alpha_strat"

    # Initial state
    port = pm.get_portfolio(strat_id)
    assert port.wallet_balance == 10000.0
    assert port.unrealized_pnl == 0.0
    assert port.realized_pnl == 0.0
    assert port.funding_pnl == 0.0
    assert port.equity == 10000.0

    # 1. Fill Event: BUY 1.0 BTC at $60,000 (Fee: $30.0)
    fill_buy = FillEvent(
        client_order_id="ord_1",
        exchange_trade_id="trade_1",
        strategy_id=strat_id,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=60000.0,
        quantity=1.0,
        fee=30.0,
    )
    await pm.apply_fill(fill_buy)

    pos = pm.get_position(strat_id, "BTCUSDT")
    assert pos.size == 1.0
    assert pos.entry_price == 60000.0
    assert pm.get_portfolio(strat_id).wallet_balance == 9970.0  # 10000 - 30 fee
    assert pm.get_portfolio(strat_id).realized_pnl == -30.0

    # 2. Market Data Event: Mark price moves to $62,000
    md_event = MarketDataEvent(
        symbol="BTCUSDT",
        mark_price=62000.0,
    )
    await event_bus.publish(md_event)

    port = pm.get_portfolio(strat_id)
    pos = pm.get_position(strat_id, "BTCUSDT")
    # Unrealized PnL = 1.0 * (62000 - 60000) = +$2,000
    assert pos.unrealized_pnl == 2000.0
    assert port.unrealized_pnl == 2000.0
    assert port.equity == 9970.0 + 2000.0  # 11,970.0

    # 3. Funding Payment: rate +0.0001 (0.01%) on 1.0 BTC at $62,000 -> payment = -6.20
    funding_payment = await pm.apply_funding_payment(
        strategy_id=strat_id,
        symbol="BTCUSDT",
        funding_rate=0.0001,
        mark_price=62000.0,
    )
    assert funding_payment == -6.20
    port = pm.get_portfolio(strat_id)
    assert port.funding_pnl == -6.20
    assert port.wallet_balance == 9970.0 - 6.20  # 9,963.80
    assert port.equity == 9963.80 + 2000.0  # 11,963.80

    # 4. Fill Event: SELL 1.0 BTC at $63,000 (Fee: $31.50) -> Close position
    fill_sell = FillEvent(
        client_order_id="ord_2",
        exchange_trade_id="trade_2",
        strategy_id=strat_id,
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        price=63000.0,
        quantity=1.0,
        fee=31.50,
    )
    await pm.apply_fill(fill_sell)

    pos = pm.get_position(strat_id, "BTCUSDT")
    assert pos.is_open is False
    assert pos.size == 0.0
    assert pos.unrealized_pnl == 0.0

    # Gross PnL on close = 1.0 * (63000 - 60000) = $3,000. Net after $31.50 fee = $2,968.50
    # Total Realized PnL = -30.0 + 2968.50 = $2,938.50
    port = pm.get_portfolio(strat_id)
    assert port.realized_pnl == 2938.50
    assert port.funding_pnl == -6.20
    assert port.unrealized_pnl == 0.0
    # Total Wallet = 9963.80 + 2968.50 = 12,932.30
    assert port.wallet_balance == 12932.30
    assert port.equity == 12932.30


@pytest.mark.asyncio
async def test_restart_recovery_from_database(async_test_engine):
    """Verify that simulating a process crash & restart fully restores all positions, balances, and PnLs."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus1 = EventBus()
    pm1 = PortfolioManager(
        event_bus=event_bus1,
        session_factory=session_factory,
        default_deposit_usd=50000.0,
    )

    # Execute trades on pm1
    await pm1.apply_fill(
        FillEvent(
            client_order_id="ord_a",
            exchange_trade_id="tr_a",
            strategy_id="strat_restart",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=60000.0,
            quantity=0.5,
            fee=15.0,
        )
    )
    await pm1.apply_fill(
        FillEvent(
            client_order_id="ord_b",
            exchange_trade_id="tr_b",
            strategy_id="strat_restart",
            symbol="ETHUSDT",
            side=OrderSide.BUY,
            price=3000.0,
            quantity=4.0,
            fee=6.0,
        )
    )
    await pm1.apply_funding_payment(
        strategy_id="strat_restart",
        symbol="BTCUSDT",
        funding_rate=0.0002,
        mark_price=60000.0,
    )

    state_before_crash = pm1.get_portfolio("strat_restart")
    pos_btc_before = pm1.get_position("strat_restart", "BTCUSDT")
    pos_eth_before = pm1.get_position("strat_restart", "ETHUSDT")

    # SIMULATE CRASH & RESTART: create completely new PortfolioManager with empty in-memory state
    del pm1
    event_bus2 = EventBus()
    pm2 = PortfolioManager(
        event_bus=event_bus2,
        session_factory=session_factory,
        default_deposit_usd=0.0,  # Ensure no default overwrites DB
    )

    # Restore from PostgreSQL
    await pm2.restore_from_db()

    state_after_recovery = pm2.get_portfolio("strat_restart")
    pos_btc_after = pm2.get_position("strat_restart", "BTCUSDT")
    pos_eth_after = pm2.get_position("strat_restart", "ETHUSDT")

    # Assert exact match
    assert pos_btc_after.size == pos_btc_before.size == 0.5
    assert pos_btc_after.entry_price == pos_btc_before.entry_price == 60000.0
    assert pos_eth_after.size == pos_eth_before.size == 4.0
    assert pos_eth_after.entry_price == pos_eth_before.entry_price == 3000.0
    assert state_after_recovery.wallet_balance == state_before_crash.wallet_balance
    assert state_after_recovery.realized_pnl == state_before_crash.realized_pnl
    assert state_after_recovery.funding_pnl == state_before_crash.funding_pnl


@pytest.mark.asyncio
async def test_multi_strategy_isolation(async_test_engine):
    """Verify that multiple strategies trading the same symbol maintain strictly isolated positions and PnL."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=20000.0,
    )

    strat_long = "momentum_long"
    strat_short = "mean_reversion_short"

    # Strategy 1 goes LONG 1.0 BTC at $60,000
    await pm.apply_fill(
        FillEvent(
            client_order_id="ord_l",
            exchange_trade_id="tr_l",
            strategy_id=strat_long,
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=60000.0,
            quantity=1.0,
            fee=0.0,
        )
    )

    # Strategy 2 goes SHORT 1.0 BTC at $60,000
    await pm.apply_fill(
        FillEvent(
            client_order_id="ord_s",
            exchange_trade_id="tr_s",
            strategy_id=strat_short,
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            price=60000.0,
            quantity=1.0,
            fee=0.0,
        )
    )

    # Mark price rises to $65,000
    await event_bus.publish(MarketDataEvent(symbol="BTCUSDT", mark_price=65000.0))

    pos_l = pm.get_position(strat_long, "BTCUSDT")
    pos_s = pm.get_position(strat_short, "BTCUSDT")

    # Long gains $5,000; Short loses $5,000
    assert pos_l.size == 1.0
    assert pos_l.unrealized_pnl == 5000.0
    assert pm.get_portfolio(strat_long).equity == 25000.0

    assert pos_s.size == -1.0
    assert pos_s.unrealized_pnl == -5000.0
    assert pm.get_portfolio(strat_short).equity == 15000.0


@pytest.mark.asyncio
async def test_portfolio_snapshot_creation(async_test_engine):
    """Verify point-in-time PortfolioSnapshot creation and database persistence."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    strat_id = "snap_strat"

    # Fill
    await pm.apply_fill(
        FillEvent(
            client_order_id="ord_snap",
            exchange_trade_id="tr_snap",
            strategy_id=strat_id,
            symbol="SOLUSDT",
            side=OrderSide.BUY,
            price=150.0,
            quantity=10.0,
            fee=0.75,
        )
    )
    await event_bus.publish(MarketDataEvent(symbol="SOLUSDT", mark_price=160.0))

    # Create Snapshot
    now = datetime(2026, 8, 30, 18, 0, 0, tzinfo=UTC)
    snap = await pm.create_snapshot(strat_id, timestamp=now)

    assert snap.strategy_id == strat_id
    assert snap.total_unrealized_pnl == 100.0  # 10 * (160 - 150)
    assert snap.total_exposure == 1600.0  # 10 * 160
    assert snap.total_wallet_balance == 9999.25  # 10000 - 0.75

    # Verify queryable from PostgreSQL
    async with session_factory() as session:
        res = await session.execute(
            select(PortfolioSnapshot).where(PortfolioSnapshot.strategy_id == strat_id)
        )
        saved = res.scalar_one()
        assert saved.total_unrealized_pnl == 100.0
        assert saved.effective_leverage > 0.0


@pytest.mark.asyncio
async def test_peak_equity_and_drawdown_calculation(async_test_engine):
    """Verify peak equity tracking and percentage drawdown calculations."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    strat_id = "dd_strat"

    await pm.apply_fill(
        FillEvent(
            client_order_id="ord_dd",
            exchange_trade_id="tr_dd",
            strategy_id=strat_id,
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=60000.0,
            quantity=1.0,
            fee=0.0,
        )
    )

    # 1. Price rises to $70,000 -> Equity = $20,000 (New Peak)
    await event_bus.publish(MarketDataEvent(symbol="BTCUSDT", mark_price=70000.0))
    port = pm.get_portfolio(strat_id)
    assert port.equity == 20000.0
    assert port.peak_equity == 20000.0
    assert port.drawdown_pct == 0.0

    # 2. Price falls to $65,000 -> Equity = $15,000 (Drawdown = (20000 - 15000) / 20000 = 25%)
    await event_bus.publish(MarketDataEvent(symbol="BTCUSDT", mark_price=65000.0))
    port = pm.get_portfolio(strat_id)
    assert port.equity == 15000.0
    assert port.peak_equity == 20000.0
    assert port.drawdown_pct == 25.0


@pytest.mark.asyncio
async def test_isolated_margin_two_positions_loss_independence(async_test_engine):
    """Verify that under isolated margin, an unrealized loss on one position does NOT affect another position's available margin or the unallocated wallet balance."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=20000.0,
    )
    strat_id = "iso_strat"

    # Open Position 1: BUY 1.0 BTC at $60,000, 10x leverage (Initial Margin = $6,000)
    await pm.apply_fill(
        FillEvent(
            client_order_id="ord_btc",
            exchange_trade_id="tr_btc",
            strategy_id=strat_id,
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=60000.0,
            quantity=1.0,
            fee=0.0,
        )
    )
    pos_btc = pm.get_position(strat_id, "BTCUSDT")
    pos_btc.leverage = 10.0

    # Open Position 2: BUY 10.0 ETH at $3,000, 10x leverage (Initial Margin = $3,000)
    await pm.apply_fill(
        FillEvent(
            client_order_id="ord_eth",
            exchange_trade_id="tr_eth",
            strategy_id=strat_id,
            symbol="ETHUSDT",
            side=OrderSide.BUY,
            price=3000.0,
            quantity=10.0,
            fee=0.0,
        )
    )
    pos_eth = pm.get_position(strat_id, "ETHUSDT")
    pos_eth.leverage = 10.0

    port_init = pm.get_portfolio(strat_id)
    assert pos_btc.initial_margin == 6000.0
    assert pos_eth.initial_margin == 3000.0
    assert port_init.total_initial_margin == 9000.0
    # Available unallocated wallet balance = 20,000 - 9,000 = 11,000
    assert port_init.available_balance == 11000.0

    # BTC price crashes from $60,000 to $55,000 (-$5,000 uPnL on BTC)
    await event_bus.publish(MarketDataEvent(symbol="BTCUSDT", mark_price=55000.0))
    await event_bus.publish(MarketDataEvent(symbol="ETHUSDT", mark_price=3000.0))

    # BTC isolated margin buffer drops: $6,000 initial + (-$5,000 uPnL) = $1,000 remaining
    assert pos_btc.unrealized_pnl == -5000.0
    assert pos_btc.isolated_margin_balance == 1000.0

    # ETH position margin is completely untouched
    assert pos_eth.unrealized_pnl == 0.0
    assert pos_eth.isolated_margin_balance == 3000.0
    assert pos_eth.available_margin > 0.0

    # Unallocated wallet balance available for new orders is NOT drained by BTC's isolated loss
    port_after_crash = pm.get_portfolio(strat_id)
    assert port_after_crash.available_balance == 11000.0


def test_position_liquidation_distance_calculation():
    """Verify liquidation price and standalone liquidation distance calculations for Long and Short."""
    # 1. Long 1.0 BTC at $60,000, 10x leverage, 0.5% MMR
    # Liq price = 60000 * (1 - 0.10 + 0.005) = 60000 * 0.905 = $54,300
    pos_long = PositionState(
        strategy_id="test",
        symbol="BTCUSDT",
        size=1.0,
        entry_price=60000.0,
        mark_price=60000.0,
        leverage=10.0,
        maintenance_margin_rate=0.005,
    )
    pos_long.recalculate_unrealized_pnl(60000.0)
    assert pos_long.calculated_liquidation_price == pytest.approx(54300.0)
    # Distance = (60000 - 54300) / 60000 = 9.5%
    assert pos_long.liquidation_distance_pct == pytest.approx(9.5, abs=0.01)

    # 2. Short 1.0 BTC at $60,000, 10x leverage, 0.5% MMR
    # Liq price = 60000 * (1 + 0.10 - 0.005) = 60000 * 1.095 = $65,700
    pos_short = PositionState(
        strategy_id="test",
        symbol="BTCUSDT",
        size=-1.0,
        entry_price=60000.0,
        mark_price=60000.0,
        leverage=10.0,
        maintenance_margin_rate=0.005,
    )
    pos_short.recalculate_unrealized_pnl(60000.0)
    assert pos_short.calculated_liquidation_price == pytest.approx(65700.0)
    # Distance = (65700 - 60000) / 60000 = 9.5%
    assert pos_short.liquidation_distance_pct == pytest.approx(9.5, abs=0.01)
