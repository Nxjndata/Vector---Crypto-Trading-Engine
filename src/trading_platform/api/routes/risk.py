"""Risk monitoring and manual emergency control endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select

from trading_platform.api.container import PlatformContainer, get_container
from trading_platform.api.schemas import (
    KillSwitchActionResponse,
    KillSwitchResetRequest,
    KillSwitchTriggerRequest,
    RiskDecisionLogResponse,
    RiskLimitMetric,
    RiskStatusResponse,
)
from trading_platform.core.events import SystemStatusEvent
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.models.risk import RiskDecisionAudit

logger = get_logger("api.risk")
router = APIRouter(prefix="/risk", tags=["Risk"])


@router.get("", response_model=RiskStatusResponse)
async def get_risk_status(
    container: PlatformContainer = Depends(get_container),
) -> RiskStatusResponse:
    """Retrieve pre-trade risk engine state, limits, utilization metrics, and audit history."""
    risk_engine = container.risk_engine
    pm = container.portfolio_manager
    config = container.config.risk
    strategy_id = container.strategy_id

    portfolio = pm.get_portfolio(strategy_id)

    # 1. Compute limit metrics
    # Max Position Size
    max_pos_val = 0.0
    for pos in pm.get_all_positions(strategy_id):
        if pos.notional > max_pos_val:
            max_pos_val = pos.notional

    pos_util = (
        (max_pos_val / config.max_position_size_usd) * 100.0
        if config.max_position_size_usd > 0
        else 0.0
    )

    # Max Portfolio Exposure
    exp_util = (
        (portfolio.total_exposure / config.max_portfolio_exposure_usd) * 100.0
        if config.max_portfolio_exposure_usd > 0
        else 0.0
    )

    # Max Leverage
    curr_lev = (portfolio.total_exposure / portfolio.equity) if portfolio.equity > 0 else 0.0
    lev_util = (curr_lev / config.max_leverage) * 100.0 if config.max_leverage > 0 else 0.0

    # Max Drawdown
    dd_util = (
        (portfolio.drawdown_pct / config.max_drawdown_pct) * 100.0
        if config.max_drawdown_pct > 0
        else 0.0
    )

    # 2. Fetch recent risk decision audit logs
    recent_decisions: list[RiskDecisionLogResponse] = []
    if container.session_factory:
        try:
            async with get_db_session(container.session_factory) as session:
                stmt = (
                    select(RiskDecisionAudit)
                    .where(RiskDecisionAudit.strategy_id == strategy_id)
                    .order_by(desc(RiskDecisionAudit.timestamp))
                    .limit(20)
                )
                res = await session.execute(stmt)
                for d in res.scalars().all():
                    recent_decisions.append(
                        RiskDecisionLogResponse(
                            signal_id=d.signal_id or "N/A",
                            strategy_id=d.strategy_id,
                            symbol=d.symbol,
                            decision=d.decision,
                            original_size=round(d.original_target_exposure, 4),
                            approved_size=round(d.approved_target_exposure, 4),
                            reject_reason=d.reason,
                            evaluated_at=d.timestamp,
                        )
                    )
        except Exception:
            pass

    return RiskStatusResponse(
        trading_enabled=risk_engine.trading_enabled,
        kill_switch_active=risk_engine.is_kill_switch_active,
        kill_switch_reason=risk_engine._kill_switch_reason
        if risk_engine.is_kill_switch_active
        else None,
        max_position_size_usd=RiskLimitMetric(
            current=round(max_pos_val, 2),
            limit=round(config.max_position_size_usd, 2),
            utilization_pct=round(min(pos_util, 100.0), 1),
            unit="USD",
        ),
        max_portfolio_exposure_usd=RiskLimitMetric(
            current=round(portfolio.total_exposure, 2),
            limit=round(config.max_portfolio_exposure_usd, 2),
            utilization_pct=round(min(exp_util, 100.0), 1),
            unit="USD",
        ),
        max_leverage=RiskLimitMetric(
            current=round(curr_lev, 2),
            limit=round(config.max_leverage, 2),
            utilization_pct=round(min(lev_util, 100.0), 1),
            unit="x",
        ),
        max_drawdown_pct=RiskLimitMetric(
            current=round(portfolio.drawdown_pct, 2),
            limit=round(config.max_drawdown_pct, 2),
            utilization_pct=round(min(dd_util, 100.0), 1),
            unit="%",
        ),
        recent_decisions=recent_decisions,
    )


@router.post("/kill-switch/trigger", response_model=KillSwitchActionResponse)
async def trigger_kill_switch(
    payload: KillSwitchTriggerRequest,
    container: PlatformContainer = Depends(get_container),
) -> KillSwitchActionResponse:
    """Manually activate emergency kill switch, halting all trade approvals in RiskEngine."""
    logger.warning(
        f"[KILL-SWITCH-API] Trigger request received: reason='{payload.reason}', confirm={payload.confirm}"
    )

    if not payload.confirm:
        logger.error(
            "[KILL-SWITCH-API] Trigger request rejected: 'confirm: true' was not provided."
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action requires explicit 'confirm: true' boolean confirmation.",
        )

    # 1. Trigger the actual pre-trade RiskEngine kill-switch
    container.risk_engine.trigger_kill_switch(payload.reason)
    logger.critical(
        f"[KILL-SWITCH-API] RiskEngine.trigger_kill_switch executed. Reason: {payload.reason}"
    )

    # 2. Publish SystemStatusEvent across EventBus
    await container.event_bus.publish(
        SystemStatusEvent(
            component="RiskEngine",
            status="HALTED",
            message=f"EMERGENCY KILL SWITCH TRIGGERED: {payload.reason}",
        )
    )

    return KillSwitchActionResponse(
        success=True,
        kill_switch_active=True,
        reason=payload.reason,
        timestamp=datetime.now(UTC),
    )


@router.post("/kill-switch/reset", response_model=KillSwitchActionResponse)
async def reset_kill_switch(
    payload: KillSwitchResetRequest,
    container: PlatformContainer = Depends(get_container),
) -> KillSwitchActionResponse:
    """Manually reset and disengage the emergency kill switch in RiskEngine."""
    logger.info(
        f"[KILL-SWITCH-API] Reset request received: reason='{payload.reason}', confirm={payload.confirm}"
    )

    if not payload.confirm:
        logger.error("[KILL-SWITCH-API] Reset request rejected: 'confirm: true' was not provided.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action requires explicit 'confirm: true' boolean confirmation.",
        )

    # 1. Reset the actual pre-trade RiskEngine kill-switch
    container.risk_engine.reset_kill_switch(payload.reason)
    logger.info(
        f"[KILL-SWITCH-API] RiskEngine.reset_kill_switch executed. Reason: {payload.reason}"
    )

    # 2. Publish SystemStatusEvent across EventBus
    await container.event_bus.publish(
        SystemStatusEvent(
            component="RiskEngine",
            status="ONLINE",
            message=f"Kill switch disengaged: {payload.reason}",
        )
    )

    return KillSwitchActionResponse(
        success=True,
        kill_switch_active=False,
        reason=payload.reason,
        timestamp=datetime.now(UTC),
    )
