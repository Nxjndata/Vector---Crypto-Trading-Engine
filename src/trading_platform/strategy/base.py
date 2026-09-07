"""Abstract base class definition for trading strategies."""

from abc import ABC, abstractmethod

from trading_platform.core.events import CandleEvent, MarketDataEvent, SignalEvent


class Strategy(ABC):
    """Abstract interface for pure-intent quantitative trading strategies.

    Non-negotiable rule: Strategies must NEVER place orders or touch exchange credentials.
    They consume market data and emit SignalEvent instances representing pure intent.
    """

    def __init__(
        self,
        strategy_id: str,
        symbols: list[str],
        timeframe: str = "1m",
    ) -> None:
        self.strategy_id = strategy_id
        self.symbols = [s.upper() for s in symbols]
        self.timeframe = timeframe

    @abstractmethod
    def on_candle(self, candle: CandleEvent) -> list[SignalEvent]:
        """Process a newly closed candle and emit trading signals if triggered."""
        pass

    @abstractmethod
    def on_market_data(self, tick: MarketDataEvent) -> list[SignalEvent]:
        """Process a real-time market data or mark price update."""
        pass

    def initialize(self, historical_candles: dict[str, list[CandleEvent]] | None = None) -> None:
        """Warm up indicators using historical candle data if available."""
        if historical_candles:
            for symbol, candles in historical_candles.items():
                if symbol.upper() in self.symbols:
                    for candle in candles:
                        self.on_candle(candle)

    def shutdown(self) -> None:
        """Clean up strategy state on termination. Override in subclasses if required."""
        return None
