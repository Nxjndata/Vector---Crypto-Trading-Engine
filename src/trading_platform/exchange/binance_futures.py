"""Binance USDT-M Perpetual Futures concrete ExchangeAdapter implementation."""

import hashlib
import hmac
import urllib.parse
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from trading_platform.core.clock import get_synchronized_timestamp_ms
from trading_platform.core.config import AppConfig
from trading_platform.core.constants import (
    ContractType,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
    TradingMode,
)
from trading_platform.core.events import MarketDataEvent
from trading_platform.core.exceptions import (
    ClockDriftError,
    ExchangeConnectionError,
    InsufficientMarginError,
    OrderNotFoundError,
    OrderValidationError,
    PlatformException,
    RateLimitExceededError,
)
from trading_platform.core.logging import get_logger
from trading_platform.exchange.adapter import ExchangeAdapter, OrderRequest
from trading_platform.exchange.rate_limiter import BinanceRateLimiter
from trading_platform.models.candle import Candle
from trading_platform.models.instrument import Instrument
from trading_platform.models.order import Order
from trading_platform.models.portfolio import AccountBalance
from trading_platform.models.position import Position

logger = get_logger("exchange.binance_futures")

# Endpoint weight estimates for USDT-M Futures
ENDPOINT_WEIGHTS = {
    "exchangeInfo": 1,
    "balance": 5,
    "account": 5,
    "positionRisk": 5,
    "openOrders_single": 1,
    "openOrders_all": 40,
    "order_get": 1,
    "order_post": 0,  # 0 IP weight, counts against order rate limit
    "order_delete": 1,
    "klines": 5,
    "premiumIndex": 1,
}


class BinanceFuturesAdapter(ExchangeAdapter):
    """Concrete implementation of ExchangeAdapter for Binance USDT-M Perpetual Futures."""

    TESTNET_BASE_URL = "https://testnet.binancefuture.com"
    PRODUCTION_BASE_URL = "https://fapi.binance.com"

    def __init__(
        self,
        config: AppConfig,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: BinanceRateLimiter | None = None,
    ) -> None:
        self.config = config
        self.strategy_id = config.strategy_id
        self.environment = config.environment

        # Select Base URL: default to Testnet unless LIVE mode is explicitly active
        if self.environment == TradingMode.LIVE and config.live_trading_enabled:
            self.base_url = self.PRODUCTION_BASE_URL
            self.api_key = config.exchange.live_api_key
            self.api_secret = config.exchange.live_api_secret
            logger.info("BinanceFuturesAdapter initialized in LIVE mode (https://fapi.binance.com)")
        else:
            self.base_url = self.TESTNET_BASE_URL
            self.api_key = config.exchange.testnet_api_key
            self.api_secret = config.exchange.testnet_api_secret
            logger.info(
                f"BinanceFuturesAdapter initialized in {self.environment.value} mode (Testnet: {self.base_url})"
            )

        self.recv_window = config.exchange.recv_window
        self.rate_limiter = rate_limiter or BinanceRateLimiter()

        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
            headers={"User-Agent": "ModularTradingPlatform/0.1.0"},
        )
        self._owns_client = http_client is None

    async def close(self) -> None:
        """Close underlying HTTP client."""
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug("BinanceFuturesAdapter HTTP client closed.")

    def _sign_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign request parameters using HMAC-SHA256 with drift-corrected timestamp."""
        payload = params.copy()
        payload["timestamp"] = get_synchronized_timestamp_ms()
        payload["recvWindow"] = self.recv_window

        # Encode query string
        query_string = urllib.parse.urlencode(payload)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["signature"] = signature
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        estimated_weight: int = 1,
        is_order: bool = False,
    ) -> Any:
        """Execute HTTP request with rate limit checks, error mapping, and header tracking."""
        # 1. Proactive Rate Limiting Check
        await self.rate_limiter.acquire(estimated_weight=estimated_weight, is_order=is_order)

        request_params = params.copy() if params else {}
        headers: dict[str, str] = {}

        if signed:
            if not self.api_key or not self.api_secret:
                raise ExchangeConnectionError(
                    "API Key and Secret must be configured for signed requests"
                )
            headers["X-MBX-APIKEY"] = self.api_key
            request_params = self._sign_payload(request_params)
        elif self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        try:
            if method.upper() == "GET":
                response = await self._client.get(path, params=request_params, headers=headers)
            elif method.upper() == "POST":
                response = await self._client.post(path, params=request_params, headers=headers)
            elif method.upper() == "DELETE":
                response = await self._client.delete(path, params=request_params, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            # 2. Update real rate limit counters from response headers
            self.rate_limiter.update_from_headers(response.headers)

            if response.status_code >= 400:
                self._handle_error_response(response)

            return response.json()

        except httpx.RequestError as e:
            msg = f"Network communication failure connecting to Binance ({method} {path}): {e}"
            logger.error(msg)
            raise ExchangeConnectionError(msg, {"error": str(e)}) from e

    def _handle_error_response(self, response: httpx.Response) -> None:
        """Translate Binance error JSON and HTTP status codes into typed platform exceptions."""
        try:
            error_data = response.json()
            code = error_data.get("code")
            msg = error_data.get("msg", response.text)
        except Exception:
            code = response.status_code
            msg = response.text

        log_msg = f"Binance API error (HTTP {response.status_code}, code {code}): {msg}"
        logger.error(log_msg)

        # Map error codes
        if code == -1021:  # INVALID_TIMESTAMP
            raise ClockDriftError(
                f"Binance timestamp skew rejection: {msg}", {"code": code, "msg": msg}
            )

        if code in (-1003, -4003) or response.status_code == 429:  # RATE_LIMIT / IP_BANNED
            raise RateLimitExceededError(
                f"Binance rate limit violated: {msg}", {"code": code, "msg": msg}
            )

        if code in (-2010, -2019):  # NEW_ORDER_REJECTED / MARGIN_NOT_SUFFICIENT
            raise InsufficientMarginError(
                f"Insufficient margin for order: {msg}", {"code": code, "msg": msg}
            )

        if code in (-1013, -1102, -1111):  # FILTER_FAILURE / INVALID_QTY / INVALID_PRICE
            raise OrderValidationError(
                f"Order parameter validation rejected by Binance: {msg}", {"code": code, "msg": msg}
            )

        if code == -2011:  # UNKNOWN_ORDER
            raise OrderNotFoundError(
                f"Order not found on Binance: {msg}", {"code": code, "msg": msg}
            )

        if response.status_code >= 500 or code in (-1000, -1001):
            raise ExchangeConnectionError(
                f"Binance internal server or connection error: {msg}", {"code": code, "msg": msg}
            )

        raise PlatformException(log_msg, {"code": code, "msg": msg})

    # ==========================================================================
    # ExchangeAdapter Abstract Methods Implementation
    # ==========================================================================

    async def get_markets(self) -> list[Instrument]:
        """Fetch all markets from /fapi/v1/exchangeInfo and map to Instrument domain models."""
        data = await self._request(
            "GET", "/fapi/v1/exchangeInfo", estimated_weight=ENDPOINT_WEIGHTS["exchangeInfo"]
        )
        symbols_data = data.get("symbols", [])
        instruments: list[Instrument] = []

        for item in symbols_data:
            if item.get("status") != "TRADING" or item.get("quoteAsset") != "USDT":
                continue

            symbol = item["symbol"]
            contract_type_str = item.get("contractType", "PERPETUAL")
            contract_type = (
                ContractType.PERPETUAL
                if contract_type_str == "PERPETUAL"
                else ContractType.CURRENT_QUARTER
            )

            tick_size = Decimal("0.01")
            step_size = Decimal("0.001")
            min_qty = Decimal("0.001")
            min_notional = Decimal("5.0")

            for f in item.get("filters", []):
                filter_type = f.get("filterType")
                if filter_type == "PRICE_FILTER":
                    tick_size = Decimal(str(f.get("tickSize", "0.01")))
                elif filter_type == "LOT_SIZE":
                    step_size = Decimal(str(f.get("stepSize", "0.001")))
                    min_qty = Decimal(str(f.get("minQty", "0.001")))
                elif filter_type == "MIN_NOTIONAL":
                    min_notional = Decimal(str(f.get("notional", "5.0")))

            inst = Instrument(
                symbol=symbol,
                base_asset=item["baseAsset"],
                quote_asset=item["quoteAsset"],
                contract_type=contract_type,
                tick_size=tick_size,
                step_size=step_size,
                min_qty=min_qty,
                min_notional=min_notional,
                maker_fee=Decimal(str(item.get("makerCommissionRate", "0.0002"))),
                taker_fee=Decimal(str(item.get("takerCommissionRate", "0.0005"))),
                is_active=True,
            )
            instruments.append(inst)

        return instruments

    async def get_balance(self) -> list[AccountBalance]:
        """Fetch account balances from /fapi/v2/account."""
        data = await self._request(
            "GET", "/fapi/v2/account", signed=True, estimated_weight=ENDPOINT_WEIGHTS["account"]
        )
        balances: list[AccountBalance] = []
        for asset_data in data.get("assets", []):
            wallet_bal = float(asset_data.get("walletBalance", 0.0))
            if wallet_bal <= 0.0 and float(asset_data.get("availableBalance", 0.0)) <= 0.0:
                continue
            balances.append(
                AccountBalance(
                    strategy_id=self.strategy_id,
                    asset=asset_data["asset"],
                    wallet_balance=wallet_bal,
                    available_balance=float(asset_data.get("availableBalance", 0.0)),
                    locked_balance=wallet_bal - float(asset_data.get("availableBalance", 0.0)),
                )
            )
        return balances

    async def get_positions(self, symbols: list[str] | None = None) -> list[Position]:
        """Fetch active perpetual positions from /fapi/v2/positionRisk."""
        data = await self._request(
            "GET",
            "/fapi/v2/positionRisk",
            signed=True,
            estimated_weight=ENDPOINT_WEIGHTS["positionRisk"],
        )
        positions: list[Position] = []
        target_symbols = {s.upper() for s in symbols} if symbols else None

        for pos_data in data:
            sym = pos_data["symbol"]
            if target_symbols and sym not in target_symbols:
                continue

            size = float(pos_data.get("positionAmt", 0.0))
            entry_price = float(pos_data.get("entryPrice", 0.0))
            mark_price = float(pos_data.get("markPrice", 0.0))
            liq_price_val = pos_data.get("liquidationPrice")
            liq_price = float(liq_price_val) if liq_price_val and float(liq_price_val) > 0 else None
            unrealized_pnl = float(pos_data.get("unRealizedProfit", 0.0))
            leverage = float(pos_data.get("leverage", 1.0))
            margin_type = pos_data.get("marginType", "isolated").upper()
            margin_mode = MarginMode.CROSSED if margin_type == "CROSS" else MarginMode.ISOLATED

            positions.append(
                Position(
                    strategy_id=self.strategy_id,
                    symbol=sym,
                    size=size,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    liquidation_price=liq_price,
                    leverage=leverage,
                    unrealized_pnl=unrealized_pnl,
                    margin_mode=margin_mode,
                )
            )
        return positions

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Fetch open orders from /fapi/v1/openOrders."""
        params: dict[str, Any] = {}
        weight = ENDPOINT_WEIGHTS["openOrders_all"]
        if symbol:
            params["symbol"] = symbol.upper()
            weight = ENDPOINT_WEIGHTS["openOrders_single"]

        data = await self._request(
            "GET", "/fapi/v1/openOrders", params=params, signed=True, estimated_weight=weight
        )
        orders: list[Order] = []
        for o in data:
            orders.append(self._parse_order_response(o))
        return orders

    async def get_order(
        self,
        symbol: str,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> Order:
        """Fetch specific order details from /fapi/v1/order."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        elif exchange_order_id:
            params["orderId"] = exchange_order_id
        else:
            raise ValueError("Either client_order_id or exchange_order_id must be provided")

        data = await self._request(
            "GET",
            "/fapi/v1/order",
            params=params,
            signed=True,
            estimated_weight=ENDPOINT_WEIGHTS["order_get"],
        )
        return self._parse_order_response(data)

    async def get_historical_data(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        """Fetch historical klines from /fapi/v1/klines."""
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start:
            params["startTime"] = int(start.timestamp() * 1000)
        if end:
            params["endTime"] = int(end.timestamp() * 1000)

        data = await self._request(
            "GET", "/fapi/v1/klines", params=params, estimated_weight=ENDPOINT_WEIGHTS["klines"]
        )
        candles: list[Candle] = []

        for row in data:
            # Row format: [openTime, open, high, low, close, volume, closeTime, quoteVolume, count, ...]
            open_dt = datetime.fromtimestamp(row[0] / 1000.0, tz=UTC)
            close_dt = datetime.fromtimestamp(row[6] / 1000.0, tz=UTC)
            candles.append(
                Candle(
                    symbol=symbol.upper(),
                    timeframe=interval,
                    open_time=open_dt,
                    close_time=close_dt,
                    open_price=float(row[1]),
                    high_price=float(row[2]),
                    low_price=float(row[3]),
                    close_price=float(row[4]),
                    volume=float(row[5]),
                    quote_volume=float(row[7]),
                    trades_count=int(row[8]),
                    is_closed=True,
                )
            )
        return candles

    async def get_ticker(self, symbol: str) -> MarketDataEvent:
        """Fetch ticker, mark price, and funding rate from /fapi/v1/premiumIndex."""
        data = await self._request(
            "GET",
            "/fapi/v1/premiumIndex",
            params={"symbol": symbol.upper()},
            estimated_weight=ENDPOINT_WEIGHTS["premiumIndex"],
        )
        mark_price = float(data.get("markPrice", 0.0))
        last_price_str = data.get("lastFundingRate")  # premiumIndex provides mark & funding
        funding_rate = float(last_price_str) if last_price_str is not None else None

        next_funding_ms = data.get("nextFundingTime")
        next_funding_time = (
            datetime.fromtimestamp(next_funding_ms / 1000.0, tz=UTC) if next_funding_ms else None
        )

        return MarketDataEvent(
            symbol=symbol.upper(),
            mark_price=mark_price,
            index_price=float(data.get("indexPrice", 0.0)),
            funding_rate=funding_rate,
            next_funding_time=next_funding_time,
        )

    async def place_order(self, order_request: Order | OrderRequest) -> Order:
        """Submit new order to /fapi/v1/order."""
        params: dict[str, Any] = {
            "symbol": order_request.symbol.upper(),
            "side": order_request.side.value,
            "type": order_request.order_type.value,
            "quantity": order_request.quantity,
        }
        if order_request.client_order_id:
            params["newClientOrderId"] = order_request.client_order_id

        if order_request.order_type == OrderType.LIMIT:
            if order_request.price is None:
                raise OrderValidationError("Price is required for LIMIT orders")
            params["price"] = order_request.price
            params["timeInForce"] = order_request.time_in_force.value

        if order_request.stop_price is not None:
            params["stopPrice"] = order_request.stop_price

        data = await self._request(
            "POST",
            "/fapi/v1/order",
            params=params,
            signed=True,
            estimated_weight=ENDPOINT_WEIGHTS["order_post"],
            is_order=True,
        )
        return self._parse_order_response(data, original_strategy_id=order_request.strategy_id)

    async def cancel_order(
        self,
        symbol: str,
        client_order_id: str | None = None,
        exchange_order_id: str | None = None,
    ) -> Order:
        """Cancel an open order via DELETE /fapi/v1/order."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        elif exchange_order_id:
            params["orderId"] = exchange_order_id
        else:
            raise ValueError("Either client_order_id or exchange_order_id must be provided")

        data = await self._request(
            "DELETE",
            "/fapi/v1/order",
            params=params,
            signed=True,
            estimated_weight=ENDPOINT_WEIGHTS["order_delete"],
            is_order=True,
        )
        return self._parse_order_response(data)

    def _parse_order_response(
        self, data: dict[str, Any], original_strategy_id: str | None = None
    ) -> Order:
        """Map Binance order response payload to internal Order model."""
        binance_status = data.get("status", "NEW")
        status_map = {
            "NEW": OrderStatus.ACKNOWLEDGED,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.EXPIRED,
        }
        order_status = status_map.get(binance_status, OrderStatus.UNKNOWN)

        order_type_str = data.get("type", "LIMIT")
        order_type = OrderType.LIMIT if order_type_str == "LIMIT" else OrderType.MARKET
        tif_str = data.get("timeInForce", "GTC")
        tif = getattr(TimeInForce, tif_str, TimeInForce.GTC)

        return Order(
            client_order_id=data.get("clientOrderId", ""),
            exchange_order_id=str(data.get("orderId", "")),
            strategy_id=original_strategy_id or self.strategy_id,
            symbol=data.get("symbol", ""),
            side=OrderSide(data.get("side", "BUY")),
            order_type=order_type,
            time_in_force=tif,
            quantity=float(data.get("origQty", 0.0)),
            price=float(data.get("price"))
            if data.get("price") and float(data.get("price")) > 0
            else None,
            stop_price=float(data.get("stopPrice"))
            if data.get("stopPrice") and float(data.get("stopPrice")) > 0
            else None,
            status=order_status,
            filled_qty=float(data.get("executedQty", 0.0)),
            avg_fill_price=float(data.get("avgPrice", 0.0)),
            cum_quote=float(data.get("cumQuote", 0.0)),
        )

    # ==========================================================================
    # User Data Stream ListenKey Lifecycle Endpoints
    # ==========================================================================

    async def create_listen_key(self) -> str:
        """Create a new user data stream listenKey via POST /fapi/v1/listenKey."""
        if not self.api_key:
            raise ExchangeConnectionError("API Key is required to create a listenKey")
        await self.rate_limiter.acquire(estimated_weight=1)
        headers = {"X-MBX-APIKEY": self.api_key}
        try:
            response = await self._client.post("/fapi/v1/listenKey", headers=headers)
            self.rate_limiter.update_from_headers(response.headers)
            if response.status_code >= 400:
                self._handle_error_response(response)
            data = response.json()
            listen_key = data.get("listenKey", "")
            logger.info("Successfully created Binance user data stream listenKey.")
            return listen_key
        except httpx.RequestError as e:
            msg = f"Network error creating listenKey: {e}"
            logger.error(msg)
            raise ExchangeConnectionError(msg, {"error": str(e)}) from e

    async def keepalive_listen_key(self, listen_key: str | None = None) -> None:
        """Keepalive an existing listenKey via PUT /fapi/v1/listenKey."""
        if not self.api_key:
            raise ExchangeConnectionError("API Key is required to keepalive listenKey")
        await self.rate_limiter.acquire(estimated_weight=1)
        headers = {"X-MBX-APIKEY": self.api_key}
        params = {"listenKey": listen_key} if listen_key else {}
        try:
            response = await self._client.put("/fapi/v1/listenKey", params=params, headers=headers)
            self.rate_limiter.update_from_headers(response.headers)
            if response.status_code >= 400:
                self._handle_error_response(response)
            logger.debug("Successfully renewed Binance user data stream listenKey.")
        except httpx.RequestError as e:
            msg = f"Network error sending keepalive for listenKey: {e}"
            logger.warning(msg)
            raise ExchangeConnectionError(msg, {"error": str(e)}) from e

    async def close_listen_key(self, listen_key: str | None = None) -> None:
        """Close/delete a listenKey via DELETE /fapi/v1/listenKey."""
        if not self.api_key:
            return
        await self.rate_limiter.acquire(estimated_weight=1)
        headers = {"X-MBX-APIKEY": self.api_key}
        params = {"listenKey": listen_key} if listen_key else {}
        try:
            response = await self._client.delete(
                "/fapi/v1/listenKey", params=params, headers=headers
            )
            self.rate_limiter.update_from_headers(response.headers)
            if response.status_code >= 400:
                self._handle_error_response(response)
            logger.info("Successfully closed Binance user data stream listenKey.")
        except httpx.RequestError as e:
            logger.warning(f"Network error closing listenKey: {e}")

    # ==========================================================================
    # Margin Mode and Leverage Explicit Control Endpoints
    # ==========================================================================

    async def set_margin_type(self, symbol: str, margin_type: str) -> bool:
        """Set symbol margin mode on Binance Futures via POST /fapi/v1/marginType.

        Handles code -4046 ("No need to change margin type.") gracefully as success.
        Raises PlatformException on -4048 ("Margin type cannot be changed if there exists position.")
        """
        sym = symbol.upper()
        target_margin = margin_type.upper()
        try:
            res = await self._request(
                "POST",
                "/fapi/v1/marginType",
                params={"symbol": sym, "marginType": target_margin},
                signed=True,
                estimated_weight=1,
            )
            logger.info(f"Set Binance margin mode for {sym} to {target_margin}: {res}")
            return True
        except PlatformException as e:
            # Code -4046: "No need to change margin type."
            if e.details and e.details.get("code") == -4046:
                logger.info(f"Binance symbol {sym} already in target margin mode {target_margin}.")
                return True
            logger.error(f"Failed to set margin mode {target_margin} on {sym}: {e}")
            raise

    async def set_leverage(self, symbol: str, leverage: int) -> int:
        """Set symbol leverage multiplier on Binance Futures via POST /fapi/v1/leverage."""
        sym = symbol.upper()
        res = await self._request(
            "POST",
            "/fapi/v1/leverage",
            params={"symbol": sym, "leverage": int(leverage)},
            signed=True,
            estimated_weight=1,
        )
        confirmed_lev = int(res.get("leverage", leverage))
        logger.info(
            f"Set Binance leverage for {sym} to {confirmed_lev}x "
            f"(maxNotional: {res.get('maxNotionalValue')})."
        )
        return confirmed_lev

    async def get_symbol_leverage_and_margin(self, symbol: str) -> dict[str, Any]:
        """Query actual exchange ground-truth leverage and margin mode via GET /fapi/v2/positionRisk."""
        sym = symbol.upper()
        positions_data = await self._request(
            "GET",
            "/fapi/v2/positionRisk",
            params={"symbol": sym},
            signed=True,
            estimated_weight=5,
        )
        if isinstance(positions_data, list) and positions_data:
            pos_info = positions_data[0]
            return {
                "symbol": sym,
                "margin_type": str(pos_info.get("marginType", "cross")).upper(),
                "leverage": int(pos_info.get("leverage", 1)),
                "position_amt": float(pos_info.get("positionAmt", 0.0)),
                "liquidation_price": float(pos_info.get("liquidationPrice", 0.0)),
            }
        return {"symbol": sym, "margin_type": "UNKNOWN", "leverage": 1, "position_amt": 0.0}

