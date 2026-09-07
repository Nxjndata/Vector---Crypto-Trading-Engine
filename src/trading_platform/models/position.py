"""Position state ORM model."""

import uuid

from sqlalchemy import Enum, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.core.constants import MarginMode
from trading_platform.db.base import Base, TimestampMixin


class Position(Base, TimestampMixin):
    """Current position state tracked per strategy and symbol."""

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # Position size: > 0 for LONG, < 0 for SHORT, == 0 for FLAT
    size: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    mark_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    liquidation_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    leverage: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # PnL accounting
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    funding_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    margin_mode: Mapped[MarginMode] = mapped_column(
        Enum(MarginMode),
        default=MarginMode.ISOLATED,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("strategy_id", "symbol", name="uq_strategy_symbol_position"),
        Index("ix_positions_strategy_symbol", "strategy_id", "symbol"),
    )

    def __repr__(self) -> str:
        return (
            f"<Position(strategy_id={self.strategy_id}, symbol={self.symbol}, "
            f"size={self.size}, entry={self.entry_price}, mark={self.mark_price}, uPnL={self.unrealized_pnl})>"
        )
