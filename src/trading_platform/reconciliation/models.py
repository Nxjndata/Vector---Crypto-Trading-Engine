"""Reconciliation data structures, tolerances, and discrepancy models."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from trading_platform.core.constants import ReconciliationEventType


class DiscrepancySeverity(StrEnum):
    """Action severity level for reconciliation discrepancies."""

    LOG_ONLY = "LOG_ONLY"
    AUTO_RESYNC = "AUTO_RESYNC"
    CRITICAL_HALT = "CRITICAL_HALT"


@dataclass
class ReconciliationToleranceConfig:
    """Configurable tolerance thresholds for mismatch classification."""

    balance_drift_tolerance_usd: float = 1.0  # Drift <= $1.00 is minor
    balance_critical_threshold_usd: float = 100.0  # Drift > $100.00 is critical halt
    position_qty_tolerance: float = 1e-5  # Rounding tolerance for position quantities
    interval_seconds: float = 30.0  # Reconciliation loop period


@dataclass
class DiscrepancyReport:
    """Detailed classification and recommended action for a detected state discrepancy."""

    event_type: ReconciliationEventType
    severity: DiscrepancySeverity
    symbol: str | None
    local_state: dict[str, Any]
    remote_state: dict[str, Any]
    details: str
    action_taken: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
