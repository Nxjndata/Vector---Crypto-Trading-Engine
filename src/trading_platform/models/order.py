"""Order, OrderEventLog, and Fill ORM models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trading_platform.core.constants import OrderSide, OrderStatus, OrderType, TimeInForce
from trading_platform.db.base import Base, TimestampMixin, utc_now


class Order(Base, TimestampMixin):
    """Full lifecycle tracking for exchange and paper orders."""

    __tablename__ = "orders"

    client_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType), nullable=False)
    time_in_force: Mapped[TimeInForce] = mapped_column(
        Enum(TimeInForce),
        default=TimeInForce.GTC,
        nullable=False,
    )

    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus),
        default=OrderStatus.CREATED,
        nullable=False,
        index=True,
    )

    filled_qty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_fill_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cum_quote: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fee_asset: Mapped[str] = mapped_column(String(20), default="USDT", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships (lazy="selectin" for async SQLAlchemy compatibility)
    events: Mapped[list["OrderEventLog"]] = relationship(
        "OrderEventLog",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    fills: Mapped[list["Fill"]] = relationship(
        "Fill",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Order(client_order_id={self.client_order_id}, symbol={self.symbol}, "
            f"side={self.side}, status={self.status}, qty={self.quantity})>"
        )


class OrderEventLog(Base):
    """Immutable audit trail for order state transitions."""

    __tablename__ = "order_event_logs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    client_order_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("orders.client_order_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), nullable=False)
    filled_qty_delta: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )

    # Relationship
    order: Mapped["Order"] = relationship("Order", back_populates="events", lazy="selectin")


class Fill(Base):
    """Individual trade execution fill."""

    __tablename__ = "fills"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    client_order_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("orders.client_order_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    exchange_trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fee_asset: Mapped[str] = mapped_column(String(20), default="USDT", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )

    # Relationship
    order: Mapped["Order"] = relationship("Order", back_populates="fills", lazy="selectin")

    __table_args__ = (Index("ix_fills_strategy_symbol", "strategy_id", "symbol"),)
