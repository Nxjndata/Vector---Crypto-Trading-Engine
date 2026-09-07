"""Tests for the concrete BinanceFuturesAdapter with mocked HTTP transport."""

from decimal import Decimal

import httpx
import pytest

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
from trading_platform.core.exceptions import (
    ClockDriftError,
    InsufficientMarginError,
    OrderNotFoundError,
    OrderValidationError,
    RateLimitExceededError,
)
from trading_platform.exchange.binance_futures import BinanceFuturesAdapter
from trading_platform.exchange.rate_limiter import BinanceRateLimiter
from trading_platform.models.order import Order


@pytest.fixture
def base_app_config() -> AppConfig:
    """Create test configuration."""
    return AppConfig(
        environment=TradingMode.PAPER,
        strategy_id="test_strat",
        exchange={
            "testnet_api_key": "test_api_key_123",
            "testnet_api_secret": "test_secret_key_456",
            "recv_window": 5000,
        },
        market_data={"active_symbols": ["BTCUSDT", "ETHUSDT"]},
    )


def test_signature_generation(base_app_config: AppConfig):
    """Verify HMAC-SHA256 signature and timestamp generation."""
    adapter = BinanceFuturesAdapter(config=base_app_config)
    params = {"symbol": "BTCUSDT", "side": "BUY"}
    signed = adapter._sign_payload(params)

    assert "timestamp" in signed
    assert "recvWindow" in signed
    assert "signature" in signed
    assert len(signed["signature"]) == 64  # SHA256 hex string


@pytest.mark.asyncio
async def test_get_markets_mock(base_app_config: AppConfig):
    """Verify get_markets parses Binance exchangeInfo payload and updates rate limit."""
    mock_payload = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "makerCommissionRate": "0.0002",
                "takerCommissionRate": "0.0005",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
                ],
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=mock_payload,
            headers={"x-mbx-used-weight-1m": "15"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    rate_limiter = BinanceRateLimiter()
    adapter = BinanceFuturesAdapter(
        config=base_app_config, http_client=client, rate_limiter=rate_limiter
    )

    markets = await adapter.get_markets()
    assert len(markets) == 1
    btc = markets[0]
    assert btc.symbol == "BTCUSDT"
    assert btc.tick_size == Decimal("0.10")
    assert btc.step_size == Decimal("0.001")
    assert btc.contract_type == ContractType.PERPETUAL
    # Verify rate limit header updated
    assert rate_limiter.used_weight_1m == 15


@pytest.mark.asyncio
async def test_get_balance_mock(base_app_config: AppConfig):
    """Verify get_balance parses /fapi/v2/account payload."""
    mock_payload = {
        "assets": [
            {"asset": "USDT", "walletBalance": "10000.50", "availableBalance": "8500.00"},
            {"asset": "BNB", "walletBalance": "0.0", "availableBalance": "0.0"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_payload, headers={"x-mbx-used-weight-1m": "20"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(config=base_app_config, http_client=client)

    balances = await adapter.get_balance()
    assert len(balances) == 1
    assert balances[0].asset == "USDT"
    assert balances[0].wallet_balance == 10000.50
    assert balances[0].available_balance == 8500.00
    assert balances[0].locked_balance == 1500.50


@pytest.mark.asyncio
async def test_get_positions_mock(base_app_config: AppConfig):
    """Verify get_positions parses /fapi/v2/positionRisk payload with mark price and liquidation price."""
    mock_payload = [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0.5",
            "entryPrice": "62000.0",
            "markPrice": "63000.0",
            "liquidationPrice": "50000.0",
            "unRealizedProfit": "500.0",
            "leverage": "5",
            "marginType": "isolated",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_payload, headers={"x-mbx-used-weight-1m": "25"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(config=base_app_config, http_client=client)

    positions = await adapter.get_positions(["BTCUSDT"])
    assert len(positions) == 1
    pos = positions[0]
    assert pos.symbol == "BTCUSDT"
    assert pos.size == 0.5
    assert pos.mark_price == 63000.0
    assert pos.liquidation_price == 50000.0
    assert pos.unrealized_pnl == 500.0
    assert pos.margin_mode == MarginMode.ISOLATED


@pytest.mark.asyncio
async def test_place_and_cancel_order_mock(base_app_config: AppConfig):
    """Verify place_order and cancel_order request formatting and response mapping."""
    placed_payload = {
        "clientOrderId": "order_12345",
        "orderId": 987654321,
        "symbol": "BTCUSDT",
        "status": "NEW",
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "GTC",
        "origQty": "0.100",
        "price": "60000.0",
        "executedQty": "0.0",
        "avgPrice": "0.0",
        "cumQuote": "0.0",
    }
    cancelled_payload = {
        **placed_payload,
        "status": "CANCELED",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=placed_payload, headers={"x-mbx-used-weight-1m": "30"})
        elif request.method == "DELETE":
            return httpx.Response(
                200, json=cancelled_payload, headers={"x-mbx-used-weight-1m": "31"}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(config=base_app_config, http_client=client)

    order_req = Order(
        client_order_id="order_12345",
        strategy_id="test_strat",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        quantity=0.1,
        price=60000.0,
    )
    placed = await adapter.place_order(order_req)
    assert placed.status == OrderStatus.ACKNOWLEDGED
    assert placed.exchange_order_id == "987654321"

    cancelled = await adapter.cancel_order("BTCUSDT", client_order_id="order_12345")
    assert cancelled.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_error_code_mapping(base_app_config: AppConfig):
    """Verify Binance numeric error codes are translated into typed platform exceptions."""

    def make_error_client(status_code: int, code: int, msg: str) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json={"code": code, "msg": msg})

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://testnet.binancefuture.com"
        )

    # 1. Timestamp skew error (-1021) -> ClockDriftError
    adapter = BinanceFuturesAdapter(
        config=base_app_config,
        http_client=make_error_client(
            400, -1021, "Timestamp for this request is outside recvWindow"
        ),
    )
    with pytest.raises(ClockDriftError, match="timestamp skew"):
        await adapter.get_balance()

    # 2. Insufficient margin (-2019) -> InsufficientMarginError
    adapter = BinanceFuturesAdapter(
        config=base_app_config, http_client=make_error_client(400, -2019, "Margin is insufficient")
    )
    with pytest.raises(InsufficientMarginError, match="Insufficient margin"):
        await adapter.get_balance()

    # 3. Filter failure (-1013) -> OrderValidationError
    adapter = BinanceFuturesAdapter(
        config=base_app_config,
        http_client=make_error_client(400, -1013, "Filter failure: MIN_NOTIONAL"),
    )
    with pytest.raises(OrderValidationError, match="Order parameter validation"):
        await adapter.get_balance()

    # 4. Unknown order (-2011) -> OrderNotFoundError
    adapter = BinanceFuturesAdapter(
        config=base_app_config, http_client=make_error_client(400, -2011, "Unknown order sent")
    )
    with pytest.raises(OrderNotFoundError, match="Order not found"):
        await adapter.get_balance()

    # 5. Rate limit (-4003) -> RateLimitExceededError
    adapter = BinanceFuturesAdapter(
        config=base_app_config, http_client=make_error_client(429, -4003, "Too many requests")
    )
    with pytest.raises(RateLimitExceededError, match="rate limit violated"):
        await adapter.get_balance()
