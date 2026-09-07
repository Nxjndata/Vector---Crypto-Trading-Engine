"""Positions monitoring REST endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from trading_platform.api.container import PlatformContainer, get_container
from trading_platform.api.schemas import PositionResponse

router = APIRouter(prefix="/positions", tags=["Positions"])


@router.get("", response_model=list[PositionResponse])
async def get_positions(
    container: PlatformContainer = Depends(get_container),
) -> list[PositionResponse]:
    """Retrieve all active and recently open positions with real-time mark and liquidation metrics."""
    strategy_id = container.strategy_id
    pm = container.portfolio_manager
    positions = pm.get_all_positions(strategy_id)

    response: list[PositionResponse] = []
    now = datetime.now(UTC)

    for pos in positions:
        side_str = "FLAT"
        if pos.size > 0:
            side_str = "LONG"
        elif pos.size < 0:
            side_str = "SHORT"

        # Calculate unrealized PnL %
        upnl_pct = 0.0
        if pos.initial_margin > 0:
            upnl_pct = (pos.unrealized_pnl / pos.initial_margin) * 100.0

        liq_price = pos.liquidation_price or pos.calculated_liquidation_price
        liq_distance_pct = pos.liquidation_distance_pct

        response.append(
            PositionResponse(
                strategy_id=pos.strategy_id,
                symbol=pos.symbol,
                side=side_str,
                quantity=round(abs(pos.size), 6),
                entry_price=round(pos.entry_price, 4),
                mark_price=round(pos.mark_price, 4),
                unrealized_pnl=round(pos.unrealized_pnl, 2),
                unrealized_pnl_pct=round(upnl_pct, 2),
                notional=round(pos.notional, 2),
                initial_margin=round(pos.initial_margin, 2),
                maintenance_margin=round(pos.maintenance_margin, 2),
                liquidation_price=round(liq_price, 4) if liq_price else None,
                liquidation_distance_pct=round(liq_distance_pct, 2)
                if liq_distance_pct is not None
                else None,
                leverage=pos.leverage,
                margin_mode=pos.margin_mode,
                is_open=pos.is_open,
                last_updated=pos.updated_at if hasattr(pos, "updated_at") else now,
            )
        )

    return response
