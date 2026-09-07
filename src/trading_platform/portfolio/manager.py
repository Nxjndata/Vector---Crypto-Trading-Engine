"""Portfolio state machine and continuous PnL accounting engine."""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import OrderSide
from trading_platform.core.events import (
    EventBus,
    FillEvent,
    MarketDataEvent,
    PositionUpdateEvent,
)
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.models.portfolio import AccountBalance, PortfolioSnapshot
from trading_platform.models.position import Position
from trading_platform.portfolio.state import PortfolioState, PositionState

logger = get_logger("portfolio.manager")


class PortfolioManager:
    """Manages real-time portfolio state, multi-strategy positions, and PnL decomposition."""

    def __init__(
        self,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        default_deposit_usd: float = 10000.0,
    ) -> None:
        self.event_bus = event_bus
        self.session_factory = session_factory
        self.default_deposit_usd = default_deposit_usd

        # In-memory fast state:
        # _positions[strategy_id][symbol] -> PositionState
        self._positions: dict[str, dict[str, PositionState]] = defaultdict(dict)
        # _wallet_balances[strategy_id] -> float
        self._wallet_balances: dict[str, float] = defaultdict(lambda: self.default_deposit_usd)
        # _realized_pnls[strategy_id] -> cumulative realized PnL
        self._realized_pnls: dict[str, float] = defaultdict(float)
        # _funding_pnls[strategy_id] -> cumulative funding PnL
        self._funding_pnls: dict[str, float] = defaultdict(float)
        # _peak_equities[strategy_id] -> peak equity
        self._peak_equities: dict[str, float] = defaultdict(lambda: self.default_deposit_usd)
        # _latest_mark_prices[symbol] -> float
        self._latest_mark_prices: dict[str, float] = {}

        # Subscribe to EventBus
        self.event_bus.subscribe(MarketDataEvent, self.on_market_data_event)
        self.event_bus.subscribe(FillEvent, self.on_fill_event)

    def deposit(self, strategy_id: str, amount: float, asset: str = "USDT") -> None:
        """Add funds to a strategy's wallet balance."""
        self._wallet_balances[strategy_id] += amount
        curr_equity = self.get_portfolio(strategy_id).equity
        if curr_equity > self._peak_equities[strategy_id]:
            self._peak_equities[strategy_id] = curr_equity
        logger.info(
            f"Deposited ${amount:,.2f} {asset} for strategy '{strategy_id}'. New Wallet: ${self._wallet_balances[strategy_id]:,.2f}"
        )

    def get_position(self, strategy_id: str, symbol: str) -> PositionState:
        """Get or initialize position state for a strategy and symbol."""
        sym = symbol.upper()
        if sym not in self._positions[strategy_id]:
            mark = self._latest_mark_prices.get(sym, 0.0)
            self._positions[strategy_id][sym] = PositionState(
                strategy_id=strategy_id,
                symbol=sym,
                mark_price=mark,
            )
        return self._positions[strategy_id][sym]

    def get_all_positions(self, strategy_id: str) -> list[PositionState]:
        """Return all open positions for a given strategy."""
        return [p for p in self._positions[strategy_id].values() if p.is_open]

    def get_portfolio(self, strategy_id: str) -> PortfolioState:
        """Calculate and return the consolidated real-time portfolio state for a strategy."""
        wallet = self._wallet_balances[strategy_id]
        total_upnl = 0.0
        total_exposure = 0.0
        total_initial_margin = 0.0

        for pos in self._positions[strategy_id].values():
            if pos.is_open:
                total_upnl += pos.unrealized_pnl
                total_exposure += pos.notional
                total_initial_margin += pos.initial_margin

        peak = self._peak_equities[strategy_id]
        curr_equity = wallet + total_upnl
        if curr_equity > peak:
            self._peak_equities[strategy_id] = curr_equity
            peak = curr_equity

        return PortfolioState(
            strategy_id=strategy_id,
            wallet_balance=wallet,
            unrealized_pnl=total_upnl,
            realized_pnl=self._realized_pnls[strategy_id],
            funding_pnl=self._funding_pnls[strategy_id],
            total_exposure=total_exposure,
            total_initial_margin=total_initial_margin,
            peak_equity=peak,
        )

    async def on_market_data_event(self, event: MarketDataEvent) -> None:
        """Continuously update mark price and recalculate unrealized PnL."""
        symbol = event.symbol.upper()
        self._latest_mark_prices[symbol] = event.mark_price

        # Update all positions holding this symbol across all strategies
        for strat_id, pos_map in self._positions.items():
            if symbol in pos_map:
                pos = pos_map[symbol]
                if pos.is_open:
                    pos.recalculate_unrealized_pnl(event.mark_price)
                    # Update peak equity if necessary
                    port = self.get_portfolio(strat_id)
                    if port.equity > self._peak_equities[strat_id]:
                        self._peak_equities[strat_id] = port.equity

    async def on_fill_event(self, event: FillEvent) -> None:
        """Handle execution fill and apply position and accounting changes."""
        await self.apply_fill(event)

    async def apply_fill(self, fill: FillEvent) -> PositionState:
        """Process an execution fill, updating position size, entry price, and realized PnL.

        Accounting logic:
        1. Scale-in (same direction): weighted average entry price. Realized PnL = 0.
        2. Scale-out / Close (opposite direction): Realized PnL calculated and credited to wallet.
        3. Position Flip: Realized PnL calculated on closed portion; remaining qty opens opposite position at fill price.
        """
        strat_id = fill.strategy_id
        symbol = fill.symbol.upper()
        pos = self.get_position(strat_id, symbol)

        fill_qty = fill.quantity
        fill_price = fill.price
        fill_fee = fill.fee
        signed_fill_qty = fill_qty if fill.side == OrderSide.BUY else -fill_qty

        old_size = pos.size
        old_entry = pos.entry_price
        pnl_delta = 0.0

        # Case 1: Position was flat
        if not pos.is_open:
            new_size = signed_fill_qty
            new_entry = fill_price
            pnl_delta = -fill_fee  # Fee deducted

        # Case 2: Adding to existing position in same direction
        elif (old_size > 0 and signed_fill_qty > 0) or (old_size < 0 and signed_fill_qty < 0):
            new_size = old_size + signed_fill_qty
            new_entry = (abs(old_size) * old_entry + fill_qty * fill_price) / abs(new_size)
            pnl_delta = -fill_fee

        # Case 3: Reducing, closing, or flipping position
        else:
            old_abs = abs(old_size)
            # Partial or full close
            if fill_qty <= old_abs:
                closed_qty = fill_qty
                new_size = old_size + signed_fill_qty
                new_entry = old_entry if abs(new_size) > 1e-8 else 0.0

                if old_size > 0:  # Closing LONG
                    gross_pnl = closed_qty * (fill_price - old_entry)
                else:  # Closing SHORT
                    gross_pnl = closed_qty * (old_entry - fill_price)

                pnl_delta = gross_pnl - fill_fee

            # Position flip (e.g. was +1.0 BTC, sold 2.0 BTC -> now -1.0 BTC)
            else:
                closed_qty = old_abs
                remaining_qty = fill_qty - closed_qty

                if old_size > 0:  # Closing LONG portion
                    gross_pnl = closed_qty * (fill_price - old_entry)
                else:  # Closing SHORT portion
                    gross_pnl = closed_qty * (old_entry - fill_price)

                pnl_delta = gross_pnl - fill_fee
                new_size = remaining_qty if fill.side == OrderSide.BUY else -remaining_qty
                new_entry = fill_price

        # Update position state
        pos.size = new_size
        pos.entry_price = new_entry
        pos.realized_pnl += pnl_delta
        pos.updated_at = datetime.now(UTC)
        mark = self._latest_mark_prices.get(symbol, fill_price)
        pos.recalculate_unrealized_pnl(mark)

        # Update portfolio accumulators
        self._realized_pnls[strat_id] += pnl_delta
        self._wallet_balances[strat_id] += pnl_delta

        # Persist position and balance atomically to PostgreSQL
        await self._persist_state_atomic(strat_id, symbol)

        # Emit PositionUpdateEvent
        await self.event_bus.publish(
            PositionUpdateEvent(
                strategy_id=strat_id,
                symbol=symbol,
                size=pos.size,
                entry_price=pos.entry_price,
                mark_price=pos.mark_price,
                liquidation_price=pos.liquidation_price,
                leverage=pos.leverage,
                unrealized_pnl=pos.unrealized_pnl,
                margin_mode=pos.margin_mode,
            )
        )

        logger.info(
            f"[FILL PROCESSED] Strat: {strat_id} | Sym: {symbol} | Side: {fill.side.value} | "
            f"Qty: {fill_qty} | Price: ${fill_price:,.2f} | New Size: {pos.size} | "
            f"Entry: ${pos.entry_price:,.2f} | Realized PnL Delta: ${pnl_delta:+,.2f} | "
            f"Wallet: ${self._wallet_balances[strat_id]:,.2f}"
        )

        return pos

    async def resync_wallet_balance(self, strategy_id: str, new_wallet_balance: float) -> None:
        """Correct local wallet balance to match exchange ground-truth during reconciliation."""
        old_balance = self._wallet_balances[strategy_id]
        self._wallet_balances[strategy_id] = new_wallet_balance
        logger.info(
            f"[RESYNC BALANCE] Strat: {strategy_id} | Old: ${old_balance:,.2f} -> New: ${new_wallet_balance:,.2f}"
        )
        # Persist balance to DB
        await self._persist_state_atomic(strategy_id, symbol="BTCUSDT")

    async def resync_position(
        self,
        strategy_id: str,
        symbol: str,
        size: float,
        entry_price: float,
        mark_price: float,
        leverage: float = 1.0,
    ) -> PositionState:
        """Correct local position state to match exchange ground-truth during reconciliation."""
        pos = self.get_position(strategy_id, symbol)
        pos.size = size
        pos.entry_price = entry_price
        pos.leverage = leverage
        pos.recalculate_unrealized_pnl(mark_price)
        pos.updated_at = datetime.now(UTC)
        await self._persist_state_atomic(strategy_id, symbol)
        logger.info(
            f"[RESYNC POSITION] Strat: {strategy_id} | Sym: {symbol} | Size: {size} | Entry: ${entry_price:,.2f}"
        )
        return pos

    async def apply_funding_payment(
        self,
        strategy_id: str,
        symbol: str,
        funding_rate: float,
        mark_price: float,
    ) -> float:
        """Apply a periodic funding settlement payment to an open position.

        Equation: payment = -(size * mark_price * funding_rate)
        (Longs pay shorts when funding_rate > 0; Shorts pay longs when funding_rate < 0)
        """
        pos = self.get_position(strategy_id, symbol)
        if not pos.is_open:
            return 0.0

        payment = -(pos.size * mark_price * funding_rate)
        pos.funding_pnl += payment
        self._funding_pnls[strategy_id] += payment
        self._wallet_balances[strategy_id] += payment

        await self._persist_state_atomic(strategy_id, symbol)

        logger.info(
            f"[FUNDING SETTLED] Strat: {strategy_id} | Sym: {symbol} | Rate: {funding_rate:.6f} | "
            f"Payment: ${payment:+,.4f} | Total Funding PnL: ${self._funding_pnls[strategy_id]:+,.4f}"
        )
        return payment

    async def create_snapshot(
        self,
        strategy_id: str,
        timestamp: datetime | None = None,
    ) -> PortfolioSnapshot:
        """Capture and persist a complete point-in-time portfolio snapshot for equity curve analytics."""
        port = self.get_portfolio(strategy_id)
        ts = timestamp or datetime.now(UTC)

        effective_leverage = port.total_exposure / port.equity if port.equity > 0 else 0.0

        snapshot = PortfolioSnapshot(
            strategy_id=strategy_id,
            total_wallet_balance=port.wallet_balance,
            total_unrealized_pnl=port.unrealized_pnl,
            total_realized_pnl=port.realized_pnl,
            total_funding_pnl=port.funding_pnl,
            total_margin_balance=port.margin_balance,
            total_initial_margin=port.total_initial_margin,
            total_maintenance_margin=port.total_initial_margin * 0.5,  # Maintenance threshold
            total_exposure=port.total_exposure,
            effective_leverage=effective_leverage,
            timestamp=ts,
        )

        async with get_db_session(self.session_factory) as session:
            session.add(snapshot)

        logger.debug(
            f"Saved PortfolioSnapshot for {strategy_id} at {ts.isoformat()} (Equity: ${port.equity:,.2f})"
        )
        return snapshot

    async def _persist_state_atomic(self, strategy_id: str, symbol: str) -> None:
        """Persist position state and account balance in a single atomic database transaction."""
        if not self.session_factory:
            return

        pos = self.get_position(strategy_id, symbol)
        wallet = self._wallet_balances[strategy_id]
        port = self.get_portfolio(strategy_id)

        try:
            async with get_db_session(self.session_factory) as session:
                # 1. Upsert Position
                res_pos = await session.execute(
                    select(Position).where(
                        Position.strategy_id == strategy_id,
                        Position.symbol == symbol,
                    )
                )
                db_pos = res_pos.scalar_one_or_none()
                if not db_pos:
                    db_pos = Position(
                        strategy_id=strategy_id,
                        symbol=symbol,
                        size=pos.size,
                        entry_price=pos.entry_price,
                        mark_price=pos.mark_price,
                        liquidation_price=pos.liquidation_price,
                        leverage=pos.leverage,
                        unrealized_pnl=pos.unrealized_pnl,
                        realized_pnl=pos.realized_pnl,
                        funding_pnl=pos.funding_pnl,
                        margin_mode=pos.margin_mode,
                    )
                    session.add(db_pos)
                else:
                    db_pos.size = pos.size
                    db_pos.entry_price = pos.entry_price
                    db_pos.mark_price = pos.mark_price
                    db_pos.liquidation_price = pos.liquidation_price
                    db_pos.leverage = pos.leverage
                    db_pos.unrealized_pnl = pos.unrealized_pnl
                    db_pos.realized_pnl = pos.realized_pnl
                    db_pos.funding_pnl = pos.funding_pnl
                    db_pos.margin_mode = pos.margin_mode

                # 2. Upsert Account Balance
                res_bal = await session.execute(
                    select(AccountBalance).where(
                        AccountBalance.strategy_id == strategy_id,
                        AccountBalance.asset == "USDT",
                    )
                )
                db_bal = res_bal.scalar_one_or_none()
                if not db_bal:
                    db_bal = AccountBalance(
                        strategy_id=strategy_id,
                        asset="USDT",
                        wallet_balance=wallet,
                        available_balance=port.available_balance,
                        locked_balance=port.total_initial_margin,
                    )
                    session.add(db_bal)
                else:
                    db_bal.wallet_balance = wallet
                    db_bal.available_balance = port.available_balance
                    db_bal.locked_balance = port.total_initial_margin

        except Exception as e:
            logger.error(
                f"Failed to persist state atomically for {strategy_id} / {symbol}: {e}",
                exc_info=True,
            )

    async def restore_from_db(self) -> None:
        """Fully restore portfolio and position states from PostgreSQL on process startup."""
        logger.info("Restoring portfolio and position state from PostgreSQL database...")

        async with get_db_session(self.session_factory) as session:
            # 1. Restore balances
            res_bal = await session.execute(select(AccountBalance))
            balances = res_bal.scalars().all()
            for b in balances:
                self._wallet_balances[b.strategy_id] = b.wallet_balance

            # 2. Restore positions
            res_pos = await session.execute(select(Position))
            positions = res_pos.scalars().all()
            for p in positions:
                pos_state = PositionState(
                    strategy_id=p.strategy_id,
                    symbol=p.symbol,
                    size=p.size,
                    entry_price=p.entry_price,
                    mark_price=p.mark_price,
                    liquidation_price=p.liquidation_price,
                    leverage=p.leverage,
                    unrealized_pnl=p.unrealized_pnl,
                    realized_pnl=p.realized_pnl,
                    funding_pnl=p.funding_pnl,
                    margin_mode=p.margin_mode,
                )
                self._positions[p.strategy_id][p.symbol] = pos_state
                self._realized_pnls[p.strategy_id] += p.realized_pnl
                self._funding_pnls[p.strategy_id] += p.funding_pnl

            # 3. Restore peak equities
            res_snaps = await session.execute(select(PortfolioSnapshot))
            snapshots = res_snaps.scalars().all()
            for snap in snapshots:
                snap_equity = snap.total_wallet_balance + snap.total_unrealized_pnl
                if snap_equity > self._peak_equities[snap.strategy_id]:
                    self._peak_equities[snap.strategy_id] = snap_equity

            # Also check restored current equity vs peak equity
            for strat_id in list(self._wallet_balances.keys()):
                port = self.get_portfolio(strat_id)
                if port.equity > self._peak_equities[strat_id]:
                    self._peak_equities[strat_id] = port.equity

        logger.info(
            f"State restored: {len(self._wallet_balances)} strategies, "
            f"{sum(len(v) for v in self._positions.values())} total positions loaded."
        )

    @property
    def metrics(self) -> dict[str, Any]:
        """Return diagnostic metrics."""
        return {
            "active_strategies": list(self._wallet_balances.keys()),
            "strategies_portfolios": {
                strat: {
                    "wallet_balance": self._wallet_balances[strat],
                    "equity": self.get_portfolio(strat).equity,
                    "drawdown_pct": self.get_portfolio(strat).drawdown_pct,
                    "open_positions": len(self.get_all_positions(strat)),
                }
                for strat in self._wallet_balances
            },
        }
