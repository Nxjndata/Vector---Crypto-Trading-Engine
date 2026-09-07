"""Platform dependency container and service locator for FastAPI routes."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.clock import ClockSync
from trading_platform.core.config import AppConfig
from trading_platform.core.events import EventBus
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.reconciliation.engine import ReconciliationEngine
from trading_platform.risk.engine import RiskEngine
from trading_platform.strategy.runner import StrategyRunner
from trading_platform.strategy.signal_manager import SignalManager


class PlatformContainer:
    """Holds references to running subsystem singletons for the API layer."""

    def __init__(
        self,
        config: AppConfig,
        event_bus: EventBus,
        portfolio_manager: PortfolioManager,
        risk_engine: RiskEngine,
        oms: OrderManagementSystem,
        reconciliation_engine: ReconciliationEngine | None = None,
        instrument_manager: InstrumentManager | None = None,
        strategy_runner: StrategyRunner | None = None,
        signal_manager: SignalManager | None = None,
        clock_sync: ClockSync | None = None,
        exchange_adapter: ExchangeAdapter | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.portfolio_manager = portfolio_manager
        self.risk_engine = risk_engine
        self.oms = oms
        self.reconciliation_engine = reconciliation_engine
        self.instrument_manager = instrument_manager
        self.strategy_runner = strategy_runner
        self.signal_manager = signal_manager
        self.clock_sync = clock_sync
        self.exchange_adapter = exchange_adapter
        self.session_factory = session_factory

        # Cache for strategy performance telemetry
        self.start_time: datetime = datetime.now(UTC)
        self.last_reconciliation_result: dict[str, Any] = {
            "status": "HEALTHY",
            "last_time": None,
            "mismatch_count": 0,
        }
        self.last_clock_drift_ms: float = 0.0
        self.last_rtt_latency_ms: float = 0.0

        # Market prices / 24h ticker cache for fast API response
        self.market_tickers: dict[str, dict[str, Any]] = {}

    @property
    def strategy_id(self) -> str:
        return self.config.strategy_id


# Global container instance accessible via dependency injection
_container: PlatformContainer | None = None


def set_container(container: PlatformContainer) -> None:
    """Set the global API platform container."""
    global _container
    _container = container


def get_container() -> PlatformContainer:
    """FastAPI dependency to retrieve the active platform container."""
    if _container is None:
        raise RuntimeError("PlatformContainer has not been initialized.")
    return _container
