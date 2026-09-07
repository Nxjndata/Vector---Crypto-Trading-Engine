"""Trading signal ORM model with deduplication constraint."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.core.constants import SignalType
from trading_platform.db.base import Base, TimestampMixin, utc_now


class Signal(Base, TimestampMixin):
    """Trading signal generated exclusively by a Strategy.

    target_exposure represents the intended portfolio exposure expressed as a SIGNED FRACTION
    of total portfolio equity in the range [-1.0, 1.0].
    """

    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    signal_type: Mapped[SignalType] = mapped_column(Enum(SignalType), nullable=False)
    # Signed fraction of portfolio equity [-1.0, 1.0]
    target_exposure: Mapped[float] = mapped_column(Float, nullable=False)
    mark_price: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = (
        # Mandatory deduplication constraint: prevents duplicate signals on process restart
        UniqueConstraint(
            "strategy_id", "symbol", "timestamp", "signal_type", name="uq_signal_dedup"
        ),
        Index("ix_signals_strategy_symbol", "strategy_id", "symbol"),
    )

    def __repr__(self) -> str:
        return (
            f"<Signal(id={self.id}, strategy_id={self.strategy_id}, symbol={self.symbol}, "
            f"type={self.signal_type}, target_exposure={self.target_exposure:.4f})>"
        )
