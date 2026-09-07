"""Portfolio snapshot and account balance ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base, TimestampMixin, utc_now


class PortfolioSnapshot(Base):
    """Historical point-in-time snapshot of portfolio performance and risk metrics."""

    __tablename__ = "portfolio_snapshots"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    total_wallet_balance: Mapped[float] = mapped_column(Float, nullable=False)
    total_unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_funding_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    total_margin_balance: Mapped[float] = mapped_column(Float, nullable=False)
    total_initial_margin: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_maintenance_margin: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_exposure: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    effective_leverage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )

    __table_args__ = (Index("ix_portfolio_snapshots_strat_time", "strategy_id", "timestamp"),)

    def __repr__(self) -> str:
        return (
            f"<PortfolioSnapshot(strategy_id={self.strategy_id}, balance={self.total_wallet_balance}, "
            f"uPnL={self.total_unrealized_pnl}, exposure={self.total_exposure})>"
        )


class AccountBalance(Base, TimestampMixin):
    """Current asset balance per asset and strategy attribution."""

    __tablename__ = "account_balances"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    strategy_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    asset: Mapped[str] = mapped_column(String(20), nullable=False, default="USDT")
    wallet_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    available_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    locked_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    __table_args__ = (UniqueConstraint("strategy_id", "asset", name="uq_strategy_asset_balance"),)

    def __repr__(self) -> str:
        return (
            f"<AccountBalance(strategy={self.strategy_id}, asset={self.asset}, "
            f"wallet={self.wallet_balance}, available={self.available_balance})>"
        )
