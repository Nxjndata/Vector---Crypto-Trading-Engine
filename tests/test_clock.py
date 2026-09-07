"""Tests for the clock drift verification utility."""

from unittest.mock import AsyncMock, patch

import pytest

from trading_platform.core.clock import verify_clock_sync
from trading_platform.core.exceptions import ClockDriftError


@pytest.mark.asyncio
async def test_clock_sync_acceptable_drift():
    """Verify clock sync passes when offset is within normal tolerance."""
    with patch("trading_platform.core.clock._check_ntp_drift", AsyncMock(return_value=12.5)):
        drift = await verify_clock_sync(max_drift_ms=1000, warn_threshold_ms=300)
        assert drift == 12.5


@pytest.mark.asyncio
async def test_clock_sync_warning_logged(caplog):
    """Verify warning is logged when drift exceeds warn_threshold_ms."""
    with patch("trading_platform.core.clock._check_ntp_drift", AsyncMock(return_value=450.0)):
        drift = await verify_clock_sync(max_drift_ms=1000, warn_threshold_ms=300)
        assert drift == 450.0
        assert any("Clock drift warning" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_clock_sync_critical_drift_raises_strict():
    """Verify ClockDriftError is raised when strict mode is enabled and drift exceeds limit."""
    with patch("trading_platform.core.clock._check_ntp_drift", AsyncMock(return_value=2500.0)):
        with pytest.raises(ClockDriftError, match="CRITICAL CLOCK DRIFT"):
            await verify_clock_sync(max_drift_ms=1000, enforce_strict=True)


@pytest.mark.asyncio
async def test_clock_sync_fallback_to_http():
    """Verify fallback to HTTP time endpoint when NTP is unreachable."""
    with (
        patch("trading_platform.core.clock._check_ntp_drift", AsyncMock(return_value=None)),
        patch("trading_platform.core.clock._check_http_binance_time", AsyncMock(return_value=15.0)),
    ):
        drift = await verify_clock_sync(max_drift_ms=1000)
        assert drift == 15.0


@pytest.mark.asyncio
async def test_clock_sync_all_unreachable_fallback(caplog):
    """Verify graceful handling when all time servers are unreachable."""
    with (
        patch("trading_platform.core.clock._check_ntp_drift", AsyncMock(return_value=None)),
        patch("trading_platform.core.clock._check_http_binance_time", AsyncMock(return_value=None)),
    ):
        drift = await verify_clock_sync(max_drift_ms=1000)
        assert drift == 0.0
        assert any(
            "Unable to reach any NTP or exchange time reference" in record.message
            for record in caplog.records
        )
