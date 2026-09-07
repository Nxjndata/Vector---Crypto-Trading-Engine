"""Independent Pre-Trade Risk Engine and Gatekeeper."""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import RiskDecisionType
from trading_platform.core.events import (
    EventBus,
    MarketDataEvent,
    RiskDecisionEvent,
    SignalEvent,
)
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.models.reconciliation import ReconciliationEvent
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.risk.audit import RiskAuditLogger
from trading_platform.risk.rules import (
    InstrumentNotionalPrecisionRule,
    LiquidationDistanceRule,
    MarketDataHealthRule,
    MaxDrawdownRule,
    MaxLeverageRule,
    MaxPositionSizeRule,
    MinAvailableBalanceRule,
    RateOfTradeRule,
    RiskEvaluationContext,
    RiskRule,
    TradingEnabledRule,
)

logger = get_logger("risk.engine")


class RiskEngine:
    """Independent pre-trade gatekeeper evaluating strategy signals against deterministic risk rules."""

    def __init__(
        self,
        event_bus: EventBus,
        portfolio_manager: PortfolioManager,
        instrument_manager: InstrumentManager | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        rules: list[RiskRule] | None = None,
        trading_enabled: bool = True,
    ) -> None:
        self.event_bus = event_bus
        self.portfolio_manager = portfolio_manager
        self.instrument_manager = instrument_manager
        self.audit_logger = RiskAuditLogger(session_factory=session_factory)
        self.trading_enabled = trading_enabled

        # Default rules suite if none provided
        self.rules: list[RiskRule] = rules or [
            TradingEnabledRule(),
            MaxDrawdownRule(max_drawdown_pct=15.0),
            MaxPositionSizeRule(max_position_pct=0.20),
            MaxLeverageRule(max_aggregate_leverage=3.0),
            MinAvailableBalanceRule(),
            LiquidationDistanceRule(min_liquidation_distance_pct=15.0),
            InstrumentNotionalPrecisionRule(),
            RateOfTradeRule(max_trades_per_window=10, window_seconds=60),
            MarketDataHealthRule(max_stale_seconds=10.0),
        ]

        # Kill switch state
        self._kill_switch_active: bool = False
        self._kill_switch_reason: str = ""

        # Health monitoring state
        self._is_exchange_healthy: bool = True
        self._last_market_data_times: dict[str, datetime] = {}
        self._recent_signals: dict[str, list[datetime]] = defaultdict(list)

        # Register EventBus listener for pure intent signals and live market data
        self.event_bus.subscribe(SignalEvent, self.on_signal_event)

        async def on_md(event: MarketDataEvent) -> None:
            self.record_market_data_tick(event.symbol, event.timestamp)

        self.event_bus.subscribe(MarketDataEvent, on_md)

    @property
    def is_kill_switch_active(self) -> bool:
        """True if the kill switch is currently engaged."""
        return self._kill_switch_active

    def trigger_kill_switch(self, reason: str) -> None:
        """Immediately trigger the emergency kill switch, halting all trade approvals."""
        self._kill_switch_active = True
        self._kill_switch_reason = reason
        logger.critical(f"EMERGENCY KILL SWITCH ENGAGED: {reason}")

    def reset_kill_switch(self, reason: str = "Manual admin reset") -> None:
        """Deliberately disengage the kill switch."""
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        logger.info(f"Kill switch reset: {reason}")

    async def restore_kill_switch_state(self) -> bool:
        """Restore kill-switch state from database on startup to persist halts across restarts."""
        if not self.audit_logger.session_factory:
            return self._kill_switch_active

        try:
            async with get_db_session(self.audit_logger.session_factory) as session:
                # Check for unresolved critical reconciliation events
                res = await session.execute(
                    select(ReconciliationEvent).where(
                        ReconciliationEvent.is_resolved == False  # noqa: E712
                    )
                )
                unresolved = res.scalars().all()
                if unresolved:
                    reasons = [e.discrepancy_details for e in unresolved]
                    self._kill_switch_active = True
                    self._kill_switch_reason = (
                        f"Restored active halt from database ({len(unresolved)} unresolved issues): "
                        + " | ".join(reasons)
                    )
                    logger.warning(
                        f"Kill switch restored to ACTIVE on startup: {self._kill_switch_reason}"
                    )
                    return True
        except Exception as e:
            logger.error(f"Failed to check persistent kill-switch state: {e}", exc_info=True)

        return self._kill_switch_active

    def record_market_data_tick(self, symbol: str, timestamp: datetime | None = None) -> None:
        """Record fresh market data tick timestamp."""
        self._last_market_data_times[symbol.upper()] = timestamp or datetime.now(UTC)

    def set_exchange_health(self, healthy: bool) -> None:
        """Update exchange connectivity health status."""
        self._is_exchange_healthy = healthy

    async def on_signal_event(self, signal: SignalEvent) -> None:
        """EventBus subscriber evaluating incoming SignalEvent."""
        await self.evaluate_signal(signal)

    async def evaluate_signal(self, signal: SignalEvent) -> RiskDecisionEvent:
        """Evaluate a strategy's SignalEvent against all deterministic risk rules."""
        strat_id = signal.strategy_id
        symbol = signal.symbol.upper()

        portfolio = self.portfolio_manager.get_portfolio(strat_id)
        position = self.portfolio_manager.get_position(strat_id, symbol)
        instrument = (
            self.instrument_manager.get_instrument(symbol) if self.instrument_manager else None
        )

        last_md_time = self._last_market_data_times.get(symbol)
        recent_sigs = self._recent_signals[symbol]

        context = RiskEvaluationContext(
            signal=signal,
            portfolio=portfolio,
            position=position,
            instrument=instrument,
            instrument_manager=self.instrument_manager,
            last_market_data_time=last_md_time,
            is_exchange_healthy=self._is_exchange_healthy,
            recent_signal_timestamps=recent_sigs,
            kill_switch_active=self._kill_switch_active,
            trading_enabled=self.trading_enabled,
        )

        # Evaluate all rules
        results = [rule.evaluate(context) for rule in self.rules]

        rejections = [r for r in results if r.decision == RiskDecisionType.REJECTED]
        resizes = [r for r in results if r.decision == RiskDecisionType.RESIZED]

        final_decision: RiskDecisionType
        approved_target_exposure: float
        rules_triggered: list[str]
        combined_reason: str

        # 1. Any rejection causes overall REJECTED outcome
        if rejections:
            final_decision = RiskDecisionType.REJECTED
            approved_target_exposure = 0.0
            rules_triggered = [r.rule_name for r in rejections] + [r.rule_name for r in resizes]
            combined_reason = " | ".join([r.reason for r in rejections + resizes])

        # 2. Resized outcome (find smallest absolute allowable exposure)
        elif resizes:
            final_decision = RiskDecisionType.RESIZED
            rules_triggered = [r.rule_name for r in resizes]
            combined_reason = " | ".join([r.reason for r in resizes])

            # Find the most conservative (smallest magnitude) allowable exposure
            min_mag = min(abs(r.adjusted_target_exposure) for r in resizes)
            approved_target_exposure = min_mag if signal.target_exposure > 0 else -min_mag

        # 3. All approved
        else:
            final_decision = RiskDecisionType.APPROVED
            approved_target_exposure = signal.target_exposure
            rules_triggered = []
            combined_reason = "All deterministic risk rules passed."

        # Snapshot for immutable audit reconstruction
        snapshot_data = {
            "wallet_balance": portfolio.wallet_balance,
            "equity": portfolio.equity,
            "available_balance": portfolio.available_balance,
            "drawdown_pct": portfolio.drawdown_pct,
            "total_exposure": portfolio.total_exposure,
            "current_position_size": position.size if position else 0.0,
            "current_position_entry": position.entry_price if position else 0.0,
            "mark_price": signal.mark_price,
            "kill_switch_active": self._kill_switch_active,
        }

        # Persist immutable audit log
        await self.audit_logger.log_decision(
            signal_id=signal.signal_id,
            strategy_id=strat_id,
            symbol=symbol,
            decision=final_decision,
            original_target_exposure=signal.target_exposure,
            approved_target_exposure=approved_target_exposure,
            reason=combined_reason,
            rules_triggered=rules_triggered,
            snapshot_data=snapshot_data,
        )

        # Construct and publish RiskDecisionEvent
        decision_event = RiskDecisionEvent(
            signal_id=signal.signal_id,
            strategy_id=strat_id,
            symbol=symbol,
            decision_type=final_decision,
            original_target_exposure=signal.target_exposure,
            approved_target_exposure=approved_target_exposure,
            reason=combined_reason,
            rule_triggered=",".join(rules_triggered) if rules_triggered else None,
            snapshot_data=snapshot_data,
        )

        sig_time = signal.timestamp if signal.timestamp else datetime.now(UTC)
        self._recent_signals[symbol].append(sig_time)
        await self.event_bus.publish(decision_event)
        return decision_event

    @property
    def metrics(self) -> dict[str, Any]:
        """Return diagnostic metrics."""
        return {
            "kill_switch_active": self._kill_switch_active,
            "trading_enabled": self.trading_enabled,
            "rules_count": len(self.rules),
            "exchange_healthy": self._is_exchange_healthy,
        }
