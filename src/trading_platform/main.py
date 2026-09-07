"""Application entrypoint for the Binance USDT-M Perpetual Futures Trading Platform."""

import asyncio
import signal
import sys

from trading_platform.core.clock import ClockSync
from trading_platform.core.config import load_config
from trading_platform.core.constants import TradingMode
from trading_platform.core.events import EventBus
from trading_platform.core.logging import (
    clear_log_context,
    get_logger,
    set_log_context,
    setup_logging,
)
from trading_platform.db.session import get_async_engine, get_session_factory
from trading_platform.engine.platform import PlatformEngine
from trading_platform.exchange.binance_futures import BinanceFuturesAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.exchange.paper import PaperExchangeAdapter
from trading_platform.market_data.ws_client import BinanceWebSocketClient
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.reconciliation.engine import ReconciliationEngine
from trading_platform.risk.engine import RiskEngine
from trading_platform.strategy.runner import StrategyRunner
from trading_platform.strategy.signal_manager import SignalManager
from trading_platform.strategy.sma_momentum import SMAMomentumStrategy

import argparse
import uvicorn

logger = get_logger("main")


class TradingPlatformApp:
    """Core platform lifecycle manager for offline/headless mode."""

    def __init__(self) -> None:
        self.config = load_config()
        self.event_bus = EventBus()
        self.engine = get_async_engine(self.config.database)
        self.session_factory = get_session_factory(self.engine)
        self.platform: PlatformEngine | None = None
        self._shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """Execute platform subsystem wiring and startup sequence."""
        # 1. Initialize structured logging
        setup_logging(
            level=self.config.logging.level,
            json_format=self.config.logging.json_format,
            log_file=self.config.logging.log_file,
        )

        logger.info("=================================================================")
        logger.info("  VECTOR // ALGORITHMIC CRYPTO TRADING ENGINE (HEADLESS MODE)    ")
        logger.info("=================================================================")
        logger.info(f"Environment Mode        : {self.config.environment.value}")
        logger.info(f"Active Strategy ID      : {self.config.strategy_id}")
        logger.info(f"Live Trading Enabled    : {self.config.live_trading_enabled}")
        logger.info(
            f"Active Universe Symbols : {', '.join(self.config.market_data.active_symbols)}"
        )
        logger.info(
            f"Max Portfolio Exposure  : ${self.config.risk.max_portfolio_exposure_usd:,.2f} USD"
        )
        logger.info(f"Max Position Size       : ${self.config.risk.max_position_size_usd:,.2f} USD")
        logger.info(f"Max Leverage            : {self.config.risk.max_leverage}x")
        logger.info(f"Min Liquidation Dist.   : {self.config.risk.min_liquidation_distance_pct}%")
        logger.info("=================================================================")

        set_log_context(strategy_id=self.config.strategy_id)

        # 2. Instantiate Exchange Adapter based on environment
        if self.config.environment in (TradingMode.TESTNET, TradingMode.LIVE):
            adapter = BinanceFuturesAdapter(config=self.config)
        else:
            adapter = PaperExchangeAdapter(initial_wallet_balance=10000.0)

        # 3. Instantiate Subsystems
        clock_sync = ClockSync(max_drift_ms=self.config.clock_max_drift_ms)
        im = InstrumentManager(config=self.config, adapter=adapter)
        pm = PortfolioManager(
            event_bus=self.event_bus,
            session_factory=self.session_factory,
            default_deposit_usd=10000.0,
        )

        # Market Data Client
        if self.config.environment in (TradingMode.TESTNET, TradingMode.LIVE):
            market_data_client = BinanceWebSocketClient(
                config=self.config,
                event_bus=self.event_bus,
            )
        else:
            market_data_client = None

        # Strategy Layer
        signal_manager = SignalManager(
            event_bus=self.event_bus,
            session_factory=self.session_factory,
        )
        strategy_runner = StrategyRunner(
            event_bus=self.event_bus,
            signal_manager=signal_manager,
        )
        sma_strategy = SMAMomentumStrategy(
            strategy_id=self.config.strategy_id,
            symbols=self.config.market_data.active_symbols,
            timeframe=self.config.market_data.candle_timeframe,
        )
        strategy_runner.register_strategy(sma_strategy)

        # Risk Engine & OMS
        risk_engine = RiskEngine(
            event_bus=self.event_bus,
            portfolio_manager=pm,
            instrument_manager=im,
            session_factory=self.session_factory,
        )
        oms = OrderManagementSystem(
            event_bus=self.event_bus,
            exchange_adapter=adapter,
            portfolio_manager=pm,
            instrument_manager=im,
            session_factory=self.session_factory,
        )
        reconciliation_engine = ReconciliationEngine(
            event_bus=self.event_bus,
            exchange_adapter=adapter,
            portfolio_manager=pm,
            risk_engine=risk_engine,
            oms=oms,
            session_factory=self.session_factory,
        )

        # 4. Assemble PlatformEngine
        self.platform = PlatformEngine(
            config=self.config,
            event_bus=self.event_bus,
            session_factory=self.session_factory,
            exchange_adapter=adapter,
            clock_sync=clock_sync,
            instrument_manager=im,
            market_data_client=market_data_client,
            portfolio_manager=pm,
            strategy_runner=strategy_runner,
            risk_engine=risk_engine,
            oms=oms,
            reconciliation_engine=reconciliation_engine,
        )

        # Execute 10-step startup sequence
        await self.platform.startup()
        self.platform.register_signal_handlers()

    async def run(self) -> None:
        """Run the platform event loop until shutdown signal."""
        await self.initialize()
        logger.info("Headless platform is running. Press Ctrl+C to terminate.")
        try:
            if self.platform:
                await self.platform._shutdown_event.wait()
            else:
                await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Perform graceful platform shutdown."""
        if self.platform and self.platform.is_running:
            await self.platform.shutdown()
        await self.engine.dispose()
        clear_log_context()
        logger.info("Platform shutdown complete.")

    def request_shutdown(self) -> None:
        """Signal platform loop to terminate."""
        if self.platform:
            asyncio.create_task(self.platform.shutdown())
        self._shutdown_event.set()


def run_unified_service(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the authoritative unified service (FastAPI + Strategy Runner + Risk Engine + OMS + Dashboard API)."""
    print(f"Starting Unified Trading Service on http://{host}:{port}...")
    uvicorn.run("trading_platform.api.server:app", host=host, port=port, reload=False)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="VECTOR Algorithmic Crypto Trading Engine")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run standalone headless PlatformEngine without the API server (for CLI/offline mode only)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="API server host")
    parser.add_argument("--port", type=int, default=8000, help="API server port")
    args = parser.parse_args()

    if args.headless:
        app = TradingPlatformApp()
        asyncio.run(app.run())
    else:
        run_unified_service(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
