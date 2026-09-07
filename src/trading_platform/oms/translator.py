"""Translates approved risk decisions into concrete exchange order requests."""

from dataclasses import dataclass

from trading_platform.core.constants import (
    OrderSide,
    OrderType,
    RiskDecisionType,
    TimeInForce,
)
from trading_platform.core.events import RiskDecisionEvent
from trading_platform.core.exceptions import OrderValidationError
from trading_platform.core.logging import get_logger
from trading_platform.exchange.adapter import OrderRequest
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.portfolio.manager import PortfolioManager

logger = get_logger("oms.translator")


@dataclass
class TranslatedOrderIntent:
    """Calculated concrete order intent derived from approved risk decisions."""

    strategy_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None
    time_in_force: TimeInForce
    mark_price: float
    target_position_size: float
    current_position_size: float


class ApprovedIntentTranslator:
    """Translates RiskDecisionEvent into concrete, precision-validated OrderRequest objects."""

    def __init__(
        self,
        portfolio_manager: PortfolioManager,
        instrument_manager: InstrumentManager | None = None,
    ) -> None:
        self.portfolio_manager = portfolio_manager
        self.instrument_manager = instrument_manager

    def translate_to_order_request(
        self,
        decision: RiskDecisionEvent,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
    ) -> OrderRequest | None:
        """Convert an APPROVED or RESIZED RiskDecisionEvent into a precision-validated OrderRequest.

        Returns None if decision was REJECTED or delta quantity rounds to zero.
        Raises OrderValidationError if instrument precision/min_notional cannot be satisfied.
        """
        if decision.decision_type == RiskDecisionType.REJECTED:
            logger.debug(
                f"Ignoring REJECTED decision for {decision.symbol} ({decision.strategy_id})."
            )
            return None

        strat_id = decision.strategy_id
        symbol = decision.symbol.upper()

        portfolio = self.portfolio_manager.get_portfolio(strat_id)
        current_pos = self.portfolio_manager.get_position(strat_id, symbol)
        current_size = current_pos.size if current_pos else 0.0

        equity = portfolio.equity
        mark_price = decision.snapshot_data.get("mark_price", 0.0)
        if mark_price <= 0:
            pos_mark = current_pos.mark_price if current_pos else 0.0
            mark_price = pos_mark if pos_mark > 0 else 1.0

        # Calculate target position size in units
        approved_exp = decision.approved_target_exposure
        target_notional = abs(approved_exp) * equity
        direction = 1.0 if approved_exp > 0 else (-1.0 if approved_exp < 0 else 0.0)
        target_size = (target_notional / mark_price) * direction if mark_price > 0 else 0.0

        # Calculate delta quantity needed to reach target position
        delta_qty = target_size - current_size

        if abs(delta_qty) < 1e-8:
            logger.debug(
                f"Position for {symbol} ({strat_id}) is already at target ({target_size:.4f}). No order needed."
            )
            return None

        side = OrderSide.BUY if delta_qty > 0 else OrderSide.SELL
        raw_quantity = abs(delta_qty)

        # Run instrument precision rounding and validation
        if self.instrument_manager:
            rounded_qty = self.instrument_manager.round_quantity(symbol, raw_quantity)
            rounded_price = (
                self.instrument_manager.round_price(symbol, price or mark_price)
                if (price or mark_price)
                else None
            )

            if rounded_qty <= 0:
                logger.warning(
                    f"Calculated quantity ({raw_quantity:.6f}) rounded down to zero for {symbol}."
                )
                return None

            # Validate client-side before submission
            try:
                self.instrument_manager.validate_order_precision(
                    symbol=symbol,
                    quantity=rounded_qty,
                    price=rounded_price,
                )
            except OrderValidationError as e:
                logger.error(f"Client-side pre-submission validation failed for {symbol}: {e}")
                raise

            final_qty = float(rounded_qty)
            final_price = (
                float(price) if (order_type == OrderType.LIMIT and price is not None) else None
            )
        else:
            final_qty = float(raw_quantity)
            final_price = (
                float(price) if (order_type == OrderType.LIMIT and price is not None) else None
            )

        return OrderRequest(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=final_qty,
            price=final_price,
            time_in_force=time_in_force,
            reduce_only=False,
        )
