"""Tests for the InstrumentManager universe discovery, caching, and precision validators."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_platform.core.config import AppConfig
from trading_platform.core.constants import ContractType, TradingMode
from trading_platform.core.exceptions import InstrumentNotFoundError, OrderValidationError
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.models.instrument import Instrument


@pytest.fixture
def sample_instruments() -> list[Instrument]:
    """Sample instruments returned by an adapter."""
    return [
        Instrument(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            contract_type=ContractType.PERPETUAL,
            tick_size=Decimal("0.10"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5.0"),
            is_active=True,
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
            is_active=True,
        ),
        Instrument(
            symbol="DOGEUSDT",  # Not in configured active symbols
            base_asset="DOGE",
            quote_asset="USDT",
            contract_type=ContractType.PERPETUAL,
            tick_size=Decimal("0.00001"),
            step_size=Decimal("1.0"),
            min_qty=Decimal("1.0"),
            min_notional=Decimal("5.0"),
            is_active=True,
        ),
    ]


@pytest.mark.asyncio
async def test_instrument_manager_explicit_universe_filtering(
    sample_instruments: list[Instrument],
):
    """Verify InstrumentManager filters exchange results to only configured active symbols."""
    config = AppConfig(
        environment=TradingMode.PAPER,
        market_data={"active_symbols": ["BTCUSDT", "ETHUSDT"]},
    )
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_markets.return_value = sample_instruments

    mgr = InstrumentManager(config=config, adapter=mock_adapter)
    await mgr.initialize()

    universe = mgr.list_universe()
    assert sorted(universe) == ["BTCUSDT", "ETHUSDT"]
    assert "DOGEUSDT" not in universe

    btc = mgr.get_instrument("BTCUSDT")
    assert btc.tick_size == Decimal("0.10")

    with pytest.raises(InstrumentNotFoundError, match="not found in active trading universe"):
        mgr.get_instrument("DOGEUSDT")


@pytest.mark.asyncio
async def test_instrument_manager_precision_rounding(sample_instruments: list[Instrument]):
    """Verify price and quantity rounding down to instrument precision."""
    config = AppConfig(
        environment=TradingMode.PAPER,
        market_data={"active_symbols": ["BTCUSDT", "ETHUSDT"]},
    )
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_markets.return_value = sample_instruments

    mgr = InstrumentManager(config=config, adapter=mock_adapter)
    await mgr.initialize()

    # BTC price tick = 0.10
    assert mgr.round_price("BTCUSDT", 64512.378) == Decimal("64512.30")
    assert mgr.round_price("BTCUSDT", 64512.30) == Decimal("64512.30")

    # BTC quantity step = 0.001
    assert mgr.round_quantity("BTCUSDT", 0.12389) == Decimal("0.123")
    assert mgr.round_quantity("BTCUSDT", 0.500) == Decimal("0.500")

    # ETH price tick = 0.01
    assert mgr.round_price("ETHUSDT", 3456.789) == Decimal("3456.78")


@pytest.mark.asyncio
async def test_order_precision_validation(sample_instruments: list[Instrument]):
    """Verify client-side pre-submission validation enforces precision and min notional rules."""
    config = AppConfig(
        environment=TradingMode.PAPER,
        market_data={"active_symbols": ["BTCUSDT", "ETHUSDT"]},
    )
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_markets.return_value = sample_instruments

    mgr = InstrumentManager(config=config, adapter=mock_adapter)
    await mgr.initialize()

    # 1. Valid order should pass
    mgr.validate_order_precision(symbol="BTCUSDT", quantity=0.01, price=60000.0)

    # 2. Quantity below min_qty (0.001 for BTC)
    with pytest.raises(OrderValidationError, match="below minimum allowed"):
        mgr.validate_order_precision(symbol="BTCUSDT", quantity=0.0005, price=60000.0)

    # 3. Quantity not a multiple of step_size (0.001 for BTC)
    with pytest.raises(OrderValidationError, match="not a multiple of step size"):
        mgr.validate_order_precision(symbol="BTCUSDT", quantity=0.0015, price=60000.0)

    # 4. Price not a multiple of tick_size (0.10 for BTC)
    with pytest.raises(OrderValidationError, match="not a multiple of tick size"):
        mgr.validate_order_precision(symbol="BTCUSDT", quantity=0.01, price=60000.05)

    # 5. Notional value below min_notional ($5.0)
    with pytest.raises(OrderValidationError, match="below minimum notional"):
        # qty 0.001 * price 1000 = $1.0 < $5.0
        mgr.validate_order_precision(symbol="BTCUSDT", quantity=0.001, price=1000.0)


@pytest.mark.asyncio
async def test_instrument_manager_sets_margin_and_leverage_on_startup(
    sample_instruments: list[Instrument],
):
    """Verify InstrumentManager configures ISOLATED margin and 5x leverage on startup and verifies state."""
    config = AppConfig(
        environment=TradingMode.PAPER,
        market_data={"active_symbols": ["BTCUSDT", "ETHUSDT"]},
        risk={"default_margin_mode": "ISOLATED", "default_symbol_leverage": 5},
    )
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_markets.return_value = sample_instruments
    mock_adapter.get_symbol_leverage_and_margin.side_effect = lambda sym: {
        "symbol": sym,
        "margin_type": "ISOLATED",
        "leverage": 5,
        "position_amt": 0.0,
    }

    mgr = InstrumentManager(config=config, adapter=mock_adapter)
    await mgr.initialize()

    # Verify adapter calls
    mock_adapter.set_margin_type.assert_any_call("BTCUSDT", "ISOLATED")
    mock_adapter.set_margin_type.assert_any_call("ETHUSDT", "ISOLATED")
    mock_adapter.set_leverage.assert_any_call("BTCUSDT", 5)
    mock_adapter.set_leverage.assert_any_call("ETHUSDT", 5)

    # Verify verified getters
    assert mgr.get_verified_margin_mode("BTCUSDT") == "ISOLATED"
    assert mgr.get_verified_leverage("BTCUSDT") == 5
    assert mgr.get_verified_margin_mode("ETHUSDT") == "ISOLATED"
    assert mgr.get_verified_leverage("ETHUSDT") == 5


@pytest.mark.asyncio
async def test_instrument_manager_fail_fast_on_margin_mismatch(
    sample_instruments: list[Instrument],
):
    """Verify platform refuses to start if exchange reports CROSSED margin instead of ISOLATED."""
    from trading_platform.core.exceptions import ConfigurationError

    config = AppConfig(
        environment=TradingMode.PAPER,
        market_data={"active_symbols": ["BTCUSDT"]},
        risk={"default_margin_mode": "ISOLATED", "default_symbol_leverage": 5},
    )
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_markets.return_value = sample_instruments
    # Exchange returns CROSSED
    mock_adapter.get_symbol_leverage_and_margin.return_value = {
        "symbol": "BTCUSDT",
        "margin_type": "CROSSED",
        "leverage": 5,
    }

    mgr = InstrumentManager(config=config, adapter=mock_adapter)
    with pytest.raises(ConfigurationError, match="Exchange margin mode mismatch"):
        await mgr.initialize()


@pytest.mark.asyncio
async def test_instrument_manager_fail_fast_on_leverage_mismatch(
    sample_instruments: list[Instrument],
):
    """Verify platform refuses to start if exchange reports 20x leverage instead of expected 5x."""
    from trading_platform.core.exceptions import ConfigurationError

    config = AppConfig(
        environment=TradingMode.PAPER,
        market_data={"active_symbols": ["BTCUSDT"]},
        risk={"default_margin_mode": "ISOLATED", "default_symbol_leverage": 5},
    )
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_markets.return_value = sample_instruments
    # Exchange returns 20x leverage
    mock_adapter.get_symbol_leverage_and_margin.return_value = {
        "symbol": "BTCUSDT",
        "margin_type": "ISOLATED",
        "leverage": 20,
    }

    mgr = InstrumentManager(config=config, adapter=mock_adapter)
    with pytest.raises(ConfigurationError, match="Exchange leverage mismatch"):
        await mgr.initialize()

