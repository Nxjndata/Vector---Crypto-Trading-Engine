"""Candle (OHLCV) ORM model for TimescaleDB time-series market data."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base


class Candle(Base):
    """OHLCV candlestick data persisted to TimescaleDB hypertable."""

    __tablename__ = "candles"

    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(10), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    open_price: Mapped[float] = mapped_column(Float, nullable=False)
    high_price: Mapped[float] = mapped_column(Float, nullable=False)
    low_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    quote_volume: Mapped[float] = mapped_column(Float, nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (Index("ix_candles_symbol_tf_time", "symbol", "timeframe", "open_time"),)

    def __repr__(self) -> str:
        return (
            f"<Candle(symbol={self.symbol}, tf={self.timeframe}, time={self.open_time}, "
            f"O={self.open_price}, H={self.high_price}, L={self.low_price}, C={self.close_price}, V={self.volume})>"
        )
