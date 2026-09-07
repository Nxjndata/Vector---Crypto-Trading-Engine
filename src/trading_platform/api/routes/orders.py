"""Orders monitoring REST endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select

from trading_platform.api.container import PlatformContainer, get_container
from trading_platform.api.schemas import OrderResponse
from trading_platform.core.constants import OrderStatus
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.models.order import Order

logger = get_logger("api.orders")
router = APIRouter(prefix="/orders", tags=["Orders"])


@router.get("/open", response_model=list[OrderResponse])
async def get_open_orders(
    container: PlatformContainer = Depends(get_container),
) -> list[OrderResponse]:
    """Retrieve all open resting orders for the active strategy."""
    orders: list[OrderResponse] = []

    if container.session_factory:
        try:
            async with get_db_session(container.session_factory) as session:
                open_statuses = [
                    OrderStatus.CREATED,
                    OrderStatus.SUBMITTED,
                    OrderStatus.ACKNOWLEDGED,
                    OrderStatus.PARTIALLY_FILLED,
                ]
                stmt = (
                    select(Order)
                    .where(
                        Order.strategy_id == container.strategy_id,
                        Order.status.in_(open_statuses),
                    )
                    .order_by(desc(Order.created_at))
                )
                result = await session.execute(stmt)
                db_orders = result.scalars().all()
                for o in db_orders:
                    orders.append(_map_order(o))
        except Exception as e:
            logger.error(
                f"Database query failed for open orders ({e}). Raising 503 Service Unavailable.",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Database service unavailable: {e}",
            ) from e

    return orders


@router.get("/history", response_model=list[OrderResponse])
async def get_order_history(
    limit: int = Query(default=50, ge=1, le=500),
    symbol: str | None = Query(default=None),
    container: PlatformContainer = Depends(get_container),
) -> list[OrderResponse]:
    """Retrieve historical orders with lifecycle status, execution fills, and fee accounting."""
    orders: list[OrderResponse] = []

    if container.session_factory:
        try:
            async with get_db_session(container.session_factory) as session:
                stmt = select(Order).where(Order.strategy_id == container.strategy_id)
                if symbol:
                    stmt = stmt.where(Order.symbol == symbol.upper())
                stmt = stmt.order_by(desc(Order.created_at)).limit(limit)

                result = await session.execute(stmt)
                db_orders = result.scalars().all()
                for o in db_orders:
                    orders.append(_map_order(o))
        except Exception as e:
            logger.error(
                f"Database query failed for order history ({e}). Raising 503 Service Unavailable.",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Database service unavailable: {e}",
            ) from e

    return orders


def _map_order(o: Order) -> OrderResponse:
    """Map DB Order model to API response schema."""
    rem_qty = max(0.0, o.quantity - (o.filled_qty or 0.0))
    created = o.created_at if o.created_at else datetime.now(UTC)
    updated = o.updated_at if o.updated_at else created

    latency_ms = None
    if o.updated_at and o.created_at:
        latency_ms = (o.updated_at - o.created_at).total_seconds() * 1000.0

    fee = o.fee
    if (fee is None or fee == 0.0) and (o.filled_qty and o.filled_qty > 0) and (o.avg_fill_price and o.avg_fill_price > 0):
        fee = round(o.filled_qty * o.avg_fill_price * 0.0004, 6)

    return OrderResponse(
        client_order_id=o.client_order_id,
        exchange_order_id=o.exchange_order_id,
        strategy_id=o.strategy_id,
        symbol=o.symbol,
        side=o.side,
        order_type=o.order_type,
        time_in_force=o.time_in_force,
        quantity=round(o.quantity, 6),
        price=round(o.price, 4) if o.price is not None else None,
        stop_price=round(o.stop_price, 4) if o.stop_price is not None else None,
        status=o.status,
        filled_qty=round(o.filled_qty, 6),
        remaining_qty=round(rem_qty, 6),
        avg_fill_price=round(o.avg_fill_price, 4),
        cum_quote=round(o.cum_quote, 2),
        fee_paid=round(fee or 0.0, 6),
        fee_asset=o.fee_asset or "USDT",
        latency_ms=round(latency_ms, 2) if latency_ms is not None else None,
        created_at=created,
        updated_at=updated,
    )
