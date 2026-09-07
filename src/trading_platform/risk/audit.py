"""Immutable audit logger for all risk decisions."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import RiskDecisionType
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.models.risk import RiskDecisionAudit

logger = get_logger("risk.audit")


class RiskAuditLogger:
    """Persists immutable audit log records for every pre-trade risk evaluation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self.session_factory = session_factory

    async def log_decision(
        self,
        signal_id: str | None,
        strategy_id: str,
        symbol: str,
        decision: RiskDecisionType,
        original_target_exposure: float,
        approved_target_exposure: float,
        reason: str,
        rules_triggered: list[str] | str | None = None,
        snapshot_data: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> RiskDecisionAudit | None:
        """Create and persist an immutable RiskDecisionAudit row."""
        ts = timestamp or datetime.now(UTC)
        rule_str = (
            ",".join(rules_triggered)
            if isinstance(rules_triggered, list)
            else (rules_triggered or "")
        )

        audit_entry = RiskDecisionAudit(
            signal_id=signal_id,
            strategy_id=strategy_id,
            symbol=symbol.upper(),
            decision=decision,
            original_target_exposure=original_target_exposure,
            approved_target_exposure=approved_target_exposure,
            reason=reason,
            rule_triggered=rule_str[:100] if rule_str else None,
            snapshot_data=snapshot_data or {},
            timestamp=ts,
        )

        if self.session_factory:
            try:
                async with get_db_session(self.session_factory) as session:
                    session.add(audit_entry)
            except Exception as e:
                logger.error(
                    f"Failed to persist risk decision audit to database: {e}", exc_info=True
                )

        logger.info(
            f"[RISK AUDIT: {decision.value}] Strategy: {strategy_id} | Symbol: {symbol} | "
            f"Orig Exp: {original_target_exposure * 100:+.1f}% | Appr Exp: {approved_target_exposure * 100:+.1f}% | "
            f"Rules: {rule_str or 'NONE'} | Reason: {reason}"
        )
        return audit_entry
