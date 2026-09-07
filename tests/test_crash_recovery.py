"""Real Cross-Process OS SIGKILL Crash Recovery & State Hydration Test Suite.

Spawns the trading platform in a genuine OS subprocess, initiates open positions and
in-flight resting orders, abruptly executes os.kill(pid, signal.SIGKILL) from outside
the process, and proves that post-restart database state hydration and exchange
reconciliation restore 100% accurate state with zero discrepancies.
"""

import multiprocessing
import os
import signal
import time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_platform.core.config import AppConfig, DatabaseConfig
from trading_platform.core.constants import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
    TradingMode,
)
from trading_platform.core.events import EventBus, FillEvent
from trading_platform.db.session import get_async_engine, get_db_session, get_session_factory
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.models.instrument import Instrument
from trading_platform.models.order import Order
from trading_platform.models.portfolio import AccountBalance
from trading_platform.models.position import Position
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.reconciliation.engine import ReconciliationEngine
from trading_platform.risk.engine import RiskEngine


def _subprocess_worker(db_path: str, strat_id: str, symbol: str, ready_event: multiprocessing.Event) -> None:
    """Worker function executed in a dedicated OS subprocess."""
    import asyncio

    async def _run() -> None:
        db_cfg = DatabaseConfig(sqlite_path=db_path)
        engine = get_async_engine(db_cfg)

        from trading_platform.db.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = get_session_factory(engine)
        event_bus = EventBus()

        pm = PortfolioManager(event_bus, session_factory=session_factory, default_deposit_usd=10000.0)

        # 1. Establish an open position of 0.50 BTC @ $70,000 via execution fill
        await pm.apply_fill(
            FillEvent(
                client_order_id="FILL_PRE_CRASH_SUBPROC_001",
                exchange_trade_id="TR_OS_9991",
                strategy_id=strat_id,
                symbol=symbol,
                side=OrderSide.BUY,
                price=70000.0,
                quantity=0.50,
                fee=14.0,
            )
        )

        # 2. Insert an in-flight resting order in the database (SUBMITTED)
        async with get_db_session(session_factory) as session:
            in_flight_order = Order(
                client_order_id="ORD_INFLIGHT_OS_SIGKILL_001",
                exchange_order_id="EX_OS_998877",
                strategy_id=strat_id,
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                time_in_force=TimeInForce.GTC,
                quantity=0.20,
                price=71000.0,
                status=OrderStatus.SUBMITTED,
                filled_qty=0.0,
                avg_fill_price=0.0,
                cum_quote=0.0,
                fee=0.0,
                fee_asset="USDT",
            )
            session.add(in_flight_order)
            await session.commit()

        # Signal parent process that database writes are flushed and worker is active
        ready_event.set()

        # Keep running in an infinite event loop so it can only be terminated by external OS SIGKILL
        while True:
            await asyncio.sleep(0.1)

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_genuine_os_sigkill_crash_recovery(tmp_path):
    """GENUINE OS-LEVEL SIGKILL RECOVERY TEST:

    1. Spawns trading process in separate OS process (multiprocessing.Process).
    2. Issues genuine external OS signal: os.kill(worker.pid, signal.SIGKILL).
    3. Confirms the subprocess died with SIGKILL (-9).
    4. Starts a new platform process, restores state from DB, runs reconciliation against exchange,
       and verifies 100% synchronization.
    """
    strat_id = "sigkill_crash_strat_v1"
    symbol = "BTCUSDT"
    db_file = str(tmp_path / "os_sigkill_test.db")

    ready_event = multiprocessing.Event()

    # 1. Spawn real OS subprocess
    worker = multiprocessing.Process(
        target=_subprocess_worker,
        args=(db_file, strat_id, symbol, ready_event),
    )
    worker.start()
    worker_pid = worker.pid
    print(f"\n[CRASH TEST] Spawned OS Subprocess PID: {worker_pid}")

    # Wait for child process to establish state and signal ready
    assert ready_event.wait(timeout=5.0), "Worker subprocess failed to initialize within 5 seconds"
    time.sleep(0.1)  # Ensure event loop in child is active

    # 2. Issue genuine external OS SIGKILL (Signal 9)
    print(f"[CRASH TEST] Sending external OS SIGKILL (signal 9) to PID {worker_pid}...")
    os.kill(worker_pid, signal.SIGKILL)

    # Join child process and confirm exit status
    worker.join(timeout=2.0)
    assert not worker.is_alive(), f"Subprocess {worker_pid} still alive after SIGKILL!"
    print(f"[CRASH TEST] Confirmed Subprocess PID {worker_pid} terminated with exitcode: {worker.exitcode}")
    # On Unix, exitcode for SIGKILL is -9
    assert worker.exitcode == -signal.SIGKILL or worker.exitcode == -9

    # =========================================================================
    # 3. Post-Crash Startup & State Hydration in New Process
    # =========================================================================
    config = AppConfig(
        environment=TradingMode.PAPER,
        strategy_id=strat_id,
        live_trading_enabled=False,
    )
    config.database.sqlite_path = db_file
    db_cfg = DatabaseConfig(sqlite_path=db_file)
    engine = get_async_engine(db_cfg)
    session_factory = get_session_factory(engine)

    # Exchange ground-truth on restart:
    # 1. Balance on exchange: $9,986.00 ($10,000 deposit - $14 fee)
    # 2. Position on exchange: 0.50 BTC @ $70,000
    # 3. Open resting order on exchange: ORD_INFLIGHT_OS_SIGKILL_001
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_markets.return_value = [
        Instrument(
            symbol=symbol,
            base_asset="BTC",
            quote_asset="USDT",
            tick_size=Decimal("0.10"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5.0"),
            is_active=True,
        )
    ]
    mock_adapter.get_balance.return_value = [
        AccountBalance(
            strategy_id=strat_id,
            asset="USDT",
            wallet_balance=9986.0,
            available_balance=9500.0,
            locked_balance=486.0,
        )
    ]
    mock_adapter.get_positions.return_value = [
        Position(
            strategy_id=strat_id,
            symbol=symbol,
            size=0.50,
            entry_price=70000.0,
            mark_price=72000.0,
            unrealized_pnl=1000.0,
            leverage=5.0,
            liquidation_price=56000.0,
        )
    ]
    mock_adapter.get_open_orders.return_value = [
        Order(
            client_order_id="ORD_INFLIGHT_OS_SIGKILL_001",
            exchange_order_id="EX_OS_998877",
            strategy_id=strat_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            quantity=0.20,
            price=71000.0,
            status=OrderStatus.SUBMITTED,
            filled_qty=0.0,
            avg_fill_price=0.0,
            cum_quote=0.0,
            fee=0.0,
            fee_asset="USDT",
        )
    ]

    event_bus = EventBus()
    im = InstrumentManager(config, mock_adapter)
    await im.initialize()

    pm = PortfolioManager(event_bus, session_factory=session_factory, default_deposit_usd=10000.0)
    risk = RiskEngine(
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
    recon = ReconciliationEngine(
        event_bus=event_bus,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm,
        risk_engine=risk,
        oms=oms,
        session_factory=session_factory,
    )

    # 4. Startup Hydration Step 5: Hydrate Portfolio and Positions from DB
    await pm.restore_from_db()
    restored_pos = pm.get_position(strat_id, symbol)
    restored_port = pm.get_portfolio(strat_id)
    print(f"[RECOVERY STEP 5] Restored Portfolio Wallet Balance: ${restored_port.wallet_balance:,.2f}")
    print(f"[RECOVERY STEP 5] Restored Position: {restored_pos.size} {symbol} @ ${restored_pos.entry_price:,.2f}")
    assert restored_pos.size == 0.50
    assert restored_port.wallet_balance == 9986.0

    # 5. Startup Hydration Step 9: Hydrate Open In-Flight Orders into OMS
    await oms.restore_from_db()
    assert "ORD_INFLIGHT_OS_SIGKILL_001" in oms._orders
    in_flight_order = oms._orders["ORD_INFLIGHT_OS_SIGKILL_001"]
    print(f"[RECOVERY STEP 9] Restored In-Flight Resting Order: {in_flight_order.client_order_id} (Status: {in_flight_order.status})")

    # 6. Startup Step 10: Run Post-Restart Reconciliation Audit
    discrepancies = await recon.reconcile_now()
    print(f"[RECOVERY STEP 10] Reconciliation Discrepancies Count: {len(discrepancies)}")
    for d in discrepancies:
        print(f"   -> Discrepancy: {d}")

    assert len(discrepancies) == 0, f"Unexpected reconciliation discrepancies: {discrepancies}"
    assert not risk.is_kill_switch_active
    print("[CRASH TEST] ✅ Genuine OS SIGKILL crash recovery verified with 0 discrepancies and 0 ghost orders.")
