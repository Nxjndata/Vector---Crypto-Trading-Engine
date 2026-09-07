"""Tests for the Strategy Engine: SMA Momentum Strategy, Signal Manager, and Strategy Runner."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import SignalType
from trading_platform.core.events import CandleEvent, EventBus, SignalEvent
from trading_platform.models.signal import Signal
from trading_platform.strategy.runner import StrategyRunner
from trading_platform.strategy.signal_manager import SignalManager
from trading_platform.strategy.sma_momentum import SMAMomentumStrategy


def _make_candle(
    symbol: str,
    close_price: float,
    open_time: datetime,
    is_closed: bool = True,
    timeframe: str = "1m",
) -> CandleEvent:
    """Helper to generate mock CandleEvent."""
    return CandleEvent(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open_price=close_price,
        high_price=close_price + 1.0,
        low_price=close_price - 1.0,
        close_price=close_price,
        volume=10.0,
        quote_volume=close_price * 10.0,
        trades_count=50,
        is_closed=is_closed,
    )


# ==============================================================================
# 1. Structural Isolation & Architectural Boundary AST Tests
# ==============================================================================


def test_strategy_layer_structural_isolation():
    """Verify that NO module inside strategy/ ever imports from exchange/ or uses exchange credentials.

    This AST-based static analysis test runs on every test execution to structurally prove
    and enforce the architectural boundary between the Strategy Engine and Exchange Adapters.
    """
    strategy_dir = Path(__file__).parent.parent / "src" / "trading_platform" / "strategy"
    assert strategy_dir.exists(), f"Strategy directory not found: {strategy_dir}"

    forbidden_modules = [
        "trading_platform.exchange",
        "exchange",
        "ccxt",
        "binance",
    ]

    violations = []

    for py_file in strategy_dir.glob("*.py"):
        with open(py_file, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(py_file))

        for node in ast.walk(tree):
            # Check 'import foo'
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_modules:
                        if alias.name == forbidden or alias.name.startswith(f"{forbidden}."):
                            violations.append(
                                f"{py_file.name}:{node.lineno} imports forbidden module '{alias.name}'"
                            )

            # Check 'from foo import bar'
            elif isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    if node.module == forbidden or node.module.startswith(f"{forbidden}."):
                        violations.append(
                            f"{py_file.name}:{node.lineno} imports from forbidden module '{node.module}'"
                        )

    assert len(violations) == 0, (
        "Architectural boundary violation detected! Strategy layer must never import from exchange layer:\n"
        + "\n".join(violations)
    )


def test_strategy_instance_isolation():
    """Verify strategy instance exposes no exchange credentials and cannot execute orders."""
    strat = SMAMomentumStrategy(strategy_id="test_sma", symbols=["BTCUSDT"])
    assert not hasattr(strat, "api_key")
    assert not hasattr(strat, "api_secret")
    assert not hasattr(strat, "place_order")
    assert not hasattr(strat, "client")


# ==============================================================================
# 2. SMAMomentumStrategy Unit & Fractional Target Exposure Tests
# ==============================================================================


def test_sma_momentum_golden_cross():
    """Verify Fast/Slow SMA golden cross emits BUY signal with fractional target exposure."""
    strat = SMAMomentumStrategy(
        strategy_id="test_sma",
        symbols=["BTCUSDT"],
        fast_period=3,
        slow_period=5,
        target_exposure_pct=0.15,  # 15% portfolio equity
    )

    base_time = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)
    # 5 flat candles
    prices = [100.0, 100.0, 100.0, 100.0, 100.0]
    for i, p in enumerate(prices):
        candle = _make_candle("BTCUSDT", p, base_time + timedelta(minutes=i))
        signals = strat.on_candle(candle)
        assert len(signals) == 0  # No crossover yet

    # Rising candles causing fast SMA to cross above slow SMA
    candle_6 = _make_candle("BTCUSDT", 110.0, base_time + timedelta(minutes=5))
    signals = strat.on_candle(candle_6)
    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_type == SignalType.BUY
    assert sig.symbol == "BTCUSDT"
    # Must be in fractional range [-1.0, 1.0], exactly 0.15 (15% equity)
    assert sig.target_exposure == 0.15
    assert -1.0 <= sig.target_exposure <= 1.0
    assert sig.strategy_id == "test_sma"


def test_sma_momentum_death_cross():
    """Verify Fast/Slow SMA death cross emits SELL signal with negative fractional target exposure."""
    strat = SMAMomentumStrategy(
        strategy_id="test_sma",
        symbols=["BTCUSDT"],
        fast_period=3,
        slow_period=5,
        target_exposure_pct=0.15,  # 15% portfolio equity
    )

    base_time = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)
    # Rising prices: fast SMA > slow SMA
    prices = [100.0, 105.0, 110.0, 115.0, 120.0]
    for i, p in enumerate(prices):
        strat.on_candle(_make_candle("BTCUSDT", p, base_time + timedelta(minutes=i)))

    # Sharp price drop: fast SMA crosses below slow SMA
    drop_candle = _make_candle("BTCUSDT", 80.0, base_time + timedelta(minutes=5))
    signals = strat.on_candle(drop_candle)
    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_type == SignalType.SELL
    # Must be in fractional range [-1.0, 1.0], exactly -0.15 (-15% equity)
    assert sig.target_exposure == -0.15
    assert -1.0 <= sig.target_exposure <= 1.0


def test_strategy_fractional_target_exposure_bounds_validation():
    """Verify strategy parameter rejects absolute dollar values or out-of-bound percentages."""
    # Sane fraction (0.10) is accepted
    strat = SMAMomentumStrategy(
        strategy_id="test_bounds", symbols=["BTCUSDT"], target_exposure_pct=0.10
    )
    assert strat.target_exposure_pct == 0.10

    # Absolute dollar figures like 1000.0 must be rejected
    with pytest.raises(ValueError, match="target_exposure_pct must be in range"):
        SMAMomentumStrategy(
            strategy_id="test_bounds", symbols=["BTCUSDT"], target_exposure_pct=1000.0
        )

    # 0 or negative percentages must be rejected
    with pytest.raises(ValueError, match="target_exposure_pct must be in range"):
        SMAMomentumStrategy(strategy_id="test_bounds", symbols=["BTCUSDT"], target_exposure_pct=0.0)

    with pytest.raises(ValueError, match="target_exposure_pct must be in range"):
        SMAMomentumStrategy(
            strategy_id="test_bounds", symbols=["BTCUSDT"], target_exposure_pct=-0.5
        )


def test_sma_momentum_ignores_unclosed_candles():
    """Verify in-progress unclosed candles are ignored."""
    strat = SMAMomentumStrategy(
        strategy_id="test_sma", symbols=["BTCUSDT"], fast_period=3, slow_period=5
    )
    base_time = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)

    candle = _make_candle("BTCUSDT", 150.0, base_time, is_closed=False)
    signals = strat.on_candle(candle)
    assert len(signals) == 0


# ==============================================================================
# 3. SignalManager & Deduplication Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_signal_manager_in_memory_deduplication():
    """Verify duplicate signals with identical (strategy, symbol, timestamp, type) are dropped."""
    event_bus = EventBus()
    published_signals: list[SignalEvent] = []

    async def on_signal(event: SignalEvent) -> None:
        published_signals.append(event)

    event_bus.subscribe(SignalEvent, on_signal)
    signal_mgr = SignalManager(event_bus=event_bus)

    sig_time = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    signal1 = SignalEvent(
        strategy_id="sma_strat",
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.10,
        mark_price=64000.0,
        timestamp=sig_time,
    )
    signal2 = SignalEvent(
        strategy_id="sma_strat",
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.10,
        mark_price=64000.0,
        timestamp=sig_time,
    )

    # First signal processed successfully
    res1 = await signal_mgr.process_signal(signal1)
    assert res1 is True
    assert len(published_signals) == 1
    assert -1.0 <= published_signals[0].target_exposure <= 1.0

    # Second identical signal dropped as duplicate
    res2 = await signal_mgr.process_signal(signal2)
    assert res2 is False
    assert len(published_signals) == 1  # No duplicate emitted
    assert signal_mgr.deduplicated_count == 1


@pytest.mark.asyncio
async def test_signal_manager_database_persistence(async_test_engine):
    """Verify signals are persisted to the database and unique constraints prevent duplicates."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    signal_mgr = SignalManager(event_bus=event_bus, session_factory=session_factory)

    sig_time = datetime(2026, 8, 30, 15, 0, 0, tzinfo=UTC)
    signal = SignalEvent(
        strategy_id="sma_strat",
        symbol="ETHUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.20,
        mark_price=3500.0,
        timestamp=sig_time,
    )

    await signal_mgr.process_signal(signal)

    async with session_factory() as session:
        result = await session.execute(
            select(Signal).where(Signal.symbol == "ETHUSDT", Signal.timestamp == sig_time)
        )
        saved = result.scalar_one()
        assert saved.strategy_id == "sma_strat"
        assert saved.target_exposure == 0.20
        assert -1.0 <= saved.target_exposure <= 1.0


# ==============================================================================
# 4. StrategyRunner Integration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_strategy_runner_event_bus_integration():
    """Verify StrategyRunner receives CandleEvent from EventBus and dispatches SignalEvent."""
    event_bus = EventBus()
    signal_mgr = SignalManager(event_bus=event_bus)
    runner = StrategyRunner(event_bus=event_bus, signal_manager=signal_mgr)

    strategy = SMAMomentumStrategy(
        strategy_id="runner_sma",
        symbols=["BTCUSDT"],
        fast_period=3,
        slow_period=5,
        target_exposure_pct=0.10,
    )
    runner.register_strategy(strategy)

    emitted_signals: list[SignalEvent] = []

    async def on_signal(event: SignalEvent) -> None:
        emitted_signals.append(event)

    event_bus.subscribe(SignalEvent, on_signal)

    base_time = datetime(2026, 8, 30, 11, 0, 0, tzinfo=UTC)
    # Feed 5 flat candles
    for i in range(5):
        await event_bus.publish(_make_candle("BTCUSDT", 100.0, base_time + timedelta(minutes=i)))

    # Feed rising candle to trigger crossover
    await event_bus.publish(_make_candle("BTCUSDT", 120.0, base_time + timedelta(minutes=5)))

    # Verify signal reached event bus
    assert len(emitted_signals) == 1
    assert emitted_signals[0].signal_type == SignalType.BUY
    assert emitted_signals[0].target_exposure == 0.10
    assert -1.0 <= emitted_signals[0].target_exposure <= 1.0
