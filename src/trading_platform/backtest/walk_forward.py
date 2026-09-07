"""Walk-forward cross-validation and in-sample/out-of-sample data splitting to prevent overfitting."""

from collections.abc import Callable
from typing import Any

from trading_platform.backtest.models import WalkForwardWindow
from trading_platform.core.logging import get_logger
from trading_platform.models.candle import Candle
from trading_platform.strategy.base import Strategy

logger = get_logger("backtest.walk_forward")


class WalkForwardValidator:
    """Provides temporal dataset partitioning and rolling walk-forward validation with zero lookahead bias."""

    @staticmethod
    def train_test_split(
        candles: list[Candle],
        train_ratio: float = 0.70,
    ) -> tuple[list[Candle], list[Candle]]:
        """Split a historical candle series into in-sample (train) and out-of-sample (test) segments.

        Enforces strict temporal ordering: no future candles can leak into the training set.
        """
        if not candles:
            return [], []

        if not (0.0 < train_ratio < 1.0):
            raise ValueError(f"train_ratio must be strictly between 0.0 and 1.0, got {train_ratio}")

        split_idx = int(len(candles) * train_ratio)
        train_candles = candles[:split_idx]
        test_candles = candles[split_idx:]

        # Verification assertion: strictly no temporal overlap
        if train_candles and test_candles:
            if train_candles[-1].open_time >= test_candles[0].open_time:
                raise ValueError("Data leakage detected: train candles overlap with test candles!")

        logger.info(
            f"Train/Test split complete: {len(train_candles)} in-sample candles, "
            f"{len(test_candles)} out-of-sample candles (Ratio: {train_ratio:.1%})."
        )
        return train_candles, test_candles

    @staticmethod
    def generate_windows(
        candles: list[Candle],
        train_size: int,
        test_size: int,
        step_size: int | None = None,
    ) -> list[tuple[WalkForwardWindow, list[Candle], list[Candle]]]:
        """Generate rolling walk-forward validation windows.

        Args:
            candles: Chronologically sorted historical candle list.
            train_size: Number of candles per training (in-sample) window.
            test_size: Number of candles per test (out-of-sample) window.
            step_size: Number of candles to advance forward each iteration (defaults to test_size).

        Returns:
            List of tuples: (WalkForwardWindow metadata, train_candles, test_candles).
        """
        if train_size <= 0 or test_size <= 0:
            raise ValueError("train_size and test_size must be positive integers.")

        step = step_size or test_size
        if step <= 0:
            raise ValueError("step_size must be a positive integer.")

        total_candles = len(candles)
        window_size = train_size + test_size

        if total_candles < window_size:
            logger.warning(
                f"Insufficient candles ({total_candles}) for even one full window of size {window_size}."
            )
            return []

        windows: list[tuple[WalkForwardWindow, list[Candle], list[Candle]]] = []
        window_id = 1
        start_idx = 0

        while start_idx + window_size <= total_candles:
            train_end_idx = start_idx + train_size
            test_end_idx = train_end_idx + test_size

            train_slice = candles[start_idx:train_end_idx]
            test_slice = candles[train_end_idx:test_end_idx]

            # Enforce zero-leakage guarantee
            assert train_slice[-1].open_time < test_slice[0].open_time, (
                f"Walk-forward leakage detected in window {window_id}: "
                f"Train end {train_slice[-1].open_time} >= Test start {test_slice[0].open_time}"
            )

            wf_window = WalkForwardWindow(
                window_id=window_id,
                train_start=train_slice[0].open_time,
                train_end=train_slice[-1].close_time,
                test_start=test_slice[0].open_time,
                test_end=test_slice[-1].close_time,
            )

            windows.append((wf_window, train_slice, test_slice))
            start_idx += step
            window_id += 1

        logger.info(
            f"Generated {len(windows)} walk-forward validation windows "
            f"(Train size: {train_size}, Test size: {test_size}, Step: {step})."
        )
        return windows

    @classmethod
    async def evaluate_walk_forward(
        cls,
        engine_factory: Callable[[], Any],
        strategy_factory: Callable[[], Strategy],
        windows: list[tuple[WalkForwardWindow, list[Candle], list[Candle]]],
    ) -> list[WalkForwardWindow]:
        """Execute walk-forward evaluation across all windows, isolating in-sample and out-of-sample results."""
        results: list[WalkForwardWindow] = []

        for window_meta, train_candles, test_candles in windows:
            logger.info(
                f"Evaluating Walk-Forward Window {window_meta.window_id}: "
                f"In-Sample [{window_meta.train_start.isoformat()} -> {window_meta.train_end.isoformat()}], "
                f"Out-of-Sample [{window_meta.test_start.isoformat()} -> {window_meta.test_end.isoformat()}]"
            )

            # 1. In-Sample Run
            train_strategy = strategy_factory()
            train_engine = engine_factory()
            in_sample_metrics = await train_engine.run(train_strategy, train_candles)
            window_meta.in_sample_metrics = in_sample_metrics

            # 2. Out-of-Sample Run
            test_strategy = strategy_factory()
            test_engine = engine_factory()
            out_of_sample_metrics = await test_engine.run(test_strategy, test_candles)
            window_meta.out_of_sample_metrics = out_of_sample_metrics

            logger.info(
                f"Window {window_meta.window_id} Results: "
                f"In-Sample Return: {in_sample_metrics.total_return_pct:+.2f}% | "
                f"Out-of-Sample Return: {out_of_sample_metrics.total_return_pct:+.2f}%"
            )
            results.append(window_meta)

        return results
