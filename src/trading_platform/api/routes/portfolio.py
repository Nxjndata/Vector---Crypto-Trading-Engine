"""Portfolio monitoring REST endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from trading_platform.api.container import PlatformContainer, get_container
from trading_platform.api.schemas import PortfolioResponse

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get("", response_model=PortfolioResponse)
async def get_portfolio(container: PlatformContainer = Depends(get_container)) -> PortfolioResponse:
    """Retrieve real-time consolidated portfolio state, equity, and PnL breakdown."""
    strategy_id = container.strategy_id
    pm = container.portfolio_manager
    state = pm.get_portfolio(strategy_id)

    # Compute open orders count
    open_orders = 0
    if container.oms:
        open_orders = len(container.oms._in_flight_orders)

    total_pnl = state.realized_pnl + state.unrealized_pnl + state.funding_pnl

    lev = (state.total_exposure / state.equity) if state.equity > 0 else 0.0

    return PortfolioResponse(
        strategy_id=strategy_id,
        equity=round(state.equity, 2),
        wallet_balance=round(state.wallet_balance, 2),
        available_balance=round(state.available_balance, 2),
        margin_used=round(state.total_initial_margin, 2),
        unrealized_pnl=round(state.unrealized_pnl, 2),
        realized_pnl=round(state.realized_pnl, 2),
        funding_pnl=round(state.funding_pnl, 2),
        total_pnl=round(total_pnl, 2),
        peak_equity=round(state.peak_equity, 2),
        drawdown_pct=round(state.drawdown_pct, 2),
        total_exposure=round(state.total_exposure, 2),
        effective_leverage=round(lev, 2),
        position_count=len(pm.get_all_positions(strategy_id)),
        open_orders_count=open_orders,
        timestamp=datetime.now(UTC),
    )
