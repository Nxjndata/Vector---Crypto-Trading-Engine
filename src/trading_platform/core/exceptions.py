"""Standard custom exceptions for the trading platform."""


class PlatformException(Exception):
    """Base exception for all trading platform errors."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(PlatformException):
    """Raised when configuration fails loading, validation, or violates safety rules."""

    pass


class ClockDriftError(PlatformException):
    """Raised when system clock drift exceeds safety tolerance."""

    pass


class DatabaseError(PlatformException):
    """Raised on database connection, migration, or query execution failure."""

    pass


class EventBusError(PlatformException):
    """Raised when event publishing or subscriber handling fails."""

    pass


class InstrumentNotFoundError(PlatformException):
    """Raised when an instrument is not found in the universe."""

    pass


class OrderValidationError(PlatformException):
    """Raised when an order fails client-side precision, min notional, or tick validation."""

    pass


class OrderNotFoundError(PlatformException):
    """Raised when an order ID is unknown or cannot be found on the exchange."""

    pass


class InvalidOrderStateTransitionError(PlatformException):
    """Raised when an illegal or non-deterministic order state transition is attempted."""

    pass


class DuplicateOrderError(PlatformException):
    """Raised when a duplicate order or concurrent in-flight order is submitted."""

    pass


class InsufficientMarginError(PlatformException):
    """Raised when account has insufficient margin balance to place or maintain an order."""

    pass


class RateLimitExceededError(PlatformException):
    """Raised when exchange rate limits are exceeded or proactive hard stop is triggered."""

    pass


class RiskCheckError(PlatformException):
    """Raised when a hard risk check threshold is violated or emergency stop triggered."""

    pass


class ReconciliationError(PlatformException):
    """Raised when state reconciliation detects critical mismatches with the exchange."""

    pass


class ExchangeConnectionError(PlatformException):
    """Raised when REST or WebSocket connection to the exchange fails."""

    pass
