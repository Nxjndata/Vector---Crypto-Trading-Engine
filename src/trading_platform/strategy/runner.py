"""Strategy runner coordinating market data routing and strategy lifecycles."""

from typing import Any

from trading_platform.core.events import CandleEvent, EventBus, MarketDataEvent
from trading_platform.core.logging import get_logger
from trading_platform.strategy.base import Strategy
from trading_platform.strategy.signal_manager import SignalManager

logger = get_logger("strategy.runner")


class StrategyRunner:
    """Subscribes to market data events on the EventBus, routes to strategies, and manages signals."""

    def __init__(
        self,
        event_bus: EventBus,
        signal_manager: SignalManager,
    ) -> None:
        self.event_bus = event_bus
        self.signal_manager = signal_manager
        self._strategies: dict[str, Strategy] = {}

        # Register event bus listeners
        self.event_bus.subscribe(CandleEvent, self.on_candle_event)
        self.event_bus.subscribe(MarketDataEvent, self.on_market_data_event)

    def register_strategy(self, strategy: Strategy) -> None:
        """Register an active strategy instance."""
        self._strategies[strategy.strategy_id] = strategy
        logger.info(
            f"Registered strategy '{strategy.strategy_id}' for symbols: {strategy.symbols} "
            f"(Timeframe: {strategy.timeframe})"
        )

    def unregister_strategy(self, strategy_id: str) -> None:
        """Unregister and shutdown a strategy."""
        if strategy_id in self._strategies:
            strat = self._strategies.pop(strategy_id)
            strat.shutdown()
            logger.info(f"Unregistered strategy '{strategy_id}'.")

    def get_strategy(self, strategy_id: str) -> Strategy | None:
        """Retrieve registered strategy by ID."""
        return self._strategies.get(strategy_id)

    def list_strategies(self) -> list[str]:
        """List all active strategy IDs."""
        return list(self._strategies.keys())

    async def on_candle_event(self, event: CandleEvent) -> None:
        """Route incoming CandleEvent to all listening strategies."""
        for strategy in self._strategies.values():
            if event.symbol.upper() in strategy.symbols and event.timeframe == strategy.timeframe:
                try:
                    signals = strategy.on_candle(event)
                    for sig in signals:
                        await self.signal_manager.process_signal(sig)
                except Exception as e:
                    logger.error(
                        f"Error in strategy '{strategy.strategy_id}' processing candle for {event.symbol}: {e}",
                        exc_info=True,
                    )

    async def on_market_data_event(self, event: MarketDataEvent) -> None:
        """Route incoming MarketDataEvent to all listening strategies."""
        for strategy in self._strategies.values():
            if event.symbol.upper() in strategy.symbols:
                try:
                    signals = strategy.on_market_data(event)
                    for sig in signals:
                        await self.signal_manager.process_signal(sig)
                except Exception as e:
                    logger.error(
                        f"Error in strategy '{strategy.strategy_id}' processing tick for {event.symbol}: {e}",
                        exc_info=True,
                    )

    async def start(self) -> None:
        """Start the strategy runner lifecycle."""
        logger.info(f"StrategyRunner started with {len(self._strategies)} active strategies.")

    async def stop(self) -> None:
        """Stop the strategy runner and gracefully shutdown strategies."""
        self.shutdown()

    def shutdown(self) -> None:
        """Shutdown all registered strategies."""
        for strategy in list(self._strategies.values()):
            strategy.shutdown()
        self._strategies.clear()
        logger.info("StrategyRunner shutdown complete.")

    @property
    def metrics(self) -> dict[str, Any]:
        """Return runner diagnostics."""
        return {
            "active_strategies": list(self._strategies.keys()),
            "signal_manager": self.signal_manager.metrics,
        }
