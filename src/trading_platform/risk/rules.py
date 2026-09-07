"""Modular and deterministic pre-trade risk rules."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from trading_platform.core.constants import RiskDecisionType, SignalType
from trading_platform.core.events import SignalEvent
from trading_platform.models.instrument import Instrument
from trading_platform.portfolio.state import PortfolioState, PositionState


@dataclass
class RuleEvaluationResult:
    """Outcome of an individual risk rule evaluation."""

    passed: bool
    decision: RiskDecisionType
    adjusted_target_exposure: float
    rule_name: str
    reason: str


@dataclass
class RiskEvaluationContext:
    """Contextual trading and portfolio state provided to risk rules."""

    signal: SignalEvent
    portfolio: PortfolioState
    position: PositionState | None = None
    instrument: Instrument | None = None
    instrument_manager: Any = None
    last_market_data_time: datetime | None = None
    is_exchange_healthy: bool = True
    recent_signal_timestamps: list[datetime] | None = None
    kill_switch_active: bool = False
    trading_enabled: bool = True


class RiskRule(ABC):
    """Abstract base class for all deterministic risk rules."""

    name: str

    @abstractmethod
    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        """Evaluate signal intent against specific risk constraint."""
        pass


# ==============================================================================
# Concrete Risk Rules
# ==============================================================================


class TradingEnabledRule(RiskRule):
    """Rule 0: Verifies global trading enablement and kill-switch status."""

    name = "TRADING_ENABLED"

    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        if context.kill_switch_active:
            return RuleEvaluationResult(
                passed=False,
                decision=RiskDecisionType.REJECTED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason="Risk Engine Kill-Switch is active. All trading halted.",
            )

        if not context.trading_enabled:
            return RuleEvaluationResult(
                passed=False,
                decision=RiskDecisionType.REJECTED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason="Global trading is disabled by configuration.",
            )

        return RuleEvaluationResult(
            passed=True,
            decision=RiskDecisionType.APPROVED,
            adjusted_target_exposure=context.signal.target_exposure,
            rule_name=self.name,
            reason="Trading is enabled and kill-switch is inactive.",
        )


class MaxDrawdownRule(RiskRule):
    """Rule 1: Circuit breaker enforcing max peak-to-current drawdown limit."""

    name = "MAX_DRAWDOWN_LIMIT"

    def __init__(self, max_drawdown_pct: float = 15.0) -> None:
        self.max_drawdown_pct = max_drawdown_pct

    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        curr_dd = context.portfolio.drawdown_pct
        if curr_dd > self.max_drawdown_pct:
            # Allow position reduction / flat signals
            if context.signal.signal_type == SignalType.FLAT or (
                context.position
                and context.position.is_open
                and abs(context.signal.target_exposure) < abs(context.position.size)
            ):
                return RuleEvaluationResult(
                    passed=True,
                    decision=RiskDecisionType.APPROVED,
                    adjusted_target_exposure=context.signal.target_exposure,
                    rule_name=self.name,
                    reason=f"Drawdown {curr_dd:.1f}% exceeds max {self.max_drawdown_pct:.1f}%, but risk-reducing signal allowed.",
                )

            return RuleEvaluationResult(
                passed=False,
                decision=RiskDecisionType.REJECTED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason=f"Portfolio drawdown ({curr_dd:.1f}%) breached circuit breaker threshold ({self.max_drawdown_pct:.1f}%).",
            )

        return RuleEvaluationResult(
            passed=True,
            decision=RiskDecisionType.APPROVED,
            adjusted_target_exposure=context.signal.target_exposure,
            rule_name=self.name,
            reason=f"Drawdown ({curr_dd:.1f}%) within limit ({self.max_drawdown_pct:.1f}%).",
        )


class MaxPositionSizeRule(RiskRule):
    """Rule 2: Restricts single-symbol position size as a fraction of portfolio equity."""

    name = "MAX_POSITION_SIZE"

    def __init__(self, max_position_pct: float = 0.20) -> None:
        self.max_position_pct = max_position_pct

    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        req_exp = context.signal.target_exposure
        abs_req = abs(req_exp)

        if abs_req > self.max_position_pct:
            clamped = self.max_position_pct if req_exp > 0 else -self.max_position_pct
            return RuleEvaluationResult(
                passed=True,
                decision=RiskDecisionType.RESIZED,
                adjusted_target_exposure=clamped,
                rule_name=self.name,
                reason=(
                    f"Requested exposure ({abs_req * 100:.1f}%) exceeded single-symbol limit "
                    f"({self.max_position_pct * 100:.1f}%). Clamped to {clamped * 100:.1f}%."
                ),
            )

        return RuleEvaluationResult(
            passed=True,
            decision=RiskDecisionType.APPROVED,
            adjusted_target_exposure=req_exp,
            rule_name=self.name,
            reason=f"Position size ({abs_req * 100:.1f}%) within limit ({self.max_position_pct * 100:.1f}%).",
        )


class MaxPortfolioLeverageRule(RiskRule):
    """Rule 3: Enforces maximum total aggregate portfolio leverage."""

    name = "MAX_PORTFOLIO_LEVERAGE"

    def __init__(self, max_aggregate_leverage: float = 3.0) -> None:
        self.max_aggregate_leverage = max_aggregate_leverage

    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        equity = context.portfolio.equity
        if equity <= 0:
            return RuleEvaluationResult(
                passed=False,
                decision=RiskDecisionType.REJECTED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason="Cannot trade with zero or negative account equity.",
            )

        # Existing notional of other positions
        existing_pos_notional = context.position.notional if context.position else 0.0
        other_notional = max(0.0, context.portfolio.total_exposure - existing_pos_notional)

        proposed_symbol_notional = abs(context.signal.target_exposure) * equity
        projected_total_notional = other_notional + proposed_symbol_notional
        projected_leverage = projected_total_notional / equity

        if projected_leverage > self.max_aggregate_leverage:
            # Calculate maximum allowable exposure
            max_allowed_symbol_notional = max(
                0.0, (self.max_aggregate_leverage * equity) - other_notional
            )
            max_allowed_exposure = max_allowed_symbol_notional / equity

            if max_allowed_exposure > 0.001:
                clamped = (
                    max_allowed_exposure
                    if context.signal.target_exposure > 0
                    else -max_allowed_exposure
                )
                return RuleEvaluationResult(
                    passed=True,
                    decision=RiskDecisionType.RESIZED,
                    adjusted_target_exposure=clamped,
                    rule_name=self.name,
                    reason=(
                        f"Projected leverage ({projected_leverage:.2f}x) exceeded max leverage "
                        f"({self.max_aggregate_leverage:.2f}x). Resized to {clamped * 100:.1f}%."
                    ),
                )
            else:
                return RuleEvaluationResult(
                    passed=False,
                    decision=RiskDecisionType.REJECTED,
                    adjusted_target_exposure=0.0,
                    rule_name=self.name,
                    reason=(
                        f"Projected leverage ({projected_leverage:.2f}x) exceeded max limit "
                        f"({self.max_aggregate_leverage:.2f}x) with no capacity remaining."
                    ),
                )

        return RuleEvaluationResult(
            passed=True,
            decision=RiskDecisionType.APPROVED,
            adjusted_target_exposure=context.signal.target_exposure,
            rule_name=self.name,
            reason=f"Projected leverage ({projected_leverage:.2f}x) within limit ({self.max_aggregate_leverage:.2f}x).",
        )


# Alias for convenience
MaxLeverageRule = MaxPortfolioLeverageRule


class MinAvailableBalanceRule(RiskRule):
    """Rule 4: Validates sufficient unallocated isolated margin balance."""

    name = "MIN_AVAILABLE_BALANCE"

    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        equity = context.portfolio.equity
        available = context.portfolio.available_balance
        req_exp = context.signal.target_exposure
        if req_exp == 0.0 or equity <= 0:
            return RuleEvaluationResult(
                passed=True,
                decision=RiskDecisionType.APPROVED,
                adjusted_target_exposure=req_exp,
                rule_name=self.name,
                reason="Closing or flat position requires no additional margin.",
            )

        leverage = (
            context.position.leverage if context.position and context.position.leverage > 0 else 1.0
        )
        proposed_notional = abs(req_exp) * equity
        required_initial_margin = proposed_notional / leverage

        existing_initial_margin = context.position.initial_margin if context.position else 0.0
        incremental_margin = max(0.0, required_initial_margin - existing_initial_margin)

        if incremental_margin > available:
            affordable_margin = available + existing_initial_margin
            affordable_notional = affordable_margin * leverage
            affordable_exposure = affordable_notional / equity if equity > 0 else 0.0

            if affordable_exposure > 0.001:
                clamped = affordable_exposure if req_exp > 0 else -affordable_exposure
                return RuleEvaluationResult(
                    passed=True,
                    decision=RiskDecisionType.RESIZED,
                    adjusted_target_exposure=clamped,
                    rule_name=self.name,
                    reason=(
                        f"Insufficient available margin (${available:,.2f}) for required "
                        f"(${incremental_margin:,.2f}). Clamped to {clamped * 100:.1f}%."
                    ),
                )
            else:
                return RuleEvaluationResult(
                    passed=False,
                    decision=RiskDecisionType.REJECTED,
                    adjusted_target_exposure=0.0,
                    rule_name=self.name,
                    reason=f"Insufficient available margin (${available:,.2f}) for trade.",
                )

        return RuleEvaluationResult(
            passed=True,
            decision=RiskDecisionType.APPROVED,
            adjusted_target_exposure=req_exp,
            rule_name=self.name,
            reason=f"Available margin (${available:,.2f}) satisfies required (${incremental_margin:,.2f}).",
        )


class LiquidationDistanceRule(RiskRule):
    """Rule 5: Standalone liquidation safety distance limit using real confirmed exchange leverage."""

    name = "LIQUIDATION_DISTANCE"

    def __init__(
        self,
        min_liquidation_distance_pct: float = 15.0,
        default_mmr: float = 0.005,
    ) -> None:
        self.min_liquidation_distance_pct = min_liquidation_distance_pct
        self.default_mmr = default_mmr

    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        req_exp = context.signal.target_exposure
        if req_exp == 0.0:
            return RuleEvaluationResult(
                passed=True,
                decision=RiskDecisionType.APPROVED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason="Flat position has infinite liquidation distance.",
            )

        # 1. Retrieve confirmed margin mode and verify strict isolation
        margin_mode = "ISOLATED"
        if context.instrument_manager and hasattr(context.instrument_manager, "get_verified_margin_mode"):
            margin_mode = context.instrument_manager.get_verified_margin_mode(context.signal.symbol)

        if margin_mode != "ISOLATED":
            return RuleEvaluationResult(
                passed=False,
                decision=RiskDecisionType.REJECTED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason=(
                    f"Symbol {context.signal.symbol} margin mode is '{margin_mode}'. "
                    f"Risk Engine strictly requires verified ISOLATED margin for safety."
                ),
            )

        # 2. Retrieve real confirmed exchange leverage multiplier
        leverage = 1.0
        if context.position and context.position.leverage > 0:
            leverage = float(context.position.leverage)
        elif context.instrument_manager and hasattr(context.instrument_manager, "get_verified_leverage"):
            leverage = float(context.instrument_manager.get_verified_leverage(context.signal.symbol))
        elif context.config and hasattr(context.config.risk, "default_symbol_leverage"):
            leverage = float(context.config.risk.default_symbol_leverage)

        # In isolated margin: liquidation distance % = (1/leverage - MMR) * 100
        dist_pct = (1.0 / leverage - self.default_mmr) * 100.0

        if dist_pct < self.min_liquidation_distance_pct:
            return RuleEvaluationResult(
                passed=False,
                decision=RiskDecisionType.REJECTED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason=(
                    f"Projected liquidation distance ({dist_pct:.1f}% at confirmed {leverage:.0f}x leverage) "
                    f"is tighter than minimum required threshold ({self.min_liquidation_distance_pct:.1f}%)."
                ),
            )

        return RuleEvaluationResult(
            passed=True,
            decision=RiskDecisionType.APPROVED,
            adjusted_target_exposure=req_exp,
            rule_name=self.name,
            reason=(
                f"Projected liquidation distance ({dist_pct:.1f}% at confirmed {leverage:.0f}x ISOLATED leverage) "
                f"satisfies minimum ({self.min_liquidation_distance_pct:.1f}%)."
            ),
        )


class InstrumentNotionalPrecisionRule(RiskRule):
    """Rule 6: Validates instrument min/max notional limits."""

    name = "INSTRUMENT_NOTIONAL_PRECISION"

    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        if not context.instrument:
            return RuleEvaluationResult(
                passed=True,
                decision=RiskDecisionType.APPROVED,
                adjusted_target_exposure=context.signal.target_exposure,
                rule_name=self.name,
                reason="No instrument constraints configured.",
            )

        notional = abs(context.signal.target_exposure) * context.portfolio.equity
        if notional > 0 and notional < float(context.instrument.min_notional):
            return RuleEvaluationResult(
                passed=False,
                decision=RiskDecisionType.REJECTED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason=(
                    f"Proposed order notional (${notional:,.2f}) is below instrument min notional "
                    f"(${float(context.instrument.min_notional):,.2f})."
                ),
            )

        return RuleEvaluationResult(
            passed=True,
            decision=RiskDecisionType.APPROVED,
            adjusted_target_exposure=context.signal.target_exposure,
            rule_name=self.name,
            reason=f"Notional (${notional:,.2f}) meets instrument requirements.",
        )


class RateOfTradeRule(RiskRule):
    """Rule 7: Protects against runaway strategy churn."""

    name = "RATE_OF_TRADE_LIMIT"

    def __init__(self, max_trades_per_window: int = 10, window_seconds: int = 60) -> None:
        self.max_trades_per_window = max_trades_per_window
        self.window_seconds = window_seconds

    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        now = (
            context.signal.timestamp
            if (context.signal and context.signal.timestamp)
            else datetime.now(UTC)
        )
        recent = context.recent_signal_timestamps or []
        cutoff = now.timestamp() - self.window_seconds
        active_recent = [t for t in recent if cutoff <= t.timestamp() <= now.timestamp()]

        if len(active_recent) >= self.max_trades_per_window:
            return RuleEvaluationResult(
                passed=False,
                decision=RiskDecisionType.REJECTED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason=(
                    f"Rate of trade ({len(active_recent)} signals in {self.window_seconds}s) "
                    f"exceeded throttle limit ({self.max_trades_per_window})."
                ),
            )

        return RuleEvaluationResult(
            passed=True,
            decision=RiskDecisionType.APPROVED,
            adjusted_target_exposure=context.signal.target_exposure,
            rule_name=self.name,
            reason="Trade frequency within rate limits.",
        )


class MarketDataHealthRule(RiskRule):
    """Rule 8: Checks exchange health and data freshness."""

    name = "MARKET_DATA_HEALTH"

    def __init__(self, max_stale_seconds: float = 10.0) -> None:
        self.max_stale_seconds = max_stale_seconds

    def evaluate(self, context: RiskEvaluationContext) -> RuleEvaluationResult:
        if not context.is_exchange_healthy:
            return RuleEvaluationResult(
                passed=False,
                decision=RiskDecisionType.REJECTED,
                adjusted_target_exposure=0.0,
                rule_name=self.name,
                reason="Exchange connectivity is degraded or disconnected.",
            )

        if context.last_market_data_time:
            now = datetime.now(UTC)
            age = (now - context.last_market_data_time).total_seconds()
            if age > self.max_stale_seconds:
                return RuleEvaluationResult(
                    passed=False,
                    decision=RiskDecisionType.REJECTED,
                    adjusted_target_exposure=0.0,
                    rule_name=self.name,
                    reason=f"Market data is stale ({age:.1f}s > {self.max_stale_seconds:.1f}s threshold).",
                )

        return RuleEvaluationResult(
            passed=True,
            decision=RiskDecisionType.APPROVED,
            adjusted_target_exposure=context.signal.target_exposure,
            rule_name=self.name,
            reason="Market data stream is fresh and exchange connection is healthy.",
        )
