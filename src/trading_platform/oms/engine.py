"""Order Management System (OMS) and Execution Engine."""

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.core.constants import OrderStatus, OrderType, RiskDecisionType, TimeInForce
from trading_platform.core.events import (
    EventBus,
    FillEvent,
    OrderEvent,
    RiskDecisionEvent,
)
from trading_platform.core.exceptions import (
    DuplicateOrderError,
    ExchangeConnectionError,
    OrderNotFoundError,
)
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.exchange.adapter import ExchangeAdapter, OrderRequest, OrderResponse
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.models.order import Fill, Order, OrderEventLog
from trading_platform.oms.state_machine import OrderStateMachine
from trading_platform.oms.translator import ApprovedIntentTranslator
from trading_platform.portfolio.manager import PortfolioManager

logger = get_logger("oms.engine")


class OrderManagementSystem:
    """Coordinates end-to-end order execution, state machine lifecycle, timeout recovery, and fill emission."""

    def __init__(
        self,
        event_bus: EventBus,
        exchange_adapter: ExchangeAdapter,
        portfolio_manager: PortfolioManager,
        instrument_manager: InstrumentManager | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.adapter = exchange_adapter
        self.portfolio_manager = portfolio_manager
        self.instrument_manager = instrument_manager
        self.session_factory = session_factory

        self.translator = ApprovedIntentTranslator(
            portfolio_manager=portfolio_manager,
            instrument_manager=instrument_manager,
        )

        # In-flight duplicate order guard: (strategy_id, symbol) -> client_order_id
        self._in_flight_orders: dict[tuple[str, str], str] = {}

        # Concurrency lock for atomic duplicate-checking and order registration
        self._submission_lock = asyncio.Lock()

        # In-memory orders cache: client_order_id -> Order
        self._orders: dict[str, Order] = {}

        # Active in-flight order execution tasks
        self._active_executions: set[asyncio.Task[Any]] = set()

        # Register EventBus subscribers
        self.event_bus.subscribe(RiskDecisionEvent, self.on_risk_decision_event)
        self.event_bus.subscribe(OrderEvent, self.on_order_event)

    async def restore_from_db(self) -> None:
        """Hydrate open resting orders from database on process startup to prevent ghost order false-alarms."""
        if not self.session_factory:
            return

        async with get_db_session(self.session_factory) as session:
            open_statuses = [
                OrderStatus.CREATED,
                OrderStatus.SUBMITTED,
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
            ]
            stmt = select(Order).where(Order.status.in_(open_statuses))
            res = await session.execute(stmt)
            open_orders = res.scalars().all()
            for o in open_orders:
                self._orders[o.client_order_id] = o
                self._in_flight_orders[(o.strategy_id, o.symbol)] = o.client_order_id
            logger.info(f"OMS hydrated {len(open_orders)} open resting orders from database.")

    def generate_client_order_id(self, strategy_id: str, symbol: str) -> str:
        """Generate deterministic, idempotent client order ID strictly under 32 chars for Binance API rules."""
        ts_ms = int(time.time() * 1000)
        short_strat = strategy_id.split("_")[0][:4]
        short_sym = symbol.lower().replace("usdt", "")[:4]
        short_uuid = uuid.uuid4().hex[:5]
        return f"c_{short_strat}_{short_sym}_{ts_ms}_{short_uuid}"

    async def on_risk_decision_event(self, decision: RiskDecisionEvent) -> None:
        """EventBus subscriber translating approved risk decision into an order execution."""
        if decision.decision_type in (RiskDecisionType.APPROVED, RiskDecisionType.RESIZED):
            await self.process_approved_decision(decision)

    async def on_order_event(self, event: OrderEvent) -> None:
        """Handle incoming OrderEvent from external adapters/streams (e.g. BinanceUserDataStreamClient)."""
        client_order_id = event.client_order_id
        order = self._orders.get(client_order_id)
        if not order:
            return

        # If incoming event has new status
        if event.status != order.status:
            if event.exchange_order_id:
                order.exchange_order_id = event.exchange_order_id
            if event.filled_qty:
                order.filled_qty = event.filled_qty
            if event.avg_fill_price:
                order.avg_fill_price = event.avg_fill_price

            try:
                OrderStateMachine.validate_transition(
                    client_order_id=order.client_order_id,
                    from_state=order.status,
                    to_state=event.status,
                )
                order.status = event.status
                order.updated_at = datetime.now(UTC)
                await self._persist_order_event(order, event.status)

                if OrderStateMachine.is_terminal(event.status):
                    self._in_flight_orders.pop((order.strategy_id, order.symbol), None)
            except Exception as e:
                logger.debug(f"OrderEvent transition ignored: {e}")

    async def process_approved_decision(
        self,
        decision: RiskDecisionEvent,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
    ) -> Order | None:
        """Translate risk decision, validate duplicates atomically under lock, and submit order."""
        strat_id = decision.strategy_id
        symbol = decision.symbol.upper()
        in_flight_key = (strat_id, symbol)

        async with self._submission_lock:
            # 1. In-flight duplicate guard under atomic lock
            if in_flight_key in self._in_flight_orders:
                active_cid = self._in_flight_orders[in_flight_key]
                msg = (
                    f"Duplicate order rejected: An order '{active_cid}' for {symbol} ({strat_id}) "
                    f"is already in-flight."
                )
                logger.warning(msg)
                raise DuplicateOrderError(msg)

            # 2. Translate intent to precision-validated OrderRequest
            order_req = self.translator.translate_to_order_request(
                decision=decision,
                order_type=order_type,
                price=price,
                time_in_force=time_in_force,
            )
            if not order_req:
                return None

            # 3. Create Order in CREATED state
            client_order_id = self.generate_client_order_id(strat_id, symbol)
            order_req.client_order_id = client_order_id
            order_req.strategy_id = strat_id

            order = Order(
                client_order_id=client_order_id,
                strategy_id=strat_id,
                symbol=symbol,
                side=order_req.side,
                order_type=order_req.order_type,
                time_in_force=order_req.time_in_force,
                quantity=order_req.quantity,
                price=order_req.price,
                status=OrderStatus.CREATED,
                filled_qty=0.0,
                avg_fill_price=0.0,
                fee=0.0,
            )
            self._orders[client_order_id] = order
            self._in_flight_orders[in_flight_key] = client_order_id

            await self._persist_order_event(order, OrderStatus.CREATED)

        # 4. Submit order to exchange as a tracked execution task
        exec_task = asyncio.create_task(self._execute_order(order, order_req))
        self._active_executions.add(exec_task)
        exec_task.add_done_callback(self._active_executions.discard)
        return await exec_task

    async def drain_in_flight(self, timeout: float = 5.0) -> None:
        """Wait for all actively executing order submissions to complete before shutdown."""
        active_tasks = [t for t in self._active_executions if not t.done()]
        if not active_tasks:
            return

        logger.info(
            f"Draining {len(active_tasks)} active in-flight order submission(s) (timeout={timeout}s)..."
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(*active_tasks, return_exceptions=True),
                timeout=timeout,
            )
            logger.info("All in-flight order submissions drained successfully.")
        except TimeoutError:
            logger.warning(f"Timeout draining in-flight orders after {timeout}s.")
        except Exception as e:
            logger.error(f"Error while draining in-flight orders: {e}")

    async def _execute_order(self, order: Order, order_req: OrderRequest) -> Order:
        """Submit order to ExchangeAdapter with strict timeout recovery."""
        strat_id = order.strategy_id
        symbol = order.symbol
        in_flight_key = (strat_id, symbol)

        # Transition to SUBMITTED
        await self._transition_order(order, OrderStatus.SUBMITTED)

        try:
            # Execute through ExchangeAdapter
            t_start = time.perf_counter()
            response: OrderResponse = await self.adapter.place_order(order_req)
            latency_ms = (time.perf_counter() - t_start) * 1000.0

            # Process exchange response
            order.exchange_order_id = response.exchange_order_id

            if response.status == OrderStatus.FILLED or (
                response.filled_qty and response.filled_qty >= order.quantity
            ):
                order.filled_qty = response.filled_qty or order.quantity
                order.avg_fill_price = (
                    response.avg_fill_price or response.price or order_req.price or 0.0
                )
                order.fee = response.fee

                await self._transition_order(order, OrderStatus.FILLED)
                await self._emit_fill_event(order, latency_ms)
                self._in_flight_orders.pop(in_flight_key, None)

            elif response.status == OrderStatus.PARTIALLY_FILLED:
                order.filled_qty = response.filled_qty
                order.avg_fill_price = response.avg_fill_price or response.price or 0.0
                await self._transition_order(order, OrderStatus.PARTIALLY_FILLED)
                await self._emit_fill_event(order, latency_ms)

            elif response.status == OrderStatus.ACKNOWLEDGED:
                await self._transition_order(order, OrderStatus.ACKNOWLEDGED)

            elif OrderStateMachine.is_terminal(response.status):
                await self._transition_order(order, response.status)
                self._in_flight_orders.pop(in_flight_key, None)

            return order

        except (TimeoutError, ExchangeConnectionError, Exception) as e:
            # NON-NEGOTIABLE PRINCIPLE: Never blind retry a timed-out order!
            logger.warning(
                f"Order submission for '{order.client_order_id}' timed out or encountered ambiguous error: {e}. "
                f"Entering timeout recovery to query exchange ground-truth..."
            )
            return await self._recover_timed_out_order(order)

    async def _recover_timed_out_order(self, order: Order) -> Order:
        """Query the exchange by client order ID to reconcile local state against actual ground-truth."""
        strat_id = order.strategy_id
        symbol = order.symbol
        in_flight_key = (strat_id, symbol)

        await self._transition_order(order, OrderStatus.UNKNOWN)

        try:
            # Query exchange by client_order_id
            exchange_order = await self.adapter.get_order(
                symbol=symbol,
                client_order_id=order.client_order_id,
            )

            if exchange_order and isinstance(exchange_order.status, OrderStatus):
                logger.info(
                    f"Timeout recovery: Exchange found order '{order.client_order_id}' "
                    f"with status {exchange_order.status.value}."
                )
                order.exchange_order_id = exchange_order.exchange_order_id
                order.filled_qty = exchange_order.filled_qty
                order.avg_fill_price = exchange_order.avg_fill_price

                # Sync local status
                await self._transition_order(order, exchange_order.status)

                # If exchange shows filled, emit fill
                if exchange_order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                    await self._emit_fill_event(order, latency_ms=0.0)

                if OrderStateMachine.is_terminal(exchange_order.status):
                    self._in_flight_orders.pop(in_flight_key, None)

            else:
                # Exchange does not have the order
                logger.warning(
                    f"Timeout recovery: Order '{order.client_order_id}' not found on exchange. Marking REJECTED."
                )
                order.error_message = "Timed out on submission and not found on exchange."
                await self._transition_order(order, OrderStatus.REJECTED)
                self._in_flight_orders.pop(in_flight_key, None)

        except OrderNotFoundError:
            logger.warning(
                f"Timeout recovery: Exchange returned OrderNotFoundError for '{order.client_order_id}'. Marking REJECTED."
            )
            order.error_message = "Timed out on submission; verified not on exchange."
            await self._transition_order(order, OrderStatus.REJECTED)
            self._in_flight_orders.pop(in_flight_key, None)

        except Exception as e:
            logger.error(
                f"Timeout recovery query failed for '{order.client_order_id}': {e}. Order remains UNKNOWN for reconciliation.",
                exc_info=True,
            )

        return order

    async def _transition_order(self, order: Order, new_status: OrderStatus) -> None:
        """Validate and apply order state machine transition and record event log."""
        OrderStateMachine.validate_transition(
            client_order_id=order.client_order_id,
            from_state=order.status,
            to_state=new_status,
        )
        order.status = new_status
        order.updated_at = datetime.now(UTC)

        await self._persist_order_event(order, new_status)

        # Emit OrderEvent onto EventBus
        await self.event_bus.publish(
            OrderEvent(
                client_order_id=order.client_order_id,
                exchange_order_id=order.exchange_order_id,
                strategy_id=order.strategy_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                price=order.price,
                status=order.status,
                filled_qty=order.filled_qty,
                avg_fill_price=order.avg_fill_price,
                error_message=order.error_message,
            )
        )

    async def _emit_fill_event(self, order: Order, latency_ms: float) -> None:
        """Emit FillEvent to EventBus so PortfolioManager can process fills."""
        fill_id = f"f_{order.client_order_id}_{int(time.time() * 1000)}"
        trade_id = order.exchange_order_id or f"tr_{order.client_order_id}"

        fill_event = FillEvent(
            fill_id=fill_id,
            client_order_id=order.client_order_id,
            exchange_trade_id=trade_id,
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            side=order.side,
            price=order.avg_fill_price or order.price or 0.0,
            quantity=order.filled_qty or order.quantity,
            fee=order.fee or (order.quantity * (order.avg_fill_price or 0.0) * 0.0005),
            fee_asset="USDT",
        )

        # Persist Fill model
        if self.session_factory:
            try:
                async with get_db_session(self.session_factory) as session:
                    db_fill = Fill(
                        id=fill_event.fill_id,
                        client_order_id=order.client_order_id,
                        exchange_trade_id=fill_event.exchange_trade_id,
                        strategy_id=order.strategy_id,
                        symbol=order.symbol,
                        side=order.side,
                        price=fill_event.price,
                        quantity=fill_event.quantity,
                        fee=fill_event.fee,
                        fee_asset=fill_event.fee_asset,
                        timestamp=datetime.now(UTC),
                    )
                    session.add(db_fill)
            except Exception as e:
                logger.error(f"Failed to persist Fill to database: {e}", exc_info=True)

        logger.info(
            f"[FILL EMITTED] ClientOrderId: {order.client_order_id} | Strat: {order.strategy_id} | "
            f"Sym: {order.symbol} | Side: {order.side.value} | Qty: {fill_event.quantity} | "
            f"Price: ${fill_event.price:,.2f} | Latency: {latency_ms:.1f}ms"
        )
        await self.event_bus.publish(fill_event)

    async def _persist_order_event(self, order: Order, status: OrderStatus) -> None:
        """Persist order update and order event log atomically."""
        if not self.session_factory:
            return

        try:
            async with get_db_session(self.session_factory) as session:
                await session.merge(order)
                log = OrderEventLog(
                    id=str(uuid.uuid4()),
                    client_order_id=order.client_order_id,
                    event_status=status,
                    filled_qty_delta=order.filled_qty,
                    fill_price=order.avg_fill_price,
                    fee=order.fee,
                    timestamp=datetime.now(UTC),
                )
                session.add(log)
        except Exception as e:
            logger.error(f"Failed to persist order event to database: {e}", exc_info=True)

    def get_order(self, client_order_id: str) -> Order | None:
        """Retrieve cached order by client order ID."""
        return self._orders.get(client_order_id)

    @property
    def metrics(self) -> dict[str, Any]:
        """Return OMS diagnostics."""
        return {
            "in_flight_count": len(self._in_flight_orders),
            "in_flight_orders": dict(self._in_flight_orders),
            "total_orders": len(self._orders),
        }
