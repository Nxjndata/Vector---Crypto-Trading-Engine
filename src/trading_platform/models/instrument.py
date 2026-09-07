"""Instrument metadata ORM model."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.core.constants import ContractType
from trading_platform.db.base import Base, TimestampMixin


class Instrument(Base, TimestampMixin):
    """Normalized perpetual futures instrument specifications."""

    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(20), nullable=False, default="USDT")
    contract_type: Mapped[ContractType] = mapped_column(
        Enum(ContractType),
        default=ContractType.PERPETUAL,
        nullable=False,
    )

    # Precision and constraints
    tick_size: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    step_size: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    min_qty: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    min_notional: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)

    # Fees
    maker_fee: Mapped[Decimal] = mapped_column(Numeric(8, 6), default=Decimal("0.0002"))
    taker_fee: Mapped[Decimal] = mapped_column(Numeric(8, 6), default=Decimal("0.0005"))

    # Perpetual specific metadata
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 8), nullable=True)
    next_funding_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Instrument(symbol={self.symbol}, tick_size={self.tick_size}, step_size={self.step_size})>"
