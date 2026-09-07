"""Deterministic Order State Machine enforcing valid lifecycle transitions."""

from trading_platform.core.constants import OrderStatus
from trading_platform.core.exceptions import InvalidOrderStateTransitionError
from trading_platform.core.logging import get_logger

logger = get_logger("oms.state_machine")


class OrderStateMachine:
    """Enforces strict, deterministic order state transitions and prevents illegal updates."""

    # Explicit allowed transitions mapping: from_state -> set of valid to_states
    VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
        OrderStatus.CREATED: {
            OrderStatus.SUBMITTED,
            OrderStatus.REJECTED,
        },
        OrderStatus.SUBMITTED: {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.UNKNOWN,
        },
        OrderStatus.ACKNOWLEDGED: {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.UNKNOWN,
        },
        OrderStatus.PARTIALLY_FILLED: {
            OrderStatus.PARTIALLY_FILLED,  # Consecutive partial fills
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.UNKNOWN,
        },
        OrderStatus.UNKNOWN: {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        },
        # Terminal states - no outbound transitions allowed
        OrderStatus.FILLED: set(),
        OrderStatus.CANCELLED: set(),
        OrderStatus.REJECTED: set(),
        OrderStatus.EXPIRED: set(),
    }

    TERMINAL_STATES: set[OrderStatus] = {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }

    IN_FLIGHT_STATES: set[OrderStatus] = {
        OrderStatus.SUBMITTED,
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
    }

    @classmethod
    def can_transition(cls, from_state: OrderStatus, to_state: OrderStatus) -> bool:
        """Check if transition from from_state to to_state is valid."""
        if from_state == to_state:
            # Allow idempotency except on terminal state mutations
            return from_state == OrderStatus.PARTIALLY_FILLED
        return to_state in cls.VALID_TRANSITIONS.get(from_state, set())

    @classmethod
    def validate_transition(
        cls,
        client_order_id: str,
        from_state: OrderStatus,
        to_state: OrderStatus,
    ) -> None:
        """Validate state transition, raising InvalidOrderStateTransitionError if illegal."""
        if not cls.can_transition(from_state, to_state):
            msg = (
                f"Illegal order state transition for order '{client_order_id}': "
                f"Cannot transition from {from_state.value} to {to_state.value}."
            )
            logger.error(msg)
            raise InvalidOrderStateTransitionError(msg)

    @classmethod
    def is_terminal(cls, state: OrderStatus) -> bool:
        """Return True if state is terminal (order closed forever)."""
        return state in cls.TERMINAL_STATES

    @classmethod
    def is_in_flight(cls, state: OrderStatus) -> bool:
        """Return True if order is currently active and pending execution on exchange."""
        return state in cls.IN_FLIGHT_STATES
