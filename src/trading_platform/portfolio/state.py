"""In-memory domain models for fast position and portfolio accounting."""

from dataclasses import dataclass
from datetime import UTC, datetime

from trading_platform.core.constants import MarginMode


@dataclass
class PositionState:
    """Current position state for a single strategy and symbol under isolated margin."""

    strategy_id: str
    symbol: str
    size: float = 0.0  # > 0 for LONG, < 0 for SHORT, == 0 for FLAT
    entry_price: float = 0.0
    mark_price: float = 0.0
    liquidation_price: float | None = None
    leverage: float = 1.0
    maintenance_margin_rate: float = 0.005  # 0.5% default maintenance margin
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    funding_pnl: float = 0.0
    margin_mode: MarginMode = MarginMode.ISOLATED
    updated_at: datetime = datetime.now(UTC)

    @property
    def is_open(self) -> bool:
        """True if position holds non-zero size."""
        return abs(self.size) > 1e-8

    @property
    def notional(self) -> float:
        """Total current notional value of the position at mark price."""
        return abs(self.size) * self.mark_price

    @property
    def initial_margin(self) -> float:
        """Initial margin locked for this isolated position based on entry value."""
        if not self.is_open or self.leverage <= 0:
            return 0.0
        return (abs(self.size) * self.entry_price) / self.leverage

    @property
    def maintenance_margin(self) -> float:
        """Minimum maintenance margin required to prevent liquidation."""
        if not self.is_open:
            return 0.0
        return self.notional * self.maintenance_margin_rate

    @property
    def isolated_margin_balance(self) -> float:
        """Total margin balance dedicated to this isolated position: Initial Margin + Unrealized PnL."""
        if not self.is_open:
            return 0.0
        return self.initial_margin + self.unrealized_pnl

    @property
    def available_margin(self) -> float:
        """Remaining isolated margin buffer for this position before reaching maintenance threshold."""
        if not self.is_open:
            return 0.0
        return max(0.0, self.isolated_margin_balance - self.maintenance_margin)

    @property
    def calculated_liquidation_price(self) -> float | None:
        """Estimated liquidation price based on isolated margin and maintenance margin rate."""
        if not self.is_open or self.entry_price <= 0 or self.leverage <= 0:
            return None

        # Long: entry * (1 - 1/leverage + MMR)
        if self.size > 0:
            return self.entry_price * (1.0 - (1.0 / self.leverage) + self.maintenance_margin_rate)
        # Short: entry * (1 + 1/leverage - MMR)
        else:
            return self.entry_price * (1.0 + (1.0 / self.leverage) - self.maintenance_margin_rate)

    @property
    def liquidation_distance_pct(self) -> float:
        """Distance from current mark price to liquidation price as a percentage.

        Long:  (mark_price - liquidation_price) / mark_price * 100
        Short: (liquidation_price - mark_price) / mark_price * 100
        """
        liq_price = self.liquidation_price or self.calculated_liquidation_price
        if not self.is_open or not liq_price or self.mark_price <= 0:
            return 100.0

        if self.size > 0:
            return max(0.0, (self.mark_price - liq_price) / self.mark_price * 100.0)
        else:
            return max(0.0, (liq_price - self.mark_price) / self.mark_price * 100.0)

    def recalculate_unrealized_pnl(self, current_mark_price: float) -> float:
        """Update mark price and calculate unrealized PnL.

        Equation: uPnL = size * (mark_price - entry_price)
        (Works identically for Long: size > 0 and Short: size < 0)
        """
        self.mark_price = current_mark_price
        if not self.is_open:
            self.unrealized_pnl = 0.0
            self.liquidation_price = None
        else:
            self.unrealized_pnl = self.size * (self.mark_price - self.entry_price)
            self.liquidation_price = self.calculated_liquidation_price
        return self.unrealized_pnl


@dataclass
class PortfolioState:
    """Consolidated point-in-time state of a strategy's portfolio under isolated margin."""

    strategy_id: str
    wallet_balance: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    funding_pnl: float = 0.0
    total_exposure: float = 0.0
    total_initial_margin: float = 0.0
    peak_equity: float = 0.0

    @property
    def equity(self) -> float:
        """Total account equity = Wallet Balance + Unrealized PnL."""
        return self.wallet_balance + self.unrealized_pnl

    @property
    def available_balance(self) -> float:
        """Unallocated wallet cash free to open new positions under isolated margin.

        In isolated margin, an unrealized loss on an existing position is contained entirely
        within that position's isolated margin balance and does NOT drain the unallocated wallet cash.
        """
        return max(0.0, self.wallet_balance - self.total_initial_margin)

    @property
    def margin_balance(self) -> float:
        """Margin balance supporting existing positions."""
        return self.equity

    @property
    def drawdown_pct(self) -> float:
        """Current peak-to-current equity drawdown percentage."""
        if self.peak_equity <= 0:
            return 0.0
        dd = (self.peak_equity - self.equity) / self.peak_equity * 100.0
        return max(0.0, dd)
