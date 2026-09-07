"""Comprehensive tests for the Independent Pre-Trade Risk Engine and deterministic risk rules."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import (
    ContractType,
    RiskDecisionType,
    SignalType,
)
from trading_platform.core.events import EventBus, SignalEvent
from trading_platform.models.instrument import Instrument
from trading_platform.models.risk import RiskDecisionAudit
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.portfolio.state import PortfolioState, PositionState
from trading_platform.risk.engine import RiskEngine
from trading_platform.risk.rules import (
    InstrumentNotionalPrecisionRule,
    LiquidationDistanceRule,
    MarketDataHealthRule,
    MaxDrawdownRule,
    MaxLeverageRule,
    MaxPositionSizeRule,
    RateOfTradeRule,
    RiskEvaluationContext,
    TradingEnabledRule,
)


def _make_signal(
    symbol: str = "BTCUSDT",
    target_exposure: float = 0.10,
    signal_type: SignalType = SignalType.BUY,
    mark_price: float = 60000.0,
    strategy_id: str = "test_strat",
) -> SignalEvent:
    return SignalEvent(
        strategy_id=strategy_id,
        symbol=symbol,
        signal_type=signal_type,
        target_exposure=target_exposure,
        mark_price=mark_price,
        timestamp=datetime.now(UTC),
    )


# ==============================================================================
# 1. Isolated Unit Tests per Rule
# ==============================================================================


def test_trading_enabled_rule():
    """Verify TradingEnabledRule blocks trades when trading is disabled or kill-switch is engaged."""
    rule = TradingEnabledRule()
    signal = _make_signal()
    portfolio = PortfolioState(strategy_id="test", wallet_balance=10000.0)

    # 1. Trading enabled & kill-switch inactive -> APPROVED
    ctx_ok = RiskEvaluationContext(
        signal=signal,
        portfolio=portfolio,
        trading_enabled=True,
        kill_switch_active=False,
    )
    res_ok = rule.evaluate(ctx_ok)
    assert res_ok.passed is True
    assert res_ok.decision == RiskDecisionType.APPROVED

    # 2. Kill switch active -> REJECTED
    ctx_kill = RiskEvaluationContext(
        signal=signal,
        portfolio=portfolio,
        trading_enabled=True,
        kill_switch_active=True,
    )
    res_kill = rule.evaluate(ctx_kill)
    assert res_kill.passed is False
    assert res_kill.decision == RiskDecisionType.REJECTED
    assert "Kill-Switch is active" in res_kill.reason


def test_max_drawdown_rule():
    """Verify MaxDrawdownRule blocks risk increases when drawdown exceeds limit but allows exits."""
    rule = MaxDrawdownRule(max_drawdown_pct=15.0)
    # Portfolio in 20% drawdown
    portfolio = PortfolioState(
        strategy_id="test",
        wallet_balance=8000.0,
        peak_equity=10000.0,
        unrealized_pnl=0.0,
    )
    assert portfolio.drawdown_pct == 20.0

    # 1. Buy signal while in 20% drawdown -> REJECTED
    sig_buy = _make_signal(target_exposure=0.10, signal_type=SignalType.BUY)
    ctx_buy = RiskEvaluationContext(signal=sig_buy, portfolio=portfolio)
    res_buy = rule.evaluate(ctx_buy)
    assert res_buy.passed is False
    assert res_buy.decision == RiskDecisionType.REJECTED
    assert "breached circuit breaker" in res_buy.reason

    # 2. FLAT (exit) signal while in 20% drawdown -> APPROVED
    sig_flat = _make_signal(target_exposure=0.0, signal_type=SignalType.FLAT)
    ctx_flat = RiskEvaluationContext(signal=sig_flat, portfolio=portfolio)
    res_flat = rule.evaluate(ctx_flat)
    assert res_flat.passed is True
    assert res_flat.decision == RiskDecisionType.APPROVED


def test_max_position_size_rule():
    """Verify MaxPositionSizeRule clamps exposures exceeding the single-symbol limit."""
    rule = MaxPositionSizeRule(max_position_pct=0.20)
    portfolio = PortfolioState(strategy_id="test", wallet_balance=10000.0)

    # 1. Signal requesting 35% exposure -> RESIZED to 20%
    sig_large = _make_signal(target_exposure=0.35)
    ctx = RiskEvaluationContext(signal=sig_large, portfolio=portfolio)
    res = rule.evaluate(ctx)
    assert res.decision == RiskDecisionType.RESIZED
    assert res.adjusted_target_exposure == 0.20

    # 2. Signal requesting -40% short exposure -> RESIZED to -20%
    sig_short = _make_signal(target_exposure=-0.40, signal_type=SignalType.SELL)
    ctx_short = RiskEvaluationContext(signal=sig_short, portfolio=portfolio)
    res_short = rule.evaluate(ctx_short)
    assert res_short.decision == RiskDecisionType.RESIZED
    assert res_short.adjusted_target_exposure == -0.20


def test_max_portfolio_leverage_rule():
    """Verify MaxPortfolioLeverageRule clamps exposure when aggregate portfolio leverage exceeds cap."""
    rule = MaxLeverageRule(max_aggregate_leverage=3.0)
    # Equity = 10,000, already exposed to 25,000 notional (2.5x leverage)
    portfolio = PortfolioState(
        strategy_id="test",
        wallet_balance=10000.0,
        total_exposure=25000.0,
    )

    # Signal requests another 10,000 notional (+1.0 = 100% equity -> total 35,000 = 3.5x leverage)
    sig = _make_signal(target_exposure=1.0)
    ctx = RiskEvaluationContext(signal=sig, portfolio=portfolio)
    res = rule.evaluate(ctx)
    assert res.decision == RiskDecisionType.RESIZED
    # Max allowed symbol notional = 3.0 * 10,000 - 25,000 = 5,000 -> 0.50 (50% equity)
    assert res.adjusted_target_exposure == pytest.approx(0.50)


def test_liquidation_distance_standalone_rule():
    """Verify standalone LiquidationDistanceRule rejects trades with inadequate liquidation safety distance."""
    rule = LiquidationDistanceRule(min_liquidation_distance_pct=15.0)
    portfolio = PortfolioState(strategy_id="test", wallet_balance=10000.0)

    # 1. 5x leverage -> distance = (1/5 - 0.005) * 100 = 19.5% >= 15.0% -> APPROVED
    pos_5x = PositionState(strategy_id="test", symbol="BTCUSDT", leverage=5.0)
    ctx_5x = RiskEvaluationContext(
        signal=_make_signal(target_exposure=0.10),
        portfolio=portfolio,
        position=pos_5x,
    )
    res_5x = rule.evaluate(ctx_5x)
    assert res_5x.decision == RiskDecisionType.APPROVED

    # 2. 10x leverage -> distance = (1/10 - 0.005) * 100 = 9.5% < 15.0% -> REJECTED
    pos_10x = PositionState(strategy_id="test", symbol="BTCUSDT", leverage=10.0)
    ctx_10x = RiskEvaluationContext(
        signal=_make_signal(target_exposure=0.10),
        portfolio=portfolio,
        position=pos_10x,
    )
    res_10x = rule.evaluate(ctx_10x)
    assert res_10x.decision == RiskDecisionType.REJECTED
    assert "tighter than minimum required" in res_10x.reason


def test_instrument_notional_precision_rule():
    """Verify InstrumentNotionalPrecisionRule rejects orders below instrument minimum notional."""
    rule = InstrumentNotionalPrecisionRule()
    portfolio = PortfolioState(strategy_id="test", wallet_balance=10000.0)
    inst = Instrument(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        contract_type=ContractType.PERPETUAL,
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("100.0"),  # Min 100 USD
    )

    # 0.5% exposure on $10,000 = $50 notional (< $100 min) -> REJECTED
    sig_small = _make_signal(target_exposure=0.005)
    ctx = RiskEvaluationContext(signal=sig_small, portfolio=portfolio, instrument=inst)
    res = rule.evaluate(ctx)
    assert res.decision == RiskDecisionType.REJECTED
    assert "below instrument min notional" in res.reason


def test_rate_of_trade_rule():
    """Verify RateOfTradeRule halts runaway signal frequency."""
    rule = RateOfTradeRule(max_trades_per_window=3, window_seconds=60)
    portfolio = PortfolioState(strategy_id="test", wallet_balance=10000.0)

    now = datetime.now(UTC)
    recent_3 = [
        now - timedelta(seconds=10),
        now - timedelta(seconds=20),
        now - timedelta(seconds=30),
    ]

    ctx = RiskEvaluationContext(
        signal=_make_signal(),
        portfolio=portfolio,
        recent_signal_timestamps=recent_3,
    )
    res = rule.evaluate(ctx)
    assert res.decision == RiskDecisionType.REJECTED
    assert "exceeded throttle limit" in res.reason


def test_market_data_health_rule():
    """Verify MarketDataHealthRule rejects trades on stale data or degraded exchange health."""
    rule = MarketDataHealthRule(max_stale_seconds=5.0)
    portfolio = PortfolioState(strategy_id="test", wallet_balance=10000.0)

    # 1. Data is 12 seconds old -> REJECTED
    old_time = datetime.now(UTC) - timedelta(seconds=12)
    ctx_stale = RiskEvaluationContext(
        signal=_make_signal(),
        portfolio=portfolio,
        last_market_data_time=old_time,
        is_exchange_healthy=True,
    )
    assert rule.evaluate(ctx_stale).decision == RiskDecisionType.REJECTED

    # 2. Exchange disconnected -> REJECTED
    ctx_disc = RiskEvaluationContext(
        signal=_make_signal(),
        portfolio=portfolio,
        last_market_data_time=datetime.now(UTC),
        is_exchange_healthy=False,
    )
    assert rule.evaluate(ctx_disc).decision == RiskDecisionType.REJECTED


# ==============================================================================
# 2. Multi-Rule Composite & Audit Trail Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_composite_multi_rule_evaluation_and_audit(async_test_engine):
    """Verify that a signal violating multiple rules captures all triggered rules in the immutable audit log."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    # Signal requesting 50% exposure (violates max position 20%) on 10x leverage (violates liquidation distance 15%)
    pos = pm.get_position("test_strat", "BTCUSDT")
    pos.leverage = 10.0
    risk_engine.record_market_data_tick("BTCUSDT")

    sig = _make_signal(target_exposure=0.50, strategy_id="test_strat")
    decision = await risk_engine.evaluate_signal(sig)

    assert decision.decision_type == RiskDecisionType.REJECTED
    assert "LIQUIDATION_DISTANCE" in decision.rule_triggered
    assert "MAX_POSITION_SIZE" in decision.rule_triggered

    # Verify immutable audit row in database
    async with session_factory() as session:
        res = await session.execute(
            select(RiskDecisionAudit).where(RiskDecisionAudit.strategy_id == "test_strat")
        )
        saved = res.scalar_one()
        assert saved.decision == RiskDecisionType.REJECTED
        assert "LIQUIDATION_DISTANCE" in saved.rule_triggered
        assert "MAX_POSITION_SIZE" in saved.rule_triggered
        assert saved.snapshot_data["wallet_balance"] == 10000.0


# ==============================================================================
# 3. Independent Kill-Switch Verification Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_independent_kill_switch_lifecycle(async_test_engine):
    """Verify kill switch halts trade approvals independently, blocks otherwise-valid signals, and requires deliberate reset."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        session_factory=session_factory,
    )
    risk_engine.record_market_data_tick("BTCUSDT")

    valid_signal = _make_signal(target_exposure=0.05, strategy_id="kill_test_strat")

    # 1. Normal state -> APPROVED
    dec1 = await risk_engine.evaluate_signal(valid_signal)
    assert dec1.decision_type == RiskDecisionType.APPROVED

    # 2. Trigger kill-switch independently (e.g. manual admin action)
    risk_engine.trigger_kill_switch("Manual emergency halt triggered by operator")
    assert risk_engine.is_kill_switch_active is True

    # 3. Submit valid signal -> REJECTED
    dec2 = await risk_engine.evaluate_signal(valid_signal)
    assert dec2.decision_type == RiskDecisionType.REJECTED
    assert "Kill-Switch is active" in dec2.reason

    # 4. Submit another good signal -> Still REJECTED (no auto-reset)
    dec3 = await risk_engine.evaluate_signal(valid_signal)
    assert dec3.decision_type == RiskDecisionType.REJECTED

    # 5. Deliberate administrative reset
    risk_engine.reset_kill_switch("Resolved issue")
    assert risk_engine.is_kill_switch_active is False

    # 6. Subsequent signal -> APPROVED
    dec4 = await risk_engine.evaluate_signal(valid_signal)
    assert dec4.decision_type == RiskDecisionType.APPROVED


# ==============================================================================
# 4. Explicit Deterministic Outcome-Precedence Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_risk_engine_precedence_reject_overrides_resize(async_test_engine):
    """Verify deterministic outcome precedence: REJECTED always overrides RESIZED regardless of rule order."""
    session_factory = async_sessionmaker(
        bind=async_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    event_bus = EventBus()
    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )

    # 1. Test standard order: [MaxPositionSizeRule (RESIZE), LiquidationDistanceRule (REJECT)]
    engine_order_1 = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        session_factory=session_factory,
        rules=[
            MaxPositionSizeRule(max_position_pct=0.20),
            LiquidationDistanceRule(min_liquidation_distance_pct=15.0),
        ],
    )
    # 10x leverage breaches liquidation distance (9.5% < 15%)
    pos = pm.get_position("prec_strat", "BTCUSDT")
    pos.leverage = 10.0

    # Signal requests 40% exposure (triggers MaxPositionSizeRule RESIZE to 20%)
    sig = _make_signal(target_exposure=0.40, strategy_id="prec_strat")

    dec_1 = await engine_order_1.evaluate_signal(sig)
    # Must be REJECTED, not resized to 0.20!
    assert dec_1.decision_type == RiskDecisionType.REJECTED
    assert dec_1.approved_target_exposure == 0.0
    assert "LIQUIDATION_DISTANCE" in dec_1.rule_triggered
    assert "MAX_POSITION_SIZE" in dec_1.rule_triggered

    # 2. Test REVERSED order: [LiquidationDistanceRule (REJECT), MaxPositionSizeRule (RESIZE)]
    engine_order_2 = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        session_factory=session_factory,
        rules=[
            LiquidationDistanceRule(min_liquidation_distance_pct=15.0),
            MaxPositionSizeRule(max_position_pct=0.20),
        ],
    )
    dec_2 = await engine_order_2.evaluate_signal(sig)
    # Must still be REJECTED deterministically
    assert dec_2.decision_type == RiskDecisionType.REJECTED
    assert dec_2.approved_target_exposure == 0.0
    assert "LIQUIDATION_DISTANCE" in dec_2.rule_triggered
    assert "MAX_POSITION_SIZE" in dec_2.rule_triggered
