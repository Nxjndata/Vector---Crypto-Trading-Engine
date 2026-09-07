"""Deterministic mismatch classifier and action hierarchy for State Reconciliation."""

from trading_platform.core.constants import ReconciliationEventType
from trading_platform.core.logging import get_logger
from trading_platform.models.order import Order
from trading_platform.models.position import Position
from trading_platform.oms.state_machine import OrderStateMachine
from trading_platform.portfolio.state import PositionState
from trading_platform.reconciliation.models import (
    DiscrepancyReport,
    DiscrepancySeverity,
    ReconciliationToleranceConfig,
)

logger = get_logger("reconciliation.classifier")


class ReconciliationClassifier:
    """Classifies discrepancies into deterministic categories and assigns action severity tiers."""

    @staticmethod
    def classify_balance(
        strategy_id: str,
        local_balance: float,
        remote_balance: float,
        config: ReconciliationToleranceConfig,
    ) -> DiscrepancyReport | None:
        """Classify account balance discrepancy."""
        drift = remote_balance - local_balance
        abs_drift = abs(drift)

        if abs_drift < 1e-6:
            return None

        local_state = {"strategy_id": strategy_id, "wallet_balance": local_balance}
        remote_state = {"strategy_id": strategy_id, "wallet_balance": remote_balance}
        details = (
            f"Wallet balance drift: local=${local_balance:,.2f} vs remote=${remote_balance:,.2f} "
            f"(diff=${drift:+,.2f})"
        )

        # Action Hierarchy:
        # Tier 1: Minor drift <= tolerance (e.g. $1.00 fee noise) -> LOG_ONLY / auto-sync
        if abs_drift <= config.balance_drift_tolerance_usd:
            return DiscrepancyReport(
                event_type=ReconciliationEventType.BALANCE_MISMATCH,
                severity=DiscrepancySeverity.LOG_ONLY,
                symbol=None,
                local_state=local_state,
                remote_state=remote_state,
                details=details,
                action_taken="LOG_AND_RESYNC: Correcting local wallet balance to match exchange ground-truth.",
            )

        # Tier 2: Moderate drift <= critical threshold -> AUTO_RESYNC
        if abs_drift <= config.balance_critical_threshold_usd:
            return DiscrepancyReport(
                event_type=ReconciliationEventType.BALANCE_MISMATCH,
                severity=DiscrepancySeverity.AUTO_RESYNC,
                symbol=None,
                local_state=local_state,
                remote_state=remote_state,
                details=details,
                action_taken="AUTO_RESYNC: Re-synchronizing local wallet balance to exchange.",
            )

        # Tier 3: Hard breach > critical threshold -> CRITICAL_HALT
        return DiscrepancyReport(
            event_type=ReconciliationEventType.BALANCE_MISMATCH,
            severity=DiscrepancySeverity.CRITICAL_HALT,
            symbol=None,
            local_state=local_state,
            remote_state=remote_state,
            details=details,
            action_taken="CRITICAL_HALT: Major balance breach exceeding critical tolerance. Triggering emergency kill switch.",
        )

    @staticmethod
    def classify_position(
        strategy_id: str,
        symbol: str,
        local_pos: PositionState | None,
        remote_pos: Position | None,
        config: ReconciliationToleranceConfig,
    ) -> DiscrepancyReport | None:
        """Classify open position discrepancy for a symbol."""
        sym = symbol.upper()
        local_size = local_pos.size if local_pos and abs(local_pos.size) > 1e-8 else 0.0
        remote_size = remote_pos.size if remote_pos and abs(remote_pos.size) > 1e-8 else 0.0
        delta_size = remote_size - local_size
        abs_delta = abs(delta_size)

        if abs_delta < config.position_qty_tolerance:
            return None

        local_state = {
            "strategy_id": strategy_id,
            "symbol": sym,
            "size": local_size,
            "entry_price": local_pos.entry_price if local_pos else 0.0,
        }
        remote_state = {
            "strategy_id": strategy_id,
            "symbol": sym,
            "size": remote_size,
            "entry_price": remote_pos.entry_price if remote_pos else 0.0,
        }
        details = (
            f"Position mismatch on {sym}: local size={local_size:.4f} vs remote size={remote_size:.4f} "
            f"(diff={delta_size:+.4f})"
        )

        # Hard Breach Case 1: Unmanaged position on exchange not tracked locally
        if local_size == 0.0 and remote_size != 0.0:
            return DiscrepancyReport(
                event_type=ReconciliationEventType.POSITION_MISMATCH,
                severity=DiscrepancySeverity.CRITICAL_HALT,
                symbol=sym,
                local_state=local_state,
                remote_state=remote_state,
                details=f"CRITICAL: Unmanaged position of {remote_size:.4f} {sym} open on exchange but flat locally.",
                action_taken="CRITICAL_HALT: Halting signal execution and triggering kill switch to protect capital.",
            )

        # Hard Breach Case 2: Local position open but flat on exchange (unexpected liquidation/closure)
        if local_size != 0.0 and remote_size == 0.0:
            return DiscrepancyReport(
                event_type=ReconciliationEventType.POSITION_MISMATCH,
                severity=DiscrepancySeverity.CRITICAL_HALT,
                symbol=sym,
                local_state=local_state,
                remote_state=remote_state,
                details=f"CRITICAL: Local position of {local_size:.4f} {sym} is flat on exchange.",
                action_taken="CRITICAL_HALT: Halting signal execution and triggering kill switch.",
            )

        # Hard Breach Case 3: Position direction mismatch (LONG vs SHORT)
        if (local_size > 0 and remote_size < 0) or (local_size < 0 and remote_size > 0):
            return DiscrepancyReport(
                event_type=ReconciliationEventType.POSITION_MISMATCH,
                severity=DiscrepancySeverity.CRITICAL_HALT,
                symbol=sym,
                local_state=local_state,
                remote_state=remote_state,
                details=f"CRITICAL: Position direction inverted on {sym} (local={local_size:.4f}, remote={remote_size:.4f}).",
                action_taken="CRITICAL_HALT: Halting signal execution and triggering kill switch.",
            )

        # Case 4: Sizing discrepancy
        return DiscrepancyReport(
            event_type=ReconciliationEventType.POSITION_MISMATCH,
            severity=DiscrepancySeverity.CRITICAL_HALT,
            symbol=sym,
            local_state=local_state,
            remote_state=remote_state,
            details=details,
            action_taken="CRITICAL_HALT: Position size drift exceeding tolerance. Triggering kill switch.",
        )

    @staticmethod
    def classify_orders(
        local_orders: list[Order],
        remote_open_orders: list[Order],
    ) -> list[DiscrepancyReport]:
        """Classify discrepancies between local order book state and exchange open orders."""
        reports: list[DiscrepancyReport] = []

        local_by_cid: dict[str, Order] = {
            o.client_order_id: o for o in local_orders if o.client_order_id
        }
        remote_by_cid: dict[str, Order] = {
            o.client_order_id: o for o in remote_open_orders if o.client_order_id
        }

        # 1. Check for GHOST_ORDER (Order open on exchange, but absent or closed locally)
        for cid, remote_ord in remote_by_cid.items():
            local_ord = local_by_cid.get(cid)
            if not local_ord or OrderStateMachine.is_terminal(local_ord.status):
                local_status = local_ord.status.value if local_ord else "ABSENT"
                details = (
                    f"GHOST ORDER: Order '{cid}' ({remote_ord.symbol}) is open on exchange ({remote_ord.status.value}) "
                    f"but local state is {local_status}."
                )
                reports.append(
                    DiscrepancyReport(
                        event_type=ReconciliationEventType.GHOST_ORDER,
                        severity=DiscrepancySeverity.CRITICAL_HALT,
                        symbol=remote_ord.symbol,
                        local_state={"client_order_id": cid, "status": local_status},
                        remote_state={"client_order_id": cid, "status": remote_ord.status.value},
                        details=details,
                        action_taken="CRITICAL_HALT: Untracked live order active on exchange. Triggering emergency kill switch.",
                    )
                )

        # 2. Check for MISSING_ORDER (Order open locally in-flight, but absent from exchange open orders)
        for cid, local_ord in local_by_cid.items():
            if OrderStateMachine.is_in_flight(local_ord.status) and cid not in remote_by_cid:
                details = (
                    f"MISSING ORDER: Order '{cid}' ({local_ord.symbol}) is in-flight locally ({local_ord.status.value}) "
                    f"but not present in exchange open orders."
                )
                reports.append(
                    DiscrepancyReport(
                        event_type=ReconciliationEventType.MISSING_ORDER,
                        severity=DiscrepancySeverity.AUTO_RESYNC,
                        symbol=local_ord.symbol,
                        local_state={"client_order_id": cid, "status": local_ord.status.value},
                        remote_state={"client_order_id": cid, "status": "ABSENT_FROM_OPEN_ORDERS"},
                        details=details,
                        action_taken="AUTO_RESYNC: Marking local order closed/reconciled to clear in-flight guard.",
                    )
                )

        return reports
