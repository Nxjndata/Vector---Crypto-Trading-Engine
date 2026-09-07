"""Tests for the BinanceRateLimiter real-header tracking and throttling engine."""

import asyncio

import pytest

from trading_platform.core.exceptions import RateLimitExceededError
from trading_platform.exchange.rate_limiter import BinanceRateLimiter


def test_rate_limiter_header_parsing():
    """Verify rate limiter parses real Binance rate limit headers."""
    limiter = BinanceRateLimiter(max_weight_1m=2400)

    headers = {
        "x-mbx-used-weight-1m": "450",
        "x-mbx-order-count-10s": "12",
        "x-mbx-order-count-1m": "50",
    }
    limiter.update_from_headers(headers)

    assert limiter.used_weight_1m == 450
    assert limiter.order_count_10s == 12
    assert limiter.order_count_1m == 50
    assert limiter.metrics["weight_pct"] == 18.75


@pytest.mark.asyncio
async def test_rate_limiter_throttling_at_80_percent():
    """Verify rate limiter sleeps / throttles when used weight exceeds 80% (1920/2400)."""
    limiter = BinanceRateLimiter(max_weight_1m=2400, throttle_pct=0.80, hard_stop_pct=0.95)

    # Set used weight to 1950 (> 1920 throttle threshold, < 2280 hard stop)
    limiter.update_from_headers({"x-mbx-used-weight-1m": "1950"})

    start_time = asyncio.get_running_loop().time()
    await limiter.acquire(estimated_weight=1)
    end_time = asyncio.get_running_loop().time()

    elapsed = end_time - start_time
    assert elapsed >= 0.1  # Throttled sleep triggered
    assert limiter.throttled_count == 1


@pytest.mark.asyncio
async def test_rate_limiter_hard_stop_at_95_percent():
    """Verify rate limiter raises RateLimitExceededError when used weight exceeds 95% (2280/2400)."""
    limiter = BinanceRateLimiter(max_weight_1m=2400, throttle_pct=0.80, hard_stop_pct=0.95)

    # Set used weight to 2285 (> 2280 hard stop threshold)
    limiter.update_from_headers({"x-mbx-used-weight-1m": "2285"})

    with pytest.raises(RateLimitExceededError, match="hard-stop triggered"):
        await limiter.acquire(estimated_weight=1)

    assert limiter.hard_stopped_count == 1


@pytest.mark.asyncio
async def test_rate_limiter_order_rate_limit_hard_stop():
    """Verify hard stop when 10-second order count exceeds 95% of limit."""
    limiter = BinanceRateLimiter(max_orders_10s=300)

    limiter.update_from_headers({"x-mbx-order-count-10s": "290"})

    with pytest.raises(RateLimitExceededError, match="Order rate limit hard-stop"):
        await limiter.acquire(estimated_weight=0, is_order=True)
