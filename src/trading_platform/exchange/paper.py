"""Paper Trading Exchange Adapter and In-Memory Matching Engine Simulator."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trading_platform.core.constants import (
    ContractType,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from trading_platform.core.events import MarketDataEvent
from trading_platform.core.exceptions import OrderNotFoundError
from trading_platform.core.logging import get_logger
from trading_platform.exchange.adapter import ExchangeAdapter, OrderRequest
from trading_platform.models.candle import Candle
from trading_platform.models.instrument import Instrument
from trading_platform.models.order import Order
from trading_platform.models.portfolio import AccountBalance
from trading_platform.models.position import Position

logger = get_logger("exchange.paper")


class PaperExchangeAdapter(ExchangeAdapter):
    """Realistic in-memory matching engine simulating Binance USDT-M Perpetual Futures."""

    def __init__(
        self,
        instruments: list[Instrument] | None = None,
        initial_wallet_balance: float = 10000.0,
        slippage_bps: float = 1.0,  # 1.0 bps = 0.01%
        maker_fee_rate: float = 0.0002,  # 0.02%
        taker_fee_rate: float = 0.0005,  # 0.05%
    ) -> None:
        self.initial_balance = initial_wallet_balance
        self.wallet_balance = initial_wallet_balance
        self.slippage_bps = slippage_bps
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate

        # Default instruments if none provided
        self._instruments: dict[str, Instrument] = {
            inst.symbol: inst
            for inst in (
                instruments
                or [
                    Instrument(
                        symbol="BTCUSDT",
                        base_asset="BTC",
                        quote_asset="USDT",
                        contract_type=ContractType.PERPETUAL,
                        tick_size=Decimal("0.10"),
                        step_size=Decimal("0.001"),
                        min_qty=Decimal("0.001"),
                        min_notional=Decimal("5.0"),
                    ),
                    Instrument(
                        symbol="ETHUSDT",
                        base_asset="ETH",
                        quote_asset="USDT",
                        contract_type=ContractType.PERPETUAL,
                        tick_size=Decimal("0.01"),
                        step_size=Decimal("0.01"),
                        min_qty=Decimal("0.01"),
                        min_notional=Decimal("5.0"),
                    ),
                ]
            )
        }

        # Simulator State
        self._mark_prices: dict[str, float] = {
            "BTCUSDT": 60000.0,
            "ETHUSDT": 3000.0,
        }
        self._positions: dict[str, Position] = {}
        self._open_orders: dict[str, Order] = {}  # cid -> Order
        self._all_orders: dict[str, Order] = {}  # cid -> Order
        self._margin_modes: dict[str, str] = {}
        self._leverages: dict[str, int] = {}

    def set_mark_price(self, symbol: str, price: float) -> list[Order]:
        """Update market price in matching engine and evaluate open limit orders."""
        sym = symbol.upper()
        self._mark_prices[sym] = price
        newly_filled: list[Order] = []

        # Check and match pending limit orders for this symbol
        for cid, order in list(self._open_orders.items()):
            if order.symbol != sym:
                continue

            limit_price = order.price or price
            should_fill = False

            # BUY limit fills when price drops to or below limit price
            if order.side == OrderSide.BUY and price <= limit_price:
                should_fill = True
            # SELL limit fills when price rises to or above limit price
            elif order.side == OrderSide.SELL and price >= limit_price:
                should_fill = True

            if should_fill:
                fill_price = limit_price
                fee = order.quantity * fill_price * self.maker_fee_rate
                self._apply_fill_accounting(
                    symbol=sym,
                    side=order.side,
                    quantity=order.quantity,
                    price=fill_price,
                    fee=fee,
                )
                order.status = OrderStatus.FILLED
                order.filled_qty = order.quantity
                order.avg_fill_price = fill_price
                order.fee = fee
                order.updated_at = datetime.now(UTC)

                self._open_orders.pop(cid, None)
                newly_filled.append(order)
                logger.info(
                    f"[PAPER LIMIT FILL] {cid} filled at ${fill_price:,.2f} (qty={order.quantity})"
                )

        return newly_filled

    def _apply_fill_accounting(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        price: float,
        fee: float,
    ) -> None:
        """Internal simulator position and wallet balance accounting."""
        self.wallet_balance -= fee
        pos = self._positions.get(symbol)
        signed_qty = quantity if side == OrderSide.BUY else -quantity

        if not pos or abs(pos.size) < 1e-8:
            self._positions[symbol] = Position(
                strategy_id="paper_strat",
                symbol=symbol,
                size=signed_qty,
                entry_price=price,
                mark_price=price,
                margin_mode=MarginMode.ISOLATED,
            )
        else:
            old_size = pos.size
            old_entry = pos.entry_price

            # Same side -> scale in
            if (old_size > 0 and signed_qty > 0) or (old_size < 0 and signed_qty < 0):
                new_size = old_size + signed_qty
                new_entry = (abs(old_size) * old_entry + quantity * price) / abs(new_size)
                pos.size = new_size
                pos.entry_price = new_entry
            else:
                # Opposite side -> closing or flipping
                closed_qty = min(abs(old_size), quantity)
                realized_pnl = (
                    closed_qty * (price - old_entry)
                    if old_size > 0
                    else closed_qty * (old_entry - price)
                )
                self.wallet_balance += realized_pnl

                new_size = old_size + signed_qty
                pos.size = new_size
                pos.entry_price = price if abs(new_size) > 1e-8 else 0.0

    async def get_markets(self) -> list[Instrument]:
        """Fetch all available trading markets."""
        return list(self._instruments.values())

    async def get_balance(self) -> list[AccountBalance]:
        """Fetch simulated account balance."""
        return [
            AccountBalance(
                strategy_id="default",
                asset="USDT",
                wallet_balance=self.wallet_balance,
                available_balance=self.wallet_balance,
                locked_balance=0.0,
            )
        ]

    async def get_positions(self, symbols: list[str] | None = None) -> list[Position]:
        """Fetch active positions."""
        if symbols:
            return [p for s, p in self._positions.items() if s in symbols and abs(p.size) > 1e-8]
        return [p for p in self._positions.values() if abs(p.size) > 1e-8]

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Fetch open unfilled/partially filled orders."""
        if symbol:
            sym = symbol.upper()
            return [o for o in self._open_orders.values() if o.symbol == sym]
        return list(self._open_orders.values())

    async def get_order(
        self,
        symbol: str,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> Order:
        """Fetch order details from simulator history."""
        if client_order_id and client_order_id in self._all_orders:
            return self._all_orders[client_order_id]

        if exchange_order_id:
            for ord_obj in self._all_orders.values():
                if ord_obj.exchange_order_id == exchange_order_id:
                    return ord_obj

        raise OrderNotFoundError(
            f"Order '{client_order_id or exchange_order_id}' not found on simulated exchange."
        )

    async def get_historical_data(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        """Fetch simulated historical candlestick data."""
        mark = self._mark_prices.get(symbol.upper(), 60000.0)
        return [
            Candle(
                symbol=symbol.upper(),
                timeframe=interval,
                open=mark,
                high=mark * 1.002,
                low=mark * 0.998,
                close=mark,
                volume=10.0,
                timestamp=datetime.now(UTC),
            )
        ]

    async def get_ticker(self, symbol: str) -> MarketDataEvent:
        """Fetch latest price ticker."""
        mark = self._mark_prices.get(symbol.upper(), 60000.0)
        return MarketDataEvent(
            symbol=symbol.upper(),
            mark_price=mark,
            index_price=mark,
            funding_rate=0.0001,
            next_funding_time=datetime.now(UTC),
        )

    async def place_order(self, order_request: Order | OrderRequest) -> Order:
        """Process simulated order execution through matching engine."""
        sym = order_request.symbol.upper()
        cid = order_request.client_order_id or f"sim_c_{uuid.uuid4().hex[:10]}"
        ex_id = f"sim_ex_{uuid.uuid4().hex[:10]}"
        strat_id = order_request.strategy_id or "paper_strat"
        mark_price = self._mark_prices.get(sym, order_request.price or 60000.0)

        # 1. Market Orders: fill immediately at mark price + slippage (taker fee)
        if order_request.order_type == OrderType.MARKET:
            slippage_factor = self.slippage_bps / 10000.0
            fill_price = (
                mark_price * (1.0 + slippage_factor)
                if order_request.side == OrderSide.BUY
                else mark_price * (1.0 - slippage_factor)
            )
            fee = order_request.quantity * fill_price * self.taker_fee_rate

            self._apply_fill_accounting(
                symbol=sym,
                side=order_request.side,
                quantity=order_request.quantity,
                price=fill_price,
                fee=fee,
            )

            order = Order(
                client_order_id=cid,
                exchange_order_id=ex_id,
                strategy_id=strat_id,
                symbol=sym,
                side=order_request.side,
                order_type=order_request.order_type,
                time_in_force=order_request.time_in_force,
                price=order_request.price or mark_price,
                quantity=order_request.quantity,
                filled_qty=order_request.quantity,
                avg_fill_price=fill_price,
                status=OrderStatus.FILLED,
                fee=fee,
            )
            self._all_orders[cid] = order
            return order

        # 2. Limit Orders: check if immediately marketable or rest in open orders book
        elif order_request.order_type == OrderType.LIMIT:
            limit_price = order_request.price or mark_price
            is_marketable = (order_request.side == OrderSide.BUY and limit_price >= mark_price) or (
                order_request.side == OrderSide.SELL and limit_price <= mark_price
            )

            if is_marketable:
                # Immediate taker execution at market
                fee = order_request.quantity * mark_price * self.taker_fee_rate
                self._apply_fill_accounting(
                    symbol=sym,
                    side=order_request.side,
                    quantity=order_request.quantity,
                    price=mark_price,
                    fee=fee,
                )
                order = Order(
                    client_order_id=cid,
                    exchange_order_id=ex_id,
                    strategy_id=strat_id,
                    symbol=sym,
                    side=order_request.side,
                    order_type=order_request.order_type,
                    time_in_force=order_request.time_in_force,
                    price=limit_price,
                    quantity=order_request.quantity,
                    filled_qty=order_request.quantity,
                    avg_fill_price=mark_price,
                    status=OrderStatus.FILLED,
                    fee=fee,
                )
                self._all_orders[cid] = order
                return order
            else:
                # Rests on order book as ACKNOWLEDGED until price crosses
                order = Order(
                    client_order_id=cid,
                    exchange_order_id=ex_id,
                    strategy_id=strat_id,
                    symbol=sym,
                    side=order_request.side,
                    order_type=order_request.order_type,
                    time_in_force=order_request.time_in_force,
                    price=limit_price,
                    quantity=order_request.quantity,
                    filled_qty=0.0,
                    avg_fill_price=0.0,
                    status=OrderStatus.ACKNOWLEDGED,
                    fee=0.0,
                )
                self._open_orders[cid] = order
                self._all_orders[cid] = order
                return order

        # Default fallback
        order = Order(
            client_order_id=cid,
            exchange_order_id=ex_id,
            strategy_id=strat_id,
            symbol=sym,
            side=order_request.side,
            order_type=order_request.order_type,
            time_in_force=TimeInForce.GTC,
            quantity=order_request.quantity,
            status=OrderStatus.ACKNOWLEDGED,
        )
        self._open_orders[cid] = order
        self._all_orders[cid] = order
        return order

    async def cancel_order(
        self,
        symbol: str,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> Order:
        """Cancel an open order in the simulator."""
        if client_order_id and client_order_id in self._open_orders:
            order = self._open_orders.pop(client_order_id)
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now(UTC)
            return order

        if client_order_id and client_order_id in self._all_orders:
            order = self._all_orders[client_order_id]
            order.status = OrderStatus.CANCELLED
            return order

        raise OrderNotFoundError(
            f"Cannot cancel: order '{client_order_id or exchange_order_id}' not found."
        )

    async def set_margin_type(self, symbol: str, margin_type: str) -> bool:
        """Set symbol margin mode in simulator."""
        self._margin_modes[symbol.upper()] = margin_type.upper()
        return True

    async def set_leverage(self, symbol: str, leverage: int) -> int:
        """Set symbol leverage in simulator."""
        self._leverages[symbol.upper()] = int(leverage)
        return int(leverage)

    async def get_symbol_leverage_and_margin(self, symbol: str) -> dict[str, Any]:
        """Query symbol leverage and margin mode in simulator."""
        sym = symbol.upper()
        return {
            "symbol": sym,
            "margin_type": self._margin_modes.get(sym, "ISOLATED"),
            "leverage": self._leverages.get(sym, 5),
            "position_amt": self._positions[sym].quantity if sym in self._positions else 0.0,
        }

    async def close(self) -> None:
        """Gracefully release resources."""
        logger.info("PaperExchangeAdapter closed.")
