"""Domain enumerations and constants for the trading platform."""

from enum import StrEnum


class TradingMode(StrEnum):
    """Execution modes for the trading engine."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


class SignalType(StrEnum):
    """Strategy signal direction."""

    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"


class OrderSide(StrEnum):
    """Order trade side."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """Supported order types."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class OrderStatus(StrEnum):
    """Full lifecycle states of an order."""

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class TimeInForce(StrEnum):
    """Order time-in-force policies."""

    GTC = "GTC"  # Good 'Til Cancelled
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill
    GTX = "GTX"  # Post Only


class MarginMode(StrEnum):
    """Margin allocation mode."""

    ISOLATED = "ISOLATED"
    CROSSED = "CROSSED"


class PositionSide(StrEnum):
    """Binance position side mode (BOTH for One-Way mode)."""

    BOTH = "BOTH"
    LONG = "LONG"
    SHORT = "SHORT"


class RiskDecisionType(StrEnum):
    """Risk engine signal evaluation outcomes."""

    APPROVED = "APPROVED"
    RESIZED = "RESIZED"
    REJECTED = "REJECTED"


class ReconciliationEventType(StrEnum):
    """Categories of reconciliation events."""

    BALANCE_MISMATCH = "BALANCE_MISMATCH"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    GHOST_ORDER = "GHOST_ORDER"
    MISSING_ORDER = "MISSING_ORDER"
    ORDER_MISMATCH = "ORDER_MISMATCH"
    FILL_MISMATCH = "FILL_MISMATCH"
    STATE_RESYNC = "STATE_RESYNC"


class ContractType(StrEnum):
    """Contract specification."""

    PERPETUAL = "PERPETUAL"
    CURRENT_QUARTER = "CURRENT_QUARTER"
    NEXT_QUARTER = "NEXT_QUARTER"
