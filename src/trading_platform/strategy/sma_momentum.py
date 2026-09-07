"""Simple Moving Average (SMA) momentum crossover trading strategy."""

from collections import defaultdict
from typing import Any

from trading_platform.core.constants import SignalType
from trading_platform.core.events import CandleEvent, MarketDataEvent, SignalEvent
from trading_platform.core.logging import get_logger
from trading_platform.strategy.base import Strategy

logger = get_logger("strategy.sma_momentum")


class SMAMomentumStrategy(Strategy):
    """Moving average momentum strategy emitting BUY/SELL signals on Fast/Slow SMA crossover.

    Emits target_exposure as a fractional percentage of total portfolio equity in range [-1.0, 1.0].
    """

    def __init__(
        self,
        strategy_id: str,
        symbols: list[str],
        fast_period: int = 9,
        slow_period: int = 21,
        target_exposure_pct: float = 0.10,
        timeframe: str = "1m",
    ) -> None:
        super().__init__(strategy_id=strategy_id, symbols=symbols, timeframe=timeframe)
        if fast_period >= slow_period:
            raise ValueError(
                f"fast_period ({fast_period}) must be strictly less than slow_period ({slow_period})"
            )
        if not (0.0 < target_exposure_pct <= 1.0):
            raise ValueError(
                f"target_exposure_pct must be in range (0.0, 1.0], got {target_exposure_pct}"
            )
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.target_exposure_pct = target_exposure_pct

        # State per symbol
        self._price_history: dict[str, list[float]] = defaultdict(list)
        self._latest_mark_prices: dict[str, float] = {}
        self._last_signal_state: dict[str, SignalType] = {}

    def on_market_data(self, tick: MarketDataEvent) -> list[SignalEvent]:
        """Update latest mark price for the symbol."""
        if tick.symbol.upper() in self.symbols:
            self._latest_mark_prices[tick.symbol.upper()] = tick.mark_price
        return []

    def on_candle(self, candle: CandleEvent) -> list[SignalEvent]:
        """Process a candle and generate a signal if a moving average crossover occurs."""
        symbol = candle.symbol.upper()
        if symbol not in self.symbols or candle.timeframe != self.timeframe:
            return []

        # Only evaluate signals on completed closed candles
        if not candle.is_closed:
            return []

        history = self._price_history[symbol]
        history.append(candle.close_price)

        # Keep history buffer bounded
        max_history = max(self.slow_period * 3, 100)
        if len(history) > max_history:
            self._price_history[symbol] = history[-max_history:]
            history = self._price_history[symbol]

        # Need at least slow_period + 1 closed candles to detect crossover
        if len(history) < self.slow_period + 1:
            return []

        # Calculate previous and current Fast/Slow SMAs
        prev_fast = sum(history[-self.fast_period - 1 : -1]) / self.fast_period
        prev_slow = sum(history[-self.slow_period - 1 : -1]) / self.slow_period

        curr_fast = sum(history[-self.fast_period :]) / self.fast_period
        curr_slow = sum(history[-self.slow_period :]) / self.slow_period

        mark_price = self._latest_mark_prices.get(symbol, candle.close_price)
        signals: list[SignalEvent] = []

        # 1. Bullish Crossover: Fast SMA crosses ABOVE Slow SMA
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            signal_type = SignalType.BUY
            target_exposure = self.target_exposure_pct
            logger.info(
                f"[STRATEGY: {self.strategy_id}] Bullish Golden Cross detected on {symbol} "
                f"(Fast: {curr_fast:.2f} > Slow: {curr_slow:.2f}) -> Target Exposure: {target_exposure * 100:.1f}%"
            )
            signals.append(
                SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    signal_type=signal_type,
                    target_exposure=target_exposure,
                    mark_price=mark_price,
                    timestamp=candle.close_time,
                    metadata={
                        "fast_sma": curr_fast,
                        "slow_sma": curr_slow,
                        "crossover": "GOLDEN_CROSS",
                    },
                )
            )
            self._last_signal_state[symbol] = signal_type

        # 2. Bearish Crossover: Fast SMA crosses BELOW Slow SMA
        elif prev_fast >= prev_slow and curr_fast < curr_slow:
            signal_type = SignalType.SELL
            target_exposure = -self.target_exposure_pct
            logger.info(
                f"[STRATEGY: {self.strategy_id}] Bearish Death Cross detected on {symbol} "
                f"(Fast: {curr_fast:.2f} < Slow: {curr_slow:.2f}) -> Target Exposure: {target_exposure * 100:.1f}%"
            )
            signals.append(
                SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    signal_type=signal_type,
                    target_exposure=target_exposure,
                    mark_price=mark_price,
                    timestamp=candle.close_time,
                    metadata={
                        "fast_sma": curr_fast,
                        "slow_sma": curr_slow,
                        "crossover": "DEATH_CROSS",
                    },
                )
            )
            self._last_signal_state[symbol] = signal_type

        return signals

    def get_indicators(self, symbol: str) -> dict[str, Any]:
        """Diagnostic helper to inspect active indicator values."""
        sym = symbol.upper()
        history = self._price_history.get(sym, [])
        if len(history) < self.slow_period:
            return {"ready": False, "count": len(history)}

        curr_fast = sum(history[-self.fast_period :]) / self.fast_period
        curr_slow = sum(history[-self.slow_period :]) / self.slow_period
        return {
            "ready": True,
            "fast_sma": curr_fast,
            "slow_sma": curr_slow,
            "last_signal": self._last_signal_state.get(sym),
            "data_points": len(history),
        }
