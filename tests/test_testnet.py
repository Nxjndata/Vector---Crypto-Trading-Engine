"""Non-live unit tests for Phase 11: Testnet Integration and User Data Stream."""

from unittest.mock import AsyncMock

import pytest

from trading_platform.core.config import AppConfig, ExchangeConfig, load_config
from trading_platform.core.constants import OrderSide, TradingMode
from trading_platform.core.events import EventBus, FillEvent
from trading_platform.exchange.binance_futures import BinanceFuturesAdapter
from trading_platform.exchange.user_stream import BinanceUserDataStreamClient


def test_credential_structural_separation():
    """Verify testnet credentials and live credentials are structurally separated and isolated."""
    exchange = ExchangeConfig(
        testnet_api_key="testnet_key_123",
        testnet_api_secret="testnet_secret_456",
        live_api_key="live_key_789",
        live_api_secret="live_secret_abc",
    )

    # 1. Testnet mode returns testnet credentials only
    k_test, s_test = exchange.get_credentials(TradingMode.TESTNET)
    assert k_test == "testnet_key_123"
    assert s_test == "testnet_secret_456"

    # 2. Live mode returns live credentials only
    k_live, s_live = exchange.get_credentials(TradingMode.LIVE)
    assert k_live == "live_key_789"
    assert s_live == "live_secret_abc"

    # 3. Paper / Backtest modes return empty strings (no credentials sent)
    assert exchange.get_credentials(TradingMode.PAPER) == ("", "")
    assert exchange.get_credentials(TradingMode.BACKTEST) == ("", "")


def test_binance_futures_adapter_url_and_credential_selection():
    """Verify BinanceFuturesAdapter chooses testnet URL and testnet keys when environment is TESTNET."""
    config = AppConfig(
        environment=TradingMode.TESTNET,
        exchange=ExchangeConfig(
            testnet_api_key="t_key",
            testnet_api_secret="t_sec",
            live_api_key="l_key",
            live_api_secret="l_sec",
        ),
    )
    adapter = BinanceFuturesAdapter(config=config)
    assert adapter.base_url == BinanceFuturesAdapter.TESTNET_BASE_URL
    assert adapter.api_key == "t_key"
    assert adapter.api_secret == "t_sec"


def test_environment_variables_direct_testnet_mapping(monkeypatch):
    """Verify standard BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET are mapped into config."""
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "env_test_key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "env_test_sec")

    config = load_config(config_dir="config", env_override="testnet")
    assert config.exchange.testnet_api_key == "env_test_key"
    assert config.exchange.testnet_api_secret == "env_test_sec"


@pytest.mark.asyncio
async def test_user_data_stream_order_trade_update_parsing():
    """Verify BinanceUserDataStreamClient parses ORDER_TRADE_UPDATE and publishes FillEvent on EventBus."""
    event_bus = EventBus()
    config = AppConfig(environment=TradingMode.TESTNET)
    mock_adapter = AsyncMock(spec=BinanceFuturesAdapter)
    mock_adapter.create_listen_key.return_value = "mock_listen_key_123"

    client = BinanceUserDataStreamClient(
        config=config,
        event_bus=event_bus,
        adapter=mock_adapter,
    )

    received_fills: list[FillEvent] = []

    async def fill_handler(event: FillEvent) -> None:
        received_fills.append(event)

    event_bus.subscribe(FillEvent, fill_handler)

    # Synthetic ORDER_TRADE_UPDATE payload from Binance
    mock_ws_msg = {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1700000000000,
        "T": 1700000000500,
        "o": {
            "s": "BTCUSDT",
            "c": "platform_order_001",
            "S": "BUY",
            "o": "LIMIT",
            "f": "GTC",
            "q": "0.010",
            "p": "60000.00",
            "ap": "60000.00",
            "sp": "0",
            "x": "TRADE",  # Execution type = TRADE
            "X": "FILLED",  # Order status = FILLED
            "i": 987654321,
            "l": "0.010",  # Last filled quantity
            "z": "0.010",  # Cumulative filled quantity
            "L": "60000.00",  # Last filled price
            "N": "USDT",  # Fee asset
            "n": "0.3000",  # Fee amount
            "T": 1700000000500,
            "t": 11223344,  # Trade ID
            "b": "0",
            "a": "0",
            "m": False,
            "R": False,
            "wt": "CONTRACT_PRICE",
            "ot": "LIMIT",
            "ps": "BOTH",
            "cp": False,
            "rp": "0",
            "pP": False,
            "si": 0,
            "ss": 0,
        },
    }

    # Process message
    await client._process_message(mock_ws_msg)

    # Verify FillEvent was correctly produced and dispatched
    assert len(received_fills) == 1
    fill = received_fills[0]
    assert fill.symbol == "BTCUSDT"
    assert fill.side == OrderSide.BUY
    assert fill.client_order_id == "platform_order_001"
    assert fill.exchange_trade_id == "11223344"
    assert fill.quantity == 0.010
    assert fill.price == 60000.00
    assert fill.fee == 0.3000
    assert fill.fee_asset == "USDT"


@pytest.mark.asyncio
async def test_user_data_stream_account_update_parsing():
    """Verify BinanceUserDataStreamClient handles ACCOUNT_UPDATE without error."""
    event_bus = EventBus()
    config = AppConfig(environment=TradingMode.TESTNET)
    mock_adapter = AsyncMock(spec=BinanceFuturesAdapter)

    client = BinanceUserDataStreamClient(
        config=config,
        event_bus=event_bus,
        adapter=mock_adapter,
    )

    mock_account_msg = {
        "e": "ACCOUNT_UPDATE",
        "E": 1700000000000,
        "T": 1700000000100,
        "a": {
            "m": "ORDER",
            "B": [
                {
                    "a": "USDT",
                    "wb": "12345.67",
                    "cw": "12345.67",
                    "bc": "0",
                }
            ],
            "P": [
                {
                    "s": "BTCUSDT",
                    "pa": "0.010",
                    "ep": "60000.00",
                    "cr": "0.00",
                    "up": "5.00",
                    "mt": "isolated",
                    "iw": "60.00",
                    "ps": "BOTH",
                }
            ],
        },
    }

    await client._process_message(mock_account_msg)
    # Verification: processed cleanly without exception


@pytest.mark.asyncio
async def test_listen_key_lifecycle_mocked():
    """Verify listenKey create, keepalive, and close calls."""
    config = AppConfig(
        environment=TradingMode.TESTNET,
        exchange=ExchangeConfig(testnet_api_key="k1", testnet_api_secret="s1"),
    )
    event_bus = EventBus()
    mock_adapter = AsyncMock(spec=BinanceFuturesAdapter)
    mock_adapter.create_listen_key.return_value = "listen_key_abc_123"

    client = BinanceUserDataStreamClient(
        config=config,
        event_bus=event_bus,
        adapter=mock_adapter,
    )

    await client.start()
    assert client.listen_key == "listen_key_abc_123"
    assert mock_adapter.create_listen_key.call_count == 1

    await client.stop()
    assert client.listen_key is None
    assert mock_adapter.close_listen_key.call_count == 1
