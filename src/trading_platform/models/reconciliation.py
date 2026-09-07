"""Reconciliation event ORM model."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.core.constants import ReconciliationEventType
from trading_platform.db.base import Base, utc_now


class ReconciliationEvent(Base):
    """Audit log for state discrepancies discovered between local DB and exchange."""

    __tablename__ = "reconciliation_events"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    event_type: Mapped[ReconciliationEventType] = mapped_column(
        Enum(ReconciliationEventType),
        nullable=False,
        index=True,
    )
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)

    local_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    remote_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    discrepancy_details: Mapped[str] = mapped_column(Text, nullable=False)
    action_taken: Mapped[str] = mapped_column(Text, nullable=False)

    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )

    __table_args__ = (Index("ix_reconciliation_type_time", "event_type", "timestamp"),)

    def __repr__(self) -> str:
        return (
            f"<ReconciliationEvent(id={self.id}, type={self.event_type}, "
            f"symbol={self.symbol}, resolved={self.is_resolved})>"
        )
