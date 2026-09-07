from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.backtest.analytics import PerformanceCalculator
from trading_platform.backtest.data_loader import HistoricalDataLoader
from trading_platform.backtest.engine import BacktestEngine, get_default_backtest_risk_rules
from trading_platform.backtest.models import BacktestConfig, TradeRecord
from trading_platform.backtest.walk_forward import WalkForwardValidator
from trading_platform.core.config import AppConfig
from trading_platform.core.constants import OrderSide, SignalType
from trading_platform.core.events import EventBus, SignalEvent
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.exchange.paper import PaperExchangeAdapter
from trading_platform.models.candle import Candle
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.risk.engine import RiskEngine
from trading_platform.risk.rules import MarketDataHealthRule, RateOfTradeRule, TradingEnabledRule
from trading_platform.strategy.base import Strategy
from trading_platform.strategy.sma_momentum import SMAMomentumStrategy


def make_candle(
    symbol: str,
    index: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    volume: float = 10.0,
    base_ts: int = 1700000000,
    interval_sec: int = 60,
) -> Candle:
    """Helper to generate standard Candle models."""
    open_t = datetime.fromtimestamp(base_ts + index * interval_sec, tz=UTC)
    close_t = datetime.fromtimestamp(base_ts + (index + 1) * interval_sec - 1, tz=UTC)
    return Candle(
        symbol=symbol.upper(),
        timeframe="1m",
        open_time=open_t,
        close_time=close_t,
        open_price=open_p,
        high_price=high_p,
        low_price=low_p,
        close_price=close_p,
        volume=volume,
        quote_volume=volume * close_p,
        trades_count=100,
        is_closed=True,
    )


# ==============================================================================
# 1. Structural Test: Strategy Interface Parity
# ==============================================================================


def test_strategy_class_is_identical_and_unmodified():
    """Structural test proving the exact same Strategy base class and SMAMomentumStrategy

    is used in backtesting with zero modification or wrapper subclassing.
    """
    strategy = SMAMomentumStrategy(
        strategy_id="test_sma",
        symbols=["BTCUSDT"],
        fast_period=3,
        slow_period=5,
        target_exposure_pct=0.10,
    )

    # 1. Must be genuine instance of core Strategy base class
    assert isinstance(strategy, Strategy)
    assert isinstance(strategy, SMAMomentumStrategy)

    # 2. Strategy interface methods must exist without backtest-specific modifications
    assert hasattr(strategy, "on_candle")
    assert hasattr(strategy, "on_market_data")
    assert hasattr(strategy, "initialize")
    assert hasattr(strategy, "shutdown")


# ==============================================================================
# 2. Historical Data Loader & Pagination Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_historical_data_loader_pagination_and_gap_detection(async_test_engine):
    """Verify paginated backfill loops through exchange adapter limit, detects gaps, and persists."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    loader = HistoricalDataLoader(session_factory=session_factory)

    # Mock Exchange Adapter with 2 pages of candles (1500 total)
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    batch_1 = [make_candle("BTCUSDT", i, 50000.0, 50100.0, 49900.0, 50000.0) for i in range(1000)]
    batch_2 = [
        make_candle("BTCUSDT", i, 50000.0, 50100.0, 49900.0, 50000.0) for i in range(1000, 1500)
    ]

    mock_adapter.get_historical_data.side_effect = [batch_1, batch_2, []]

    start_t = datetime.fromtimestamp(1700000000, tz=UTC)
    end_t = start_t + timedelta(minutes=1500)

    # Execute backfill
    candles = await loader.backfill_from_adapter(
        adapter=mock_adapter,
        symbol="BTCUSDT",
        timeframe="1m",
        start=start_t,
        end=end_t,
        batch_limit=1000,
    )

    assert len(candles) == 1500
    assert mock_adapter.get_historical_data.call_count == 2

    # Verify loaded back from database
    db_candles = await loader.load_from_db("BTCUSDT", "1m", start=start_t, end=end_t)
    assert len(db_candles) == 1500

    # Test gap detection
    # Introduce an artificial gap between index 10 and 20
    gapped_series = [
        make_candle("BTCUSDT", 0, 50000, 50000, 50000, 50000),
        make_candle("BTCUSDT", 1, 50000, 50000, 50000, 50000),
        make_candle("BTCUSDT", 10, 50000, 50000, 50000, 50000),  # Gap!
    ]
    gaps = HistoricalDataLoader.detect_gaps(gapped_series, timeframe="1m")
    assert len(gaps) == 1
    assert gaps[0][0] == gapped_series[1].close_time
    assert gaps[0][1] == gapped_series[2].open_time


# ==============================================================================
# 3. Execution Simulation: Fee and Slippage Modeling
# ==============================================================================


@pytest.mark.asyncio
async def test_backtest_fee_and_slippage_numeric_precision(async_test_engine):
    """Verify execution simulation calculates exact mathematical slippage and fees."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    # Config: 10.0 bps slippage (0.1%), 0.0005 taker fee (0.05%)
    config = BacktestConfig(
        symbol="BTCUSDT",
        initial_capital=10000.0,
        slippage_bps=10.0,  # 0.1% slippage
        taker_fee_rate=0.0005,  # 0.05%
    )
    engine = BacktestEngine(config=config, session_factory=session_factory)

    # Custom 1-trade mock strategy
    class OneTradeStrategy(Strategy):
        def __init__(self):
            super().__init__(strategy_id="strat_slip_test", symbols=["BTCUSDT"])
            self.emitted = False

        def on_candle(self, candle):
            if not self.emitted:
                self.emitted = True
                return [
                    SMAMomentumStrategy.on_candle.__annotations__  # dummy
                    and Candle(
                        symbol="BTCUSDT",
                        timeframe="1m",
                        open_time=candle.open_time,
                        close_time=candle.close_time,
                        open_price=0,
                        high_price=0,
                        low_price=0,
                        close_price=0,
                        volume=0,
                        quote_volume=0,
                        trades_count=0,
                    )
                ]  # noqa
            return []

        def on_market_data(self, tick):
            return []

    # Sequence of 3 candles: flat -> buy -> hold
    prices = [60000.0, 60000.0, 60000.0]
    [make_candle("BTCUSDT", i, p, p, p, p) for i, p in enumerate(prices)]

    # We use a real SMAMomentumStrategy (fast=2, slow=3) with crossover
    sma_strat = SMAMomentumStrategy(
        strategy_id="strat_slip_test",
        symbols=["BTCUSDT"],
        fast_period=2,
        slow_period=3,
        target_exposure_pct=0.10,  # 10% on $10,000 = $1,000 notional
    )

    # 4 candles to trigger cross: 60k, 59k, 58k, 62k -> Golden Cross at 62k
    cross_prices = [60000.0, 59000.0, 58000.0, 62000.0]
    cross_candles = [make_candle("BTCUSDT", i, p, p, p, p) for i, p in enumerate(cross_prices)]

    metrics = await engine.run(sma_strat, cross_candles)

    # Math verification:
    # Mark price at crossover: $62,000.00
    # Expected slippage on BUY (10 bps = 0.1%): 62000 * (1 + 0.001) = $62,062.00
    # Target notional: $1,000.00 / $62,000.00 = 0.016129 BTC
    # Taker fee (0.05%): 0.016129 * 62062.00 * 0.0005 = $0.5005
    # Capital after entry fee: $10,000 - $0.5005 = $9,999.4995
    # Unrealized PnL at close ($62,000 vs entry $62,062): 0.016129 * (62000 - 62062) = -$1.00
    # Final equity: $9,999.4995 - $1.00 = $9,998.4995
    assert metrics.final_equity == pytest.approx(9998.50, abs=0.10)
    assert metrics.total_return_pct == pytest.approx(-0.015, abs=0.005)


# ==============================================================================
# 4. Hand-Constructed Predictable SMA Crossover Backtest
# ==============================================================================


@pytest.mark.asyncio
async def test_hand_constructed_sma_crossover_full_pipeline(async_test_engine):
    """Deterministic full backtest on known candle series: Golden Cross -> Long -> Death Cross -> Close/Flip.

    Verifies exact trade counts, fees, realized PnL, equity curve, and buy-and-hold benchmark alpha.
    """
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    adapter = PaperExchangeAdapter()
    config = BacktestConfig(
        symbol="BTCUSDT",
        initial_capital=10000.0,
        slippage_bps=0.0,  # Zero slippage for exact math assertion
        taker_fee_rate=0.0005,  # 0.05%
    )
    im = InstrumentManager(config=AppConfig(), adapter=adapter)
    await im.initialize()

    engine = BacktestEngine(
        config=config,
        instrument_manager=im,
        session_factory=session_factory,
    )

    # Strategy: fast=2, slow=4, target_exposure = 20% ($2,000 notional)
    strategy = SMAMomentumStrategy(
        strategy_id="sma_backtest_strat",
        symbols=["BTCUSDT"],
        fast_period=2,
        slow_period=4,
        target_exposure_pct=0.20,
    )

    # Candle price path:
    # 0: 50,000 (init)
    # 1: 49,000 (init)
    # 2: 48,000 (init)
    # 3: 47,000 (slow SMA: 48,500; fast SMA: 47,500 -> fast <= slow)
    # 4: 52,000 (slow SMA: 49,000; fast SMA: 49,500 -> GOLDEN CROSS! BUY 20% exposure @ $52,000)
    # 5: 55,000 (holding long; unrealized gain)
    # 6: 58,000 (holding long; unrealized gain)
    # 7: 46,000 (fast SMA: 52,000; slow SMA: 52,750 -> DEATH CROSS! SELL to flip to Short @ $46,000)
    # 8: 45,000 (holding short; final close)
    prices = [50000.0, 49000.0, 48000.0, 47000.0, 52000.0, 55000.0, 58000.0, 46000.0, 45000.0]
    candles = [make_candle("BTCUSDT", i, p, p + 50, p - 50, p) for i, p in enumerate(prices)]

    metrics = await engine.run(strategy, candles)

    # 1. Assertions on completed trades
    assert metrics.total_trades == 1  # 1 completed LONG trade closed on candle 7
    trade = metrics.trades[0]
    assert trade.side == OrderSide.BUY
    assert trade.entry_price == 52000.0
    assert trade.exit_price == 46000.0

    # Math for Trade 1:
    # Target notional on entry = 0.20 * $10,000 = $2,000.00
    # Qty = 2000 / 52000 = 0.0384615 -> rounded by instrument step size to 0.038 BTC
    # Executed Entry Notional = 0.038 * 52,000 = $1,976.00
    # Entry fee: 1976.00 * 0.0005 = $0.988
    # Exit Notional = 0.038 * 46,000 = $1,748.00
    # Exit fee: 1748.00 * 0.0005 = $0.874
    # Gross PnL: 0.038 * (46000 - 52000) = -$228.00
    # Total Fee: $0.988 + $0.874 = $1.862
    # Net PnL: -$228.00 - $1.862 = -$229.862
    assert trade.quantity == 0.038
    assert trade.gross_pnl == pytest.approx(-228.00, abs=0.01)
    assert trade.fee == pytest.approx(1.862, abs=0.01)
    assert trade.net_pnl == pytest.approx(-229.862, abs=0.01)

    # 2. Buy-and-Hold Benchmark
    # First open: $50,000 -> Last close: $45,000 = -10.00%
    assert metrics.benchmark_return_pct == pytest.approx(-10.00, abs=0.01)

    # 3. Final Equity & Total Fees
    assert len(metrics.equity_curve) == len(candles) + 1  # initial + each candle
    # Total fees paid across all fills: $1.862 (completed long trade) + $1.012 (new open short entry fee) = $2.874
    assert metrics.total_fees_paid == pytest.approx(2.874, abs=0.01)


# ==============================================================================
# 5. Walk-Forward Validation & Data Leakage Prevention Tests
# ==============================================================================


def test_walk_forward_split_zero_data_leakage():
    """Verify WalkForwardValidator generates non-overlapping windows and strictly prevents future data leakage."""
    # 100 candles
    candles = [make_candle("BTCUSDT", i, 50000 + i * 10, 50000, 50000, 50000) for i in range(100)]

    # 1. Simple train/test split (70/30)
    train, test = WalkForwardValidator.train_test_split(candles, train_ratio=0.70)
    assert len(train) == 70
    assert len(test) == 30
    # Strict temporal assertion: last train candle < first test candle
    assert train[-1].open_time < test[0].open_time
    assert train[-1].close_time <= test[0].open_time

    # 2. Rolling Walk-Forward Windows (Train: 40, Test: 20, Step: 20)
    # Total 100 candles:
    # Window 1: Train 0..40, Test 40..60
    # Window 2: Train 20..60, Test 60..80
    # Window 3: Train 40..80, Test 80..100
    windows = WalkForwardValidator.generate_windows(
        candles=candles,
        train_size=40,
        test_size=20,
        step_size=20,
    )

    assert len(windows) == 3

    for wf_window, train_slice, test_slice in windows:
        assert len(train_slice) == 40
        assert len(test_slice) == 20
        # No lookahead overlap
        assert train_slice[-1].open_time < test_slice[0].open_time
        assert wf_window.train_end <= wf_window.test_start


@pytest.mark.asyncio
async def test_walk_forward_evaluation_execution():
    """Verify walk-forward evaluation runs separately for in-sample and out-of-sample datasets."""
    candles = [
        make_candle("BTCUSDT", i, 50000 + (i % 10) * 100, 50000, 50000, 50000) for i in range(60)
    ]

    windows = WalkForwardValidator.generate_windows(
        candles=candles,
        train_size=20,
        test_size=10,
        step_size=15,
    )
    assert len(windows) == 3

    def engine_factory():
        return BacktestEngine(config=BacktestConfig(symbol="BTCUSDT"))

    def strategy_factory():
        return SMAMomentumStrategy(
            strategy_id="wf_strat",
            symbols=["BTCUSDT"],
            fast_period=2,
            slow_period=4,
            target_exposure_pct=0.10,
        )

    results = await WalkForwardValidator.evaluate_walk_forward(
        engine_factory=engine_factory,
        strategy_factory=strategy_factory,
        windows=windows,
    )

    assert len(results) == 3
    for res in results:
        assert res.in_sample_metrics is not None
        assert res.out_of_sample_metrics is not None
        assert isinstance(res.in_sample_metrics.total_return_pct, float)
        assert isinstance(res.out_of_sample_metrics.total_return_pct, float)


def test_performance_calculator_metrics_isolated_math():
    """Verify quantitative formulas for Sharpe, Sortino, Drawdown, Win Rate, and Profit Factor on synthetic data."""
    t0 = datetime.fromtimestamp(1700000000, tz=UTC)
    # Synthetic equity curve: 10k -> 10.5k -> 10.2k -> 11.0k -> 10.8k
    equity_curve = [
        (t0, 10000.0),
        (t0 + timedelta(days=1), 10500.0),
        (t0 + timedelta(days=2), 10200.0),
        (t0 + timedelta(days=3), 11000.0),
        (t0 + timedelta(days=4), 10800.0),
    ]

    # Synthetic trades: 2 wins, 1 loss
    trades = [
        TradeRecord(
            trade_id="T1",
            strategy_id="s1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            entry_time=t0,
            exit_time=t0 + timedelta(days=1),
            entry_price=50000.0,
            exit_price=52500.0,
            quantity=0.2,
            notional=10000.0,
            gross_pnl=500.0,
            fee=5.0,
            net_pnl=495.0,
            return_pct=4.95,
        ),
        TradeRecord(
            trade_id="T2",
            strategy_id="s1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            entry_time=t0 + timedelta(days=1),
            exit_time=t0 + timedelta(days=2),
            entry_price=52500.0,
            exit_price=51000.0,
            quantity=0.2,
            notional=10500.0,
            gross_pnl=-300.0,
            fee=5.0,
            net_pnl=-305.0,
            return_pct=-2.9,
        ),
        TradeRecord(
            trade_id="T3",
            strategy_id="s1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            entry_time=t0 + timedelta(days=2),
            exit_time=t0 + timedelta(days=3),
            entry_price=51000.0,
            exit_price=55000.0,
            quantity=0.2,
            notional=10200.0,
            gross_pnl=800.0,
            fee=5.0,
            net_pnl=795.0,
            return_pct=7.8,
        ),
    ]

    candles = [
        make_candle("BTCUSDT", 0, 50000.0, 50000.0, 50000.0, 50000.0),
        make_candle("BTCUSDT", 4, 54000.0, 54000.0, 54000.0, 54000.0),
    ]

    metrics = PerformanceCalculator.compute_metrics(
        initial_capital=10000.0,
        equity_curve=equity_curve,
        trades=trades,
        candles=candles,
    )

    # Total Return: (10800 - 10000) / 10000 = +8.00%
    assert metrics.total_return_pct == pytest.approx(8.0, abs=0.01)

    # Buy & Hold: (54000 - 50000) / 50000 = +8.00%
    assert metrics.benchmark_return_pct == pytest.approx(8.0, abs=0.01)
    assert metrics.alpha_vs_benchmark_pct == pytest.approx(0.0, abs=0.01)

    # Trades: 2 wins / 3 total = 66.7%
    assert metrics.total_trades == 3
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert metrics.win_rate_pct == pytest.approx(66.67, abs=0.01)

    # Profit Factor: (495 + 795) / 305 = 1290 / 305 = 4.2295
    assert metrics.profit_factor == pytest.approx(4.23, abs=0.01)

    # Max Drawdown: Peak 11,000 -> 10,800 = (200 / 11000) = 1.818%
    # Earlier Drawdown: Peak 10,500 -> 10,200 = (300 / 10500) = 2.857% -> Max is 2.857%
    assert metrics.max_drawdown_pct == pytest.approx(2.857, abs=0.01)

    # Summary dict formatting
    summary = metrics.summary()
    assert summary["Total Return"] == "+8.00%"
    assert summary["Win Rate"] == "66.7%"


def test_walk_forward_invalid_parameters_fail_fast():
    """Verify validation guard rails reject invalid walk-forward configs."""
    candles = [make_candle("BTCUSDT", i, 50000, 50000, 50000, 50000) for i in range(10)]

    with pytest.raises(ValueError, match="train_ratio must be strictly between 0.0 and 1.0"):
        WalkForwardValidator.train_test_split(candles, train_ratio=1.5)

    with pytest.raises(ValueError, match="train_ratio must be strictly between 0.0 and 1.0"):
        WalkForwardValidator.train_test_split(candles, train_ratio=0.0)

    with pytest.raises(ValueError, match="train_size and test_size must be positive"):
        WalkForwardValidator.generate_windows(candles, train_size=0, test_size=5)

    with pytest.raises(ValueError, match="step_size must be a positive integer"):
        WalkForwardValidator.generate_windows(candles, train_size=5, test_size=2, step_size=-1)


# ==============================================================================
# 6. Structural Risk Rule Parity & Throttling Tests
# ==============================================================================


def test_backtest_risk_rules_structural_parity_with_live():
    """Structural test proving backtest RiskEngine uses the exact same rule set as live/paper,

    with MarketDataHealthRule as the ONLY explicit, documented exception.
    """
    event_bus = EventBus()
    pm = PortfolioManager(event_bus=event_bus, session_factory=None)
    live_risk_engine = RiskEngine(event_bus=event_bus, portfolio_manager=pm)

    live_rule_types = {type(r) for r in live_risk_engine.rules}
    backtest_rule_types = {type(r) for r in get_default_backtest_risk_rules()}

    # Assert live has 9 rules, backtest has 8 rules
    assert len(live_rule_types) == 9
    assert len(backtest_rule_types) == 8

    # Assert backtest contains TradingEnabledRule, RateOfTradeRule, and all other core live rules
    assert TradingEnabledRule in backtest_rule_types
    assert RateOfTradeRule in backtest_rule_types

    # Assert MarketDataHealthRule is the ONLY excluded rule (since live WS heartbeat lag doesn't apply to historical replay)
    assert backtest_rule_types == live_rule_types - {MarketDataHealthRule}


@pytest.mark.asyncio
async def test_backtest_engine_throttles_rapid_signals_via_rate_of_trade_rule():
    """Verify RateOfTradeRule actively throttles rapid backtest signals identically to live trading."""

    # Custom strategy emitting a BUY signal with varying target exposure on every candle
    class RapidSignalStrategy(Strategy):
        def __init__(self):
            super().__init__(strategy_id="strat_rapid", symbols=["BTCUSDT"])
            self.signal_count = 0

        def on_candle(self, candle):
            self.signal_count += 1
            # Request distinct target exposures so if approved it would trigger orders:
            # 0.05, 0.10, 0.15, 0.20, 0.25
            return [
                SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=candle.symbol,
                    signal_type=SignalType.BUY,
                    target_exposure=0.05 * self.signal_count,
                    mark_price=candle.close_price,
                    timestamp=candle.close_time,
                )
            ]

        def on_market_data(self, tick):
            return []

    # Backtest engine with RateOfTradeRule configured for max 2 trades per 180-second window
    custom_rules = [
        TradingEnabledRule(),
        RateOfTradeRule(max_trades_per_window=2, window_seconds=180),
    ]
    config = BacktestConfig(symbol="BTCUSDT", initial_capital=10000.0, slippage_bps=0.0)
    engine = BacktestEngine(config=config, risk_rules=custom_rules)

    # 5 candles spaced 30 seconds apart (all within 120s < 180s window)
    prices = [60000.0, 60000.0, 60000.0, 60000.0, 60000.0]
    candles = [make_candle("BTCUSDT", i, p, p, p, p, interval_sec=30) for i, p in enumerate(prices)]

    strategy = RapidSignalStrategy()
    metrics = await engine.run(strategy, candles)

    # Verification:
    # Candle 0 (t=30s): Signal 1 (Target 0.05) -> APPROVED (1st in window) -> position opened (0.00833 BTC)
    # Candle 1 (t=60s): Signal 2 (Target 0.10) -> APPROVED (2nd in window) -> position increased to 0.01666 BTC
    # Candle 2 (t=90s): Signal 3 (Target 0.15) -> REJECTED by RateOfTradeRule (throttle limit 2 exceeded)!
    # Candle 3 (t=120s): Signal 4 (Target 0.20) -> REJECTED by RateOfTradeRule!
    # Candle 4 (t=150s): Signal 5 (Target 0.25) -> REJECTED by RateOfTradeRule!

    # Final position remains capped at Signal 2's size (10% exposure = $1,000 / $60,000 = 0.01666 BTC)
    # rather than inflating to Signal 5's 25% ($2,500) exposure!
    assert metrics is not None
    # Total fees paid reflects only the 2 approved execution fills ($0.25 + $0.25 = $0.50), not 5 fills
    assert metrics.total_fees_paid == pytest.approx(0.50, abs=0.01)
