"""System telemetry, component health, and environment status endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text

from trading_platform.api.container import PlatformContainer, get_container
from trading_platform.api.schemas import SystemComponentHealth, SystemStatusResponse
from trading_platform.core.constants import TradingMode
from trading_platform.db.session import get_db_session

router = APIRouter(prefix="/system", tags=["System"])


@router.get("", response_model=SystemStatusResponse)
async def get_system_status(
    container: PlatformContainer = Depends(get_container),
) -> SystemStatusResponse:
    """Retrieve comprehensive platform telemetry, connection status, and subsystem health."""
    now = datetime.now(UTC)
    config = container.config

    # Subsystem health list
    components: list[SystemComponentHealth] = []

    # 1. Event Bus Health
    bus_metrics = container.event_bus.metrics
    components.append(
        SystemComponentHealth(
            name="EventBus",
            status="ONLINE",
            details=f"Subscribers: {len(bus_metrics['subscribers'])}, Published: {sum(bus_metrics['published'].values())}",
            last_heartbeat=now,
        )
    )

    # 2. Risk Engine Health
    risk_status = "ONLINE"
    risk_details = "All risk rules operational"
    if container.risk_engine.is_kill_switch_active:
        risk_status = "HALTED"
        risk_details = f"Kill switch active: {container.risk_engine._kill_switch_reason}"
    components.append(
        SystemComponentHealth(
            name="RiskEngine",
            status=risk_status,
            details=risk_details,
            last_heartbeat=now,
        )
    )

    # 3. OMS Health
    in_flight = len(container.oms._in_flight_orders) if container.oms else 0
    components.append(
        SystemComponentHealth(
            name="OrderManagementSystem",
            status="ONLINE",
            details=f"In-flight orders: {in_flight}",
            last_heartbeat=now,
        )
    )

    # 4. Exchange Adapter Health & Rate Limits
    used_weight = 0
    max_weight = 2400
    if container.exchange_adapter and hasattr(container.exchange_adapter, "rate_limiter"):
        metrics = container.exchange_adapter.rate_limiter.metrics
        used_weight = metrics.get("used_weight_1m", 0)
        max_weight = metrics.get("max_weight_1m", 2400)

    adapter_status = "ONLINE" if config.environment != TradingMode.BACKTEST else "SIMULATED"
    components.append(
        SystemComponentHealth(
            name="ExchangeAdapter",
            status=adapter_status,
            details=f"Mode: {config.environment.value} | Used Weight: {used_weight}/{max_weight}",
            last_heartbeat=now,
        )
    )

    # 5. Reconciliation Engine Health
    recon_res = container.last_reconciliation_result
    recon_status = recon_res.get("status", "HEALTHY")
    recon_time = recon_res.get("last_time")
    recon_mismatches = recon_res.get("mismatch_count", 0)

    components.append(
        SystemComponentHealth(
            name="ReconciliationEngine",
            status=recon_status,
            details=f"Mismatches: {recon_mismatches} | Last run: {recon_time.strftime('%H:%M:%S') if recon_time else 'Pending'}",
            last_heartbeat=recon_time or now,
        )
    )

    # 6. Database Health
    db_status = "LOCAL_MEMORY"
    db_details = "In-memory session mode (no external PostgreSQL configured)"
    if container.session_factory:
        try:
            async with get_db_session(container.session_factory) as session:
                await session.execute(text("SELECT 1"))
            db_status = "ONLINE"
            db_details = (
                f"Connected: {config.database.name} ({config.database.host}:{config.database.port})"
            )
        except Exception as e:
            db_status = "OFFLINE"
            db_details = (
                f"Database unreachable ({config.database.host}:{config.database.port}): {e}"
            )

    components.append(
        SystemComponentHealth(
            name="DatabaseStorage",
            status=db_status,
            details=db_details,
            last_heartbeat=now,
        )
    )

    return SystemStatusResponse(
        environment=config.environment,
        live_trading_enabled=config.live_trading_enabled,
        server_time_utc=now,
        clock_drift_ms=round(container.last_clock_drift_ms, 2),
        rtt_latency_ms=round(container.last_rtt_latency_ms, 2)
        if container.last_rtt_latency_ms
        else None,
        rate_limit_used_1m=used_weight,
        rate_limit_max_1m=max_weight,
        reconciliation_status=recon_status,
        last_reconciliation_time=recon_time,
        reconciliation_mismatches_count=recon_mismatches,
        components=components,
    )
