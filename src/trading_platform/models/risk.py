"""Risk decision audit ORM model."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.core.constants import RiskDecisionType
from trading_platform.db.base import Base, utc_now


class RiskDecisionAudit(Base):
    """Immutable audit trail of all risk engine decisions."""

    __tablename__ = "risk_decision_audit"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    signal_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("signals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    decision: Mapped[RiskDecisionType] = mapped_column(
        Enum(RiskDecisionType),
        nullable=False,
        index=True,
    )
    original_target_exposure: Mapped[float] = mapped_column(Float, nullable=False)
    approved_target_exposure: Mapped[float] = mapped_column(Float, nullable=False)

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    rule_triggered: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    snapshot_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )

    __table_args__ = (Index("ix_risk_audit_strat_time", "strategy_id", "timestamp"),)

    def __repr__(self) -> str:
        return (
            f"<RiskDecisionAudit(id={self.id}, decision={self.decision}, "
            f"strategy={self.strategy_id}, symbol={self.symbol}, reason='{self.reason}')>"
        )
