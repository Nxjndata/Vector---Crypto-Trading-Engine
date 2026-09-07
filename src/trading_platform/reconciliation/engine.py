"""State Reconciliation Engine coordinating periodic exchange audit and recovery."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import OrderStatus, ReconciliationEventType
from trading_platform.core.events import EventBus, ReconciliationMismatchEvent
from trading_platform.core.exceptions import OrderNotFoundError
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.models.position import Position
from trading_platform.models.reconciliation import ReconciliationEvent
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.oms.state_machine import OrderStateMachine
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.reconciliation.classifier import ReconciliationClassifier
from trading_platform.reconciliation.models import (
    DiscrepancyReport,
    DiscrepancySeverity,
    ReconciliationToleranceConfig,
)
from trading_platform.risk.engine import RiskEngine

logger = get_logger("reconciliation.engine")


class ReconciliationEngine:
    """Performs scheduled independent audits between local platform state and exchange ground-truth."""

    def __init__(
        self,
        event_bus: EventBus,
        exchange_adapter: ExchangeAdapter,
        portfolio_manager: PortfolioManager,
        risk_engine: RiskEngine,
        oms: OrderManagementSystem | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        config: ReconciliationToleranceConfig | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.adapter = exchange_adapter
        self.portfolio_manager = portfolio_manager
        self.risk_engine = risk_engine
        self.oms = oms
        self.session_factory = session_factory
        self.config = config or ReconciliationToleranceConfig()

        self._is_running: bool = False
        self._loop_task: asyncio.Task | None = None
        self._last_reconciliation_time: datetime | None = None
        self._history: list[DiscrepancyReport] = []

    async def start(self) -> None:
        """Start the background periodic reconciliation loop."""
        if self._is_running:
            return
        self._is_running = True
        self._loop_task = asyncio.create_task(self._run_loop(), name="reconciliation_loop")
        logger.info(
            f"Reconciliation Engine started (interval: {self.config.interval_seconds:.1f}s)."
        )

    async def stop(self) -> None:
        """Stop the background reconciliation loop."""
        self._is_running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info("Reconciliation Engine stopped.")

    async def _run_loop(self) -> None:
        """Background periodic execution loop."""
        while self._is_running:
            try:
                await self.reconcile_now()
            except Exception as e:
                logger.error(f"Error during periodic state reconciliation: {e}", exc_info=True)

            await asyncio.sleep(self.config.interval_seconds)

    async def reconcile_now(self) -> list[DiscrepancyReport]:
        """Perform an immediate full reconciliation cycle against exchange REST snapshots."""
        logger.debug("Starting state reconciliation cycle...")
        discrepancies: list[DiscrepancyReport] = []

        try:
            # 1. Fetch remote ground-truth snapshots via ExchangeAdapter
            remote_balances = await self.adapter.get_balance()
            remote_positions = await self.adapter.get_positions()
            remote_orders = await self.adapter.get_open_orders()

            # Map remote balances by asset
            remote_bal_map = {b.asset: b.wallet_balance for b in remote_balances}
            remote_usdt_balance = remote_bal_map.get("USDT", 0.0)

            # Map remote positions by symbol
            remote_pos_map: dict[str, Position] = {p.symbol.upper(): p for p in remote_positions}

            # 2. Audit Balances for all active strategies
            for strat_id in self.portfolio_manager._wallet_balances:
                local_port = self.portfolio_manager.get_portfolio(strat_id)
                bal_report = ReconciliationClassifier.classify_balance(
                    strategy_id=strat_id,
                    local_balance=local_port.wallet_balance,
                    remote_balance=remote_usdt_balance,
                    config=self.config,
                )
                if bal_report:
                    discrepancies.append(bal_report)

            # 3. Audit Positions across all strategies
            for strat_id, pos_map in self.portfolio_manager._positions.items():
                # Check symbols tracked locally
                all_symbols = set(pos_map.keys()) | set(remote_pos_map.keys())
                for symbol in all_symbols:
                    local_pos = pos_map.get(symbol)
                    remote_pos = remote_pos_map.get(symbol)
                    pos_report = ReconciliationClassifier.classify_position(
                        strategy_id=strat_id,
                        symbol=symbol,
                        local_pos=local_pos,
                        remote_pos=remote_pos,
                        config=self.config,
                    )
                    if pos_report:
                        discrepancies.append(pos_report)

            # 4. Audit Open Orders
            if self.oms:
                local_orders = list(self.oms._orders.values())
                order_reports = ReconciliationClassifier.classify_orders(
                    local_orders=local_orders,
                    remote_open_orders=remote_orders,
                )
                discrepancies.extend(order_reports)

            # 5. Process Discrepancies through Action Hierarchy
            for report in discrepancies:
                await self._handle_discrepancy(report)

            self._last_reconciliation_time = datetime.now(UTC)
            self._history.extend(discrepancies)

            if discrepancies:
                logger.warning(
                    f"Reconciliation cycle completed with {len(discrepancies)} discrepancies detected."
                )
            else:
                logger.debug("Reconciliation cycle completed: Local state matches exchange 100%.")

        except Exception as e:
            logger.error(f"Reconciliation cycle failed: {e}", exc_info=True)

        return discrepancies

    async def _handle_discrepancy(self, report: DiscrepancyReport) -> None:
        """Apply action hierarchy according to discrepancy severity tier."""
        logger.warning(
            f"[RECONCILIATION {report.severity.value}] Type: {report.event_type.value} | "
            f"Symbol: {report.symbol or 'N/A'} | Details: {report.details}"
        )

        # 1. Tier 1: LOG_ONLY / Minor Auto-Sync
        if report.severity == DiscrepancySeverity.LOG_ONLY:
            if report.event_type == ReconciliationEventType.BALANCE_MISMATCH:
                strat_id = report.local_state.get("strategy_id", "default")
                remote_bal = report.remote_state.get("wallet_balance", 0.0)
                await self.portfolio_manager.resync_wallet_balance(strat_id, remote_bal)

        # 2. Tier 2: AUTO_RESYNC
        elif report.severity == DiscrepancySeverity.AUTO_RESYNC:
            if report.event_type == ReconciliationEventType.BALANCE_MISMATCH:
                strat_id = report.local_state.get("strategy_id", "default")
                remote_bal = report.remote_state.get("wallet_balance", 0.0)
                await self.portfolio_manager.resync_wallet_balance(strat_id, remote_bal)

            elif report.event_type == ReconciliationEventType.MISSING_ORDER and self.oms:
                cid = report.local_state.get("client_order_id")
                sym = report.symbol or ""
                if cid:
                    local_ord = self.oms.get_order(cid)
                    if local_ord:
                        in_flight_key = (local_ord.strategy_id, local_ord.symbol)
                        try:
                            # 1. Query ground-truth status from exchange
                            remote_order = await self.adapter.get_order(
                                symbol=sym,
                                client_order_id=cid,
                            )
                            # 2. Check if order was actually FILLED on exchange (missed fill)
                            if remote_order and remote_order.status in (
                                OrderStatus.FILLED,
                                OrderStatus.PARTIALLY_FILLED,
                            ):
                                logger.warning(
                                    f"[RECONCILIATION MISSED FILL] Missing order '{cid}' was found "
                                    f"FILLED on exchange (qty={remote_order.filled_qty}, price=${remote_order.avg_fill_price:,.2f}). "
                                    f"Emitting FillEvent for PortfolioManager..."
                                )
                                local_ord.exchange_order_id = remote_order.exchange_order_id
                                local_ord.filled_qty = remote_order.filled_qty
                                local_ord.avg_fill_price = remote_order.avg_fill_price
                                local_ord.fee = remote_order.fee or (
                                    remote_order.filled_qty
                                    * (remote_order.avg_fill_price or 0.0)
                                    * 0.0005
                                )
                                local_ord.status = remote_order.status
                                local_ord.updated_at = datetime.now(UTC)

                                # Dispatch missed FillEvent to PortfolioManager
                                await self.oms._emit_fill_event(local_ord, latency_ms=0.0)
                                await self.oms._persist_order_event(local_ord, remote_order.status)

                                if OrderStateMachine.is_terminal(remote_order.status):
                                    self.oms._in_flight_orders.pop(in_flight_key, None)

                                report.action_taken = (
                                    f"AUTO_RESYNC: Recovered missed fill on exchange ({remote_order.filled_qty} @ "
                                    f"${remote_order.avg_fill_price:,.2f}). Emitted FillEvent and updated portfolio."
                                )

                            elif remote_order and OrderStateMachine.is_terminal(
                                remote_order.status
                            ):
                                # Truly terminal and unfilled (CANCELLED / REJECTED / EXPIRED)
                                local_ord.status = remote_order.status
                                local_ord.updated_at = datetime.now(UTC)
                                self.oms._in_flight_orders.pop(in_flight_key, None)
                                await self.oms._persist_order_event(local_ord, remote_order.status)
                                report.action_taken = (
                                    f"AUTO_RESYNC: Verified order terminal on exchange ({remote_order.status.value}). "
                                    f"Cleaned local in-flight state."
                                )

                        except OrderNotFoundError:
                            # Order never existed or was dropped before exchange registration
                            local_ord.status = OrderStatus.CANCELLED
                            local_ord.error_message = (
                                "Cancelled during reconciliation: order not found on exchange."
                            )
                            local_ord.updated_at = datetime.now(UTC)
                            self.oms._in_flight_orders.pop(in_flight_key, None)
                            await self.oms._persist_order_event(local_ord, OrderStatus.CANCELLED)
                            report.action_taken = "AUTO_RESYNC: Order not found on exchange. Cancelled local order and cleared in-flight."

        # 3. Tier 3: CRITICAL_HALT (Emergency Kill Switch)
        elif report.severity == DiscrepancySeverity.CRITICAL_HALT:
            halt_reason = (
                f"Reconciliation hard breach [{report.event_type.value}]: {report.details}"
            )
            self.risk_engine.trigger_kill_switch(halt_reason)
            logger.critical(f"EMERGENCY HALT TRIGGERED BY RECONCILIATION: {halt_reason}")

        # 4. Emit ReconciliationMismatchEvent onto EventBus
        mismatch_event = ReconciliationMismatchEvent(
            discrepancy_type=report.event_type,
            symbol=report.symbol,
            local_state=report.local_state,
            remote_state=report.remote_state,
            discrepancy_details=report.details,
            action_taken=report.action_taken,
        )
        await self.event_bus.publish(mismatch_event)

        # 5. Persist immutable audit row in reconciliation_events
        if self.session_factory:
            try:
                async with get_db_session(self.session_factory) as session:
                    db_event = ReconciliationEvent(
                        id=str(uuid.uuid4()),
                        event_type=report.event_type,
                        symbol=report.symbol,
                        local_state=report.local_state,
                        remote_state=report.remote_state,
                        discrepancy_details=report.details,
                        action_taken=report.action_taken,
                        is_resolved=report.severity != DiscrepancySeverity.CRITICAL_HALT,
                        timestamp=report.timestamp,
                    )
                    session.add(db_event)
            except Exception as e:
                logger.error(
                    f"Failed to persist reconciliation audit record to database: {e}",
                    exc_info=True,
                )

    @property
    def metrics(self) -> dict[str, Any]:
        """Return diagnostic metrics."""
        return {
            "is_running": self._is_running,
            "last_reconciliation_time": (
                self._last_reconciliation_time.isoformat()
                if self._last_reconciliation_time
                else None
            ),
            "total_discrepancies_detected": len(self._history),
        }
