"""Standalone API server entrypoint for running FastAPI with live platform subsystems."""

import uvicorn

from trading_platform.api.app import create_app
from trading_platform.api.container import PlatformContainer
from trading_platform.core.clock import ClockSync
from trading_platform.core.config import load_config
from trading_platform.core.constants import TradingMode
from trading_platform.core.events import EventBus
from trading_platform.core.logging import setup_logging
from trading_platform.db.session import get_async_engine, get_session_factory
from trading_platform.exchange.binance_futures import BinanceFuturesAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.exchange.paper import PaperExchangeAdapter
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.reconciliation.engine import ReconciliationEngine
from trading_platform.risk.engine import RiskEngine
from trading_platform.strategy.runner import StrategyRunner
from trading_platform.strategy.signal_manager import SignalManager
from trading_platform.strategy.sma_momentum import SMAMomentumStrategy


def build_app():
    """Build platform container and instantiate configured FastAPI application."""
    config = load_config()
    setup_logging(level=config.logging.level, json_format=config.logging.json_format)

    event_bus = EventBus()
    engine = get_async_engine(config.database)
    session_factory = get_session_factory(engine)

    # 1. Initialize Portfolio Manager
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )

    # 2. Initialize Exchange Adapter
    if config.environment == TradingMode.TESTNET:
        adapter = BinanceFuturesAdapter(config=config)
    elif config.environment == TradingMode.PAPER:
        adapter = PaperExchangeAdapter(initial_wallet_balance=10000.0)
    else:
        adapter = PaperExchangeAdapter(initial_wallet_balance=10000.0)

    # 3. Instrument Manager & Clock Sync
    im = InstrumentManager(config=config, adapter=adapter)
    clock_sync = ClockSync(max_drift_ms=config.clock_max_drift_ms)

    # 4. Strategy Layer
    signal_manager = SignalManager(
        event_bus=event_bus,
        session_factory=session_factory,
    )
    strategy_runner = StrategyRunner(
        event_bus=event_bus,
        signal_manager=signal_manager,
    )
    sma_strategy = SMAMomentumStrategy(
        strategy_id=config.strategy_id,
        symbols=config.market_data.active_symbols,
        timeframe=config.market_data.candle_timeframe,
    )
    strategy_runner.register_strategy(sma_strategy)

    # 5. Risk Engine & OMS
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )
    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=adapter,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )
    reconciliation_engine = ReconciliationEngine(
        event_bus=event_bus,
        exchange_adapter=adapter,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        oms=oms,
        session_factory=session_factory,
    )

    container = PlatformContainer(
        config=config,
        event_bus=event_bus,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        oms=oms,
        reconciliation_engine=reconciliation_engine,
        instrument_manager=im,
        strategy_runner=strategy_runner,
        signal_manager=signal_manager,
        clock_sync=clock_sync,
        exchange_adapter=adapter,
        session_factory=session_factory,
    )

    return create_app(container)


app = build_app()

if __name__ == "__main__":
    uvicorn.run("trading_platform.api.server:app", host="127.0.0.1", port=8000, reload=False)
