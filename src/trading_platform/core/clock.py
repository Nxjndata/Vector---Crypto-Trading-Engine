"""Clock drift verification utility to prevent Binance timestamp skew rejections."""

import asyncio
import time
from datetime import UTC, datetime

import httpx
import ntplib

from trading_platform.core.exceptions import ClockDriftError
from trading_platform.core.logging import get_logger

logger = get_logger("core.clock")

NTP_SERVERS = [
    "pool.ntp.org",
    "time.google.com",
    "time.cloudflare.com",
]

HTTP_TIME_ENDPOINTS = [
    "https://fapi.binance.com/fapi/v1/time",  # Binance Futures server time
    "https://api.binance.com/api/v3/time",  # Binance Spot server time
]

# Global state tracking measured clock drift in milliseconds
_current_drift_ms: float = 0.0


def set_clock_drift_ms(offset_ms: float) -> None:
    """Set the active clock offset in milliseconds."""
    global _current_drift_ms
    _current_drift_ms = offset_ms


def get_clock_drift_ms() -> float:
    """Retrieve the currently measured clock offset in milliseconds."""
    return _current_drift_ms


def get_synchronized_timestamp_ms() -> int:
    """Return the current drift-corrected UTC timestamp in milliseconds."""
    local_ms = time.time() * 1000.0
    return int(local_ms + _current_drift_ms)


async def _check_ntp_drift(server: str, timeout: float = 2.0) -> float | None:
    """Query an NTP server synchronously in a worker thread. Returns offset in ms."""

    def _query() -> float:
        client = ntplib.NTPClient()
        response = client.request(server, version=3, timeout=timeout)
        # response.offset is in seconds; convert to ms
        return response.offset * 1000.0

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.debug(f"NTP query failed for {server}: {e}")
        return None


async def _check_http_binance_time(endpoint: str, timeout: float = 2.0) -> float | None:
    """Query Binance HTTP server time endpoint. Returns offset in ms."""
    try:
        start_mono = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(endpoint)
            end_mono = time.monotonic()

            if resp.status_code == 200:
                data = resp.json()
                server_time_ms = data.get("serverTime")
                if server_time_ms is not None:
                    # Round-trip latency estimation (half RTT)
                    rtt_ms = (end_mono - start_mono) * 1000.0
                    estimated_server_time_at_recv = server_time_ms - (rtt_ms / 2.0)
                    local_time_ms = time.time() * 1000.0
                    # Offset = server_time - local_time
                    offset_ms = estimated_server_time_at_recv - local_time_ms
                    return offset_ms
    except Exception as e:
        logger.debug(f"HTTP time query failed for {endpoint}: {e}")
        return None
    return None


async def verify_clock_sync(
    max_drift_ms: int = 1000,
    warn_threshold_ms: int = 300,
    enforce_strict: bool = False,
) -> float:
    """Verify local system clock synchronization against NTP and exchange servers.

    Args:
        max_drift_ms: Maximum allowed clock skew in ms before raising ClockDriftError (if strict).
        warn_threshold_ms: Threshold in ms above which a warning is logged.
        enforce_strict: If True, raises ClockDriftError when max_drift_ms is exceeded.

    Returns:
        Measured clock drift in milliseconds (relative to reference servers).
    """
    logger.info("Verifying system clock synchronization...")
    measured_offset_ms: float | None = None

    # 1. Try NTP servers first
    for server in NTP_SERVERS:
        offset = await _check_ntp_drift(server)
        if offset is not None:
            measured_offset_ms = offset
            logger.debug(f"Clock offset verified via NTP server {server}: {offset:+.2f}ms")
            break

    # 2. Fallback to Binance HTTP time endpoints
    if measured_offset_ms is None:
        for endpoint in HTTP_TIME_ENDPOINTS:
            offset = await _check_http_binance_time(endpoint)
            if offset is not None:
                measured_offset_ms = offset
                logger.debug(f"Clock offset verified via HTTP endpoint {endpoint}: {offset:+.2f}ms")
                break

    if measured_offset_ms is None:
        msg = "Unable to reach any NTP or exchange time reference servers. Proceeding with unverified local clock."
        logger.warning(msg)
        set_clock_drift_ms(0.0)
        return 0.0

    set_clock_drift_ms(measured_offset_ms)
    abs_drift = abs(measured_offset_ms)
    local_iso = datetime.now(UTC).isoformat()

    if abs_drift > max_drift_ms:
        msg = (
            f"CRITICAL CLOCK DRIFT: Local clock skew is {measured_offset_ms:+.2f}ms "
            f"(threshold: {max_drift_ms}ms). Local UTC: {local_iso}. "
            f"Binance requests will likely fail with timestamp recvWindow errors."
        )
        logger.error(msg)
        if enforce_strict:
            raise ClockDriftError(
                msg, {"drift_ms": measured_offset_ms, "threshold_ms": max_drift_ms}
            )
    elif abs_drift > warn_threshold_ms:
        logger.warning(
            f"Clock drift warning: Local clock skew is {measured_offset_ms:+.2f}ms "
            f"(warning threshold: {warn_threshold_ms}ms)."
        )
    else:
        logger.info(f"Clock synchronization verified. Drift: {measured_offset_ms:+.2f}ms (OK)")

    return measured_offset_ms


class ClockSync:
    """NTP and Exchange clock synchronization checker."""

    def __init__(
        self,
        max_drift_ms: int = 1000,
        warn_threshold_ms: int = 300,
        enforce_strict: bool = True,
    ) -> None:
        self.max_drift_ms = max_drift_ms
        self.warn_threshold_ms = warn_threshold_ms
        self.enforce_strict = enforce_strict

    async def check_sync(self) -> bool:
        """Check clock sync and return True if within allowable drift."""
        try:
            drift = await verify_clock_sync(
                max_drift_ms=self.max_drift_ms,
                warn_threshold_ms=self.warn_threshold_ms,
                enforce_strict=self.enforce_strict,
            )
            return abs(drift) <= self.max_drift_ms
        except ClockDriftError:
            return False
