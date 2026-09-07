"""Abstract exchange adapter interface definition."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from trading_platform.core.constants import OrderSide, OrderType, TimeInForce
from trading_platform.core.events import MarketDataEvent
from trading_platform.models.candle import Candle
from trading_platform.models.instrument import Instrument
from trading_platform.models.order import Order
from trading_platform.models.portfolio import AccountBalance
from trading_platform.models.position import Position


@dataclass
class OrderRequest:
    """Request payload for submitting a new order to an exchange."""

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    client_order_id: str | None = None
    strategy_id: str | None = None


# OrderResponse alias for platform standard Order model
OrderResponse = Order


class ExchangeAdapter(ABC):
    """Abstract interface for cryptocurrency exchange connectivity.

    This interface contains zero exchange-specific logic and is designed to be
    implemented across different exchanges (Binance, Bybit, OKX, etc.).
    """

    @abstractmethod
    async def get_markets(self) -> list[Instrument]:
        """Fetch all available trading markets and contract specifications."""
        pass

    @abstractmethod
    async def get_balance(self) -> list[AccountBalance]:
        """Fetch current account balances across assets."""
        pass

    @abstractmethod
    async def get_positions(self, symbols: list[str] | None = None) -> list[Position]:
        """Fetch active positions for given symbols or all open positions."""
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Fetch open unfilled/partially filled orders."""
        pass

    @abstractmethod
    async def get_order(
        self,
        symbol: str,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> Order:
        """Fetch details of a specific order by client or exchange order ID."""
        pass

    @abstractmethod
    async def get_historical_data(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        """Fetch historical OHLCV candlestick data."""
        pass

    @abstractmethod
    async def get_ticker(self, symbol: str) -> MarketDataEvent:
        """Fetch latest price ticker, mark price, and funding rate for a symbol."""
        pass

    @abstractmethod
    async def place_order(self, order_request: Order | OrderRequest) -> Order:
        """Submit a new order to the exchange."""
        pass

    @abstractmethod
    async def cancel_order(
        self,
        symbol: str,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> Order:
        """Cancel an open order."""
        pass

    async def set_margin_type(self, symbol: str, margin_type: str) -> bool:
        """Set symbol margin mode (ISOLATED or CROSSED). Default implementation returns True."""
        return True

    async def set_leverage(self, symbol: str, leverage: int) -> int:
        """Set symbol leverage multiplier. Default implementation returns leverage."""
        return leverage

    async def get_symbol_leverage_and_margin(self, symbol: str) -> dict[str, Any]:
        """Query current exchange ground-truth leverage and margin mode for symbol."""
        return {"symbol": symbol.upper(), "margin_type": "ISOLATED", "leverage": 5}

    @abstractmethod
    async def close(self) -> None:
        """Gracefully close HTTP/WebSocket connections and release resources."""
        pass
