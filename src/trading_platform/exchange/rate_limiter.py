"""Rate limiter for Binance API tracking real response headers and enforcing throttling."""

import asyncio
import time
from typing import Any

from trading_platform.core.exceptions import RateLimitExceededError
from trading_platform.core.logging import get_logger

logger = get_logger("exchange.rate_limiter")


class BinanceRateLimiter:
    """Tracks used request weight and order count headers, enforcing proactive throttling and hard stops."""

    def __init__(
        self,
        max_weight_1m: int = 2400,
        max_orders_10s: int = 300,
        max_orders_1m: int = 1200,
        throttle_pct: float = 0.80,
        hard_stop_pct: float = 0.95,
    ) -> None:
        self.max_weight_1m = max_weight_1m
        self.max_orders_10s = max_orders_10s
        self.max_orders_1m = max_orders_1m

        self.throttle_threshold = int(max_weight_1m * throttle_pct)
        self.hard_stop_threshold = int(max_weight_1m * hard_stop_pct)

        # Real header values reported by Binance
        self.used_weight_1m: int = 0
        self.order_count_10s: int = 0
        self.order_count_1m: int = 0

        self.last_weight_update_time: float = time.monotonic()
        self.last_window_reset_time: float = time.time()
        self._lock = asyncio.Lock()

        # Metrics
        self.throttled_count: int = 0
        self.hard_stopped_count: int = 0

    def update_from_headers(self, headers: dict[str, str] | Any) -> None:
        """Parse rate limit headers from Binance HTTP response."""
        now = time.monotonic()
        self.last_weight_update_time = now

        # Normalize header keys to lowercase
        norm_headers = (
            {k.lower(): v for k, v in headers.items()} if hasattr(headers, "items") else {}
        )

        # 1. Used Weight 1M
        for key in ["x-mbx-used-weight-1m", "x-mbx-used-weight"]:
            if key in norm_headers:
                try:
                    val = int(norm_headers[key])
                    self.used_weight_1m = max(0, val)
                except ValueError:
                    pass

        # 2. Order counts
        if "x-mbx-order-count-10s" in norm_headers:
            try:
                self.order_count_10s = int(norm_headers["x-mbx-order-count-10s"])
            except ValueError:
                pass

        if "x-mbx-order-count-1m" in norm_headers:
            try:
                self.order_count_1m = int(norm_headers["x-mbx-order-count-1m"])
            except ValueError:
                pass

        # Log warning if approaching thresholds
        if self.used_weight_1m >= self.hard_stop_threshold:
            logger.error(
                f"Rate limit HARD STOP threshold reached: {self.used_weight_1m}/{self.max_weight_1m} "
                f"({(self.used_weight_1m / self.max_weight_1m) * 100:.1f}%)"
            )
        elif self.used_weight_1m >= self.throttle_threshold:
            logger.warning(
                f"Rate limit throttling active: {self.used_weight_1m}/{self.max_weight_1m} "
                f"({(self.used_weight_1m / self.max_weight_1m) * 100:.1f}%)"
            )

    async def acquire(self, estimated_weight: int = 1, is_order: bool = False) -> None:
        """Evaluate current rate limits and throttle or hard-stop before executing request.

        Args:
            estimated_weight: Request weight cost for the target endpoint.
            is_order: Whether the request creates/modifies an order.

        Raises:
            RateLimitExceededError: If current weight exceeds hard-stop limit (95%).
        """
        async with self._lock:
            # 1. Check Hard Stop Threshold (95%)
            if (self.used_weight_1m + estimated_weight) >= self.hard_stop_threshold:
                self.hard_stopped_count += 1
                msg = (
                    f"Binance rate limit hard-stop triggered: used weight {self.used_weight_1m} + "
                    f"cost {estimated_weight} >= limit {self.hard_stop_threshold} ({self.max_weight_1m} max)."
                )
                logger.critical(msg)
                raise RateLimitExceededError(
                    msg,
                    details={
                        "used_weight_1m": self.used_weight_1m,
                        "limit": self.max_weight_1m,
                        "hard_stop_threshold": self.hard_stop_threshold,
                    },
                )

            # 2. Check Order Count Thresholds
            if is_order:
                if self.order_count_10s >= int(self.max_orders_10s * 0.95):
                    self.hard_stopped_count += 1
                    raise RateLimitExceededError(
                        f"Order rate limit hard-stop (10s): {self.order_count_10s}/{self.max_orders_10s}",
                        details={"order_count_10s": self.order_count_10s},
                    )

            # 3. Check Throttling Threshold (80%)
            if self.used_weight_1m >= self.throttle_threshold:
                self.throttled_count += 1
                # Scale sleep time between 0.1s and 1.5s based on how close we are to hard stop
                excess_ratio = (self.used_weight_1m - self.throttle_threshold) / (
                    self.hard_stop_threshold - self.throttle_threshold
                )
                sleep_duration = 0.1 + (excess_ratio * 1.4)
                logger.warning(
                    f"Throttling request by {sleep_duration:.2f}s (Used weight: {self.used_weight_1m}/{self.max_weight_1m})"
                )
                await asyncio.sleep(sleep_duration)

    def reset_state(self) -> None:
        """Reset internal rate limit counters (useful for testing or window rotations)."""
        self.used_weight_1m = 0
        self.order_count_10s = 0
        self.order_count_1m = 0

    @property
    def metrics(self) -> dict[str, Any]:
        """Return rate limiter diagnostics."""
        used = max(0, self.used_weight_1m)
        return {
            "used_weight_1m": used,
            "max_weight_1m": self.max_weight_1m,
            "weight_pct": round((used / self.max_weight_1m) * 100, 2),
            "order_count_10s": max(0, self.order_count_10s),
            "order_count_1m": max(0, self.order_count_1m),
            "throttled_count": self.throttled_count,
            "hard_stopped_count": self.hard_stopped_count,
        }
