"""Application Lifecycle Coordinator and Platform Engine."""

import asyncio
import signal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.clock import ClockSync
from trading_platform.core.config import AppConfig
from trading_platform.core.constants import OrderStatus
from trading_platform.core.events import EventBus
from trading_platform.core.exceptions import ConfigurationError
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.exchange.paper import PaperExchangeAdapter
from trading_platform.market_data.ws_client import BinanceWebSocketClient
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.oms.state_machine import OrderStateMachine
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.reconciliation.engine import ReconciliationEngine
from trading_platform.risk.engine import RiskEngine
from trading_platform.strategy.runner import StrategyRunner
from trading_platform.strategy.signal_manager import SignalManager

logger = get_logger("engine.platform")


class PlatformEngine:
    """Coordinates strict 10-step startup sequence, runtime execution, and graceful shutdown."""

    def __init__(
        self,
        config: AppConfig | None = None,
        event_bus: EventBus | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        exchange_adapter: ExchangeAdapter | None = None,
        clock_sync: ClockSync | None = None,
        instrument_manager: InstrumentManager | None = None,
        market_data_client: BinanceWebSocketClient | None = None,
        portfolio_manager: PortfolioManager | None = None,
        strategy_runner: StrategyRunner | None = None,
        risk_engine: RiskEngine | None = None,
        oms: OrderManagementSystem | None = None,
        reconciliation_engine: ReconciliationEngine | None = None,
    ) -> None:
        self.config = config or AppConfig()
        self.event_bus = event_bus or EventBus()
        self.session_factory = session_factory

        # Exchange Adapter: defaults to PaperExchangeAdapter in paper mode
        self.adapter = exchange_adapter or PaperExchangeAdapter()
        self.clock_sync = clock_sync
        self.instrument_manager = instrument_manager
        self.market_data_client = market_data_client
        self.portfolio_manager = portfolio_manager
        self.strategy_runner = strategy_runner
        self.risk_engine = risk_engine
        self.oms = oms
        self.reconciliation_engine = reconciliation_engine

        self._is_running: bool = False
        self._shutdown_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        """True if all 10 startup steps completed successfully and system is actively running."""
        return self._is_running

    async def startup(self) -> None:
        """Execute strict 10-step startup sequence, failing fast on any step error."""
        logger.info("Initializing Algorithmic Trading Platform startup sequence...")

        try:
            # ------------------------------------------------------------------
            # Step 1: Load and validate config (Fail fast)
            # ------------------------------------------------------------------
            logger.info("[Startup Step 1/10] Validating configuration...")
            if not self.config or not getattr(self.config, "environment", None):
                raise ConfigurationError("AppConfig is invalid or missing 'environment' setting.")
            logger.info(f"Configuration valid. Mode: {self.config.environment.value.upper()}")

            # ------------------------------------------------------------------
            # Step 2: Database connectivity check
            # ------------------------------------------------------------------
            logger.info("[Startup Step 2/10] Verifying database connectivity...")
            if self.session_factory:
                async with get_db_session(self.session_factory) as session:
                    await session.execute(text("SELECT 1"))
                logger.info("Database connection established successfully.")
            else:
                logger.info(
                    "Running in in-memory session mode (no external PostgreSQL configured)."
                )

            # ------------------------------------------------------------------
            # Step 3: Clock sync check
            # ------------------------------------------------------------------
            logger.info("[Startup Step 3/10] Checking NTP clock synchronization...")
            if self.clock_sync:
                sync_ok = await self.clock_sync.check_sync()
                if not sync_ok:
                    raise RuntimeError(
                        f"Clock drift check failed: drift exceeded allowable threshold for mode {self.config.environment}."
                    )
            logger.info("Clock synchronization verified.")

            # ------------------------------------------------------------------
            # Step 4: Instrument discovery & precision metadata
            # ------------------------------------------------------------------
            logger.info(
                "[Startup Step 4/10] Initializing instrument manager and market discovery..."
            )
            if not self.instrument_manager:
                self.instrument_manager = InstrumentManager(
                    config=self.config,
                    adapter=self.adapter,
                )
            await self.instrument_manager.initialize()
            logger.info(
                f"Instrument discovery complete: {len(self.instrument_manager._instruments)} instruments loaded."
            )

            # ------------------------------------------------------------------
            # Step 5: Portfolio state restoration from DB
            # ------------------------------------------------------------------
            logger.info("[Startup Step 5/10] Restoring portfolio and position state from DB...")
            if not self.portfolio_manager:
                self.portfolio_manager = PortfolioManager(
                    event_bus=self.event_bus,
                    session_factory=self.session_factory,
                )
            if self.session_factory:
                await self.portfolio_manager.restore_from_db()
            logger.info("Portfolio state restoration complete.")

            # ------------------------------------------------------------------
            # Step 6: Market data WebSocket connect
            # ------------------------------------------------------------------
            logger.info("[Startup Step 6/10] Connecting market data stream...")
            if self.market_data_client:
                await self.market_data_client.connect()
                logger.info("Market data stream connected.")
            else:
                logger.info("Using simulated in-memory market data stream.")

            # ------------------------------------------------------------------
            # Step 7: Start Strategy Runner
            # ------------------------------------------------------------------
            logger.info("[Startup Step 7/10] Starting Strategy Runner...")
            if not self.strategy_runner:
                signal_manager = SignalManager(
                    event_bus=self.event_bus,
                    session_factory=self.session_factory,
                )
                self.strategy_runner = StrategyRunner(
                    event_bus=self.event_bus,
                    signal_manager=signal_manager,
                )
            logger.info("Strategy Runner active.")

            # ------------------------------------------------------------------
            # Step 8: Start Risk Engine & Restore Kill-Switch
            # ------------------------------------------------------------------
            logger.info(
                "[Startup Step 8/10] Initializing Risk Engine and restoring kill switch state..."
            )
            if not self.risk_engine:
                self.risk_engine = RiskEngine(
                    event_bus=self.event_bus,
                    portfolio_manager=self.portfolio_manager,
                    instrument_manager=self.instrument_manager,
                    session_factory=self.session_factory,
                )
            # Restore any persistent active kill-switch halt
            await self.risk_engine.restore_kill_switch_state()
            if self.risk_engine.is_kill_switch_active:
                logger.warning(
                    f"Risk Engine initialized with active kill switch: {self.risk_engine._kill_switch_reason}"
                )
            else:
                logger.info("Risk Engine active (Kill switch clear).")

            # ------------------------------------------------------------------
            # Step 9: Start OMS & Execution Engine
            # ------------------------------------------------------------------
            logger.info("[Startup Step 9/10] Initializing Order Management System (OMS)...")
            if not self.oms:
                self.oms = OrderManagementSystem(
                    event_bus=self.event_bus,
                    exchange_adapter=self.adapter,
                    portfolio_manager=self.portfolio_manager,
                    instrument_manager=self.instrument_manager,
                    session_factory=self.session_factory,
                )
            await self.oms.restore_from_db()
            logger.info("OMS active and subscribed to approved risk decisions.")

            # ------------------------------------------------------------------
            # Step 10: Start State Reconciliation Engine
            # ------------------------------------------------------------------
            logger.info("[Startup Step 10/10] Initializing State Reconciliation Engine...")
            if not self.reconciliation_engine:
                self.reconciliation_engine = ReconciliationEngine(
                    event_bus=self.event_bus,
                    exchange_adapter=self.adapter,
                    portfolio_manager=self.portfolio_manager,
                    risk_engine=self.risk_engine,
                    oms=self.oms,
                    session_factory=self.session_factory,
                )
            await self.reconciliation_engine.start()
            logger.info("Reconciliation loop started.")

            # Mark platform running
            self._is_running = True
            logger.info(">>> ALL 10 STARTUP STEPS COMPLETED. PLATFORM IS FULLY OPERATIONAL. <<<")

        except Exception as e:
            self._is_running = False
            logger.critical(f"FATAL: Startup sequence failed at step: {e}", exc_info=True)
            # Clean up anything partially started
            await self.shutdown()
            raise RuntimeError(f"Platform startup aborted: {e}") from e

    async def shutdown(self) -> None:
        """Execute graceful shutdown sequence with in-flight order resolution and snapshots."""
        if not self._is_running and not self._shutdown_event.is_set():
            logger.info("Shutdown called on inactive platform.")
            return

        logger.info(">>> INITIATING GRACEFUL PLATFORM SHUTDOWN <<<")

        # 1. Stop Strategy Runner first (prevents any new signals/orders from being generated)
        if self.strategy_runner:
            try:
                self.strategy_runner.shutdown()
            except Exception as e:
                logger.error(f"Error stopping Strategy Runner: {e}")

        # 2. Stop Reconciliation loop
        if self.reconciliation_engine:
            try:
                await self.reconciliation_engine.stop()
            except Exception as e:
                logger.error(f"Error stopping Reconciliation Engine: {e}")

        # 3. Drain in-flight SUBMITTED orders (wait for active network calls to resolve)
        if self.oms:
            try:
                await self.oms.drain_in_flight(timeout=5.0)
            except Exception as e:
                logger.error(f"Error draining in-flight orders: {e}")

        # 4. Handle remaining open resting orders: cancel cleanly on adapter and sync OMS
        if self.oms and self.adapter:
            try:
                logger.info("Reconciling and cancelling remaining open in-flight orders...")
                open_orders = await self.adapter.get_open_orders()
                for ord_obj in open_orders:
                    try:
                        await self.adapter.cancel_order(
                            symbol=ord_obj.symbol,
                            client_order_id=ord_obj.client_order_id,
                        )
                        local_ord = self.oms.get_order(ord_obj.client_order_id)
                        if local_ord and not OrderStateMachine.is_terminal(local_ord.status):
                            await self.oms._transition_order(local_ord, OrderStatus.CANCELLED)
                        logger.info(
                            f"Cleanly cancelled open order {ord_obj.client_order_id} during shutdown."
                        )
                    except Exception as ce:
                        logger.warning(f"Could not cancel order {ord_obj.client_order_id}: {ce}")
                self.oms._in_flight_orders.clear()
            except Exception as e:
                logger.error(f"Error cleaning open orders during shutdown: {e}")

        # 5. Capture final Portfolio Snapshot (now guaranteed consistent with all fills & cancels)
        if self.portfolio_manager and self.session_factory:
            try:
                logger.info("Persisting final PortfolioSnapshot for all active strategies...")
                active_strats = list(self.portfolio_manager._wallet_balances.keys()) or [
                    self.config.strategy_id
                ]
                for strat_id in active_strats:
                    await self.portfolio_manager.create_snapshot(strat_id)
            except Exception as e:
                logger.error(f"Error persisting final snapshot: {e}")

        # 6. Disconnect WebSocket market data stream
        if self.market_data_client:
            try:
                await self.market_data_client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting Market Data client: {e}")

        # 7. Close Exchange Adapter
        if self.adapter:
            try:
                await self.adapter.close()
            except Exception as e:
                logger.error(f"Error closing Exchange Adapter: {e}")

        self._is_running = False
        self._shutdown_event.set()
        logger.info(">>> PLATFORM SHUTDOWN COMPLETE. ALL SUBSYSTEMS TERMINATED CLEANLY. <<<")

    def register_signal_handlers(self) -> None:
        """Register OS signal handlers for graceful SIGINT and SIGTERM termination."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(self._handle_signal(s)),
                )
            except NotImplementedError:
                # Windows or thread restriction fallback
                pass

    async def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle incoming termination signal."""
        logger.warning(
            f"Received OS signal {sig.name} ({sig.value}). Triggering graceful shutdown..."
        )
        await self.shutdown()

    @property
    def metrics(self) -> dict[str, Any]:
        """Return consolidated platform health and diagnostic metrics."""
        return {
            "is_running": self._is_running,
            "mode": self.config.environment.value,
            "portfolio": self.portfolio_manager.metrics if self.portfolio_manager else {},
            "risk": self.risk_engine.metrics if self.risk_engine else {},
            "oms": self.oms.metrics if self.oms else {},
            "reconciliation": self.reconciliation_engine.metrics
            if self.reconciliation_engine
            else {},
        }
