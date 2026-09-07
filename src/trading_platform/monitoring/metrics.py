"""Prometheus-compatible real-time metrics registry and EventBus telemetry collector."""

import threading
from collections import defaultdict
from typing import Any

from trading_platform.core.constants import RiskDecisionType
from trading_platform.core.events import (
    EventBus,
    FillEvent,
    KillSwitchResetEvent,
    KillSwitchTriggeredEvent,
    OrderEvent,
    PortfolioUpdateEvent,
    ReconciliationResultEvent,
    RiskDecisionEvent,
    SignalEvent,
)
from trading_platform.core.logging import get_logger

logger = get_logger("monitoring.metrics")


class PrometheusRegistry:
    """Thread-safe, zero-allocation metrics registry emitting standard Prometheus exposition text."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(
            lambda: defaultdict(float)
        )
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(
            lambda: defaultdict(float)
        )
        self._histograms: dict[str, dict[tuple[tuple[str, str], ...], dict[str, Any]]] = (
            defaultdict(dict)
        )
        self._descriptions: dict[str, tuple[str, str]] = {}  # name -> (help, type)

        # Default histogram buckets for execution latency in seconds
        self._default_latency_buckets = (
            0.001,
            0.005,
            0.010,
            0.025,
            0.050,
            0.100,
            0.250,
            0.500,
            1.000,
            2.500,
            5.000,
        )

        self._register_default_metrics()

    def _register_default_metrics(self) -> None:
        """Define standard platform observability metrics."""
        self._descriptions["trading_order_execution_latency_seconds"] = (
            "Order submission to acknowledgment round-trip latency in seconds",
            "histogram",
        )
        self._descriptions["trading_signals_total"] = (
            "Total strategy signals processed by intent outcome",
            "counter",
        )
        self._descriptions["trading_risk_rejections_total"] = (
            "Total risk rule violations preventing order execution",
            "counter",
        )
        self._descriptions["trading_reconciliation_mismatches_total"] = (
            "Total state reconciliation discrepancies categorized by severity",
            "counter",
        )
        self._descriptions["trading_websocket_connections_active"] = (
            "Current active client WebSocket connections to monitoring API",
            "gauge",
        )
        self._descriptions["trading_websocket_reconnects_total"] = (
            "Total exchange WebSocket stream reconnection events",
            "counter",
        )
        self._descriptions["trading_kill_switch_state"] = (
            "Current platform kill switch status (0 = Inactive/Trading, 1 = Active/Halted)",
            "gauge",
        )
        self._descriptions["trading_portfolio_equity_usd"] = (
            "Total portfolio equity in USD based on mark price valuation",
            "gauge",
        )
        self._descriptions["trading_portfolio_unrealized_pnl_usd"] = (
            "Aggregate unrealized PnL in USD across all active positions",
            "gauge",
        )

    def _normalize_labels(self, labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        if not labels:
            return ()
        return tuple(sorted((str(k), str(v)) for k, v in labels.items()))

    def inc_counter(
        self, name: str, value: float = 1.0, labels: dict[str, str] | None = None
    ) -> None:
        """Increment a counter metric."""
        lbl_key = self._normalize_labels(labels)
        with self._lock:
            self._counters[name][lbl_key] += value

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge metric value."""
        lbl_key = self._normalize_labels(labels)
        with self._lock:
            self._gauges[name][lbl_key] = float(value)

    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        """Record an observation in a histogram metric."""
        lbl_key = self._normalize_labels(labels)
        target_buckets = buckets or self._default_latency_buckets
        with self._lock:
            if lbl_key not in self._histograms[name]:
                self._histograms[name][lbl_key] = {
                    "buckets": dict.fromkeys(target_buckets, 0),
                    "count": 0,
                    "sum": 0.0,
                }
            hist = self._histograms[name][lbl_key]
            hist["count"] += 1
            hist["sum"] += value
            for b in target_buckets:
                if value <= b:
                    hist["buckets"][b] += 1

    def generate_prometheus_text(self) -> str:
        """Export all registered metrics in Prometheus text exposition format (version 0.0.4)."""
        lines: list[str] = []

        with self._lock:
            all_metric_names = sorted(
                set(self._counters.keys())
                | set(self._gauges.keys())
                | set(self._histograms.keys())
                | set(self._descriptions.keys())
            )

            for name in all_metric_names:
                help_text, metric_type = self._descriptions.get(name, (f"Metric {name}", "untyped"))
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} {metric_type}")

                # Counters
                if name in self._counters:
                    for labels_tuple, val in sorted(self._counters[name].items()):
                        lbl_str = self._format_label_str(labels_tuple)
                        lines.append(f"{name}{lbl_str} {val}")
                elif metric_type == "counter" and name not in self._counters:
                    lines.append(f"{name} 0")

                # Gauges
                if name in self._gauges:
                    for labels_tuple, val in sorted(self._gauges[name].items()):
                        lbl_str = self._format_label_str(labels_tuple)
                        lines.append(f"{name}{lbl_str} {val}")
                elif metric_type == "gauge" and name not in self._gauges:
                    lines.append(f"{name} 0")

                # Histograms
                if name in self._histograms:
                    for labels_tuple, hist in sorted(self._histograms[name].items()):
                        base_lbls = dict(labels_tuple)
                        for b, count in sorted(hist["buckets"].items()):
                            bucket_lbls = dict(base_lbls)
                            bucket_lbls["le"] = str(b)
                            lbl_str = self._format_label_str(self._normalize_labels(bucket_lbls))
                            lines.append(f"{name}_bucket{lbl_str} {count}")
                        # +Inf bucket
                        inf_lbls = dict(base_lbls)
                        inf_lbls["le"] = "+Inf"
                        lines.append(
                            f"{name}_bucket{self._format_label_str(self._normalize_labels(inf_lbls))} {hist['count']}"
                        )
                        base_lbl_str = self._format_label_str(labels_tuple)
                        lines.append(f"{name}_sum{base_lbl_str} {round(hist['sum'], 6)}")
                        lines.append(f"{name}_count{base_lbl_str} {hist['count']}")

        return "\n".join(lines) + "\n"

    def _format_label_str(self, labels_tuple: tuple[tuple[str, str], ...]) -> str:
        if not labels_tuple:
            return ""
        kvs = [f'{k}="{v}"' for k, v in labels_tuple]
        return "{" + ",".join(kvs) + "}"


# Global Platform Metrics Registry
METRICS = PrometheusRegistry()


class MetricsCollector:
    """Attaches to the platform EventBus to update Prometheus metrics asynchronously with 0 hot-path overhead."""

    def __init__(self, event_bus: EventBus, registry: PrometheusRegistry = METRICS) -> None:
        self.event_bus = event_bus
        self.registry = registry
        self._subscribe_events()

    def _subscribe_events(self) -> None:
        """Register EventBus handlers for automatic metric updates."""
        self.event_bus.subscribe(SignalEvent, self._on_signal)
        self.event_bus.subscribe(RiskDecisionEvent, self._on_risk_decision)
        self.event_bus.subscribe(OrderEvent, self._on_order_event)
        self.event_bus.subscribe(FillEvent, self._on_fill_event)
        self.event_bus.subscribe(ReconciliationResultEvent, self._on_reconciliation_result)
        self.event_bus.subscribe(KillSwitchTriggeredEvent, self._on_kill_switch_triggered)
        self.event_bus.subscribe(KillSwitchResetEvent, self._on_kill_switch_reset)
        self.event_bus.subscribe(PortfolioUpdateEvent, self._on_portfolio_update)

    async def _on_signal(self, event: SignalEvent) -> None:
        self.registry.inc_counter(
            "trading_signals_total",
            labels={
                "strategy_id": event.strategy_id,
                "symbol": event.symbol,
                "outcome": "received",
            },
        )

    async def _on_risk_decision(self, event: RiskDecisionEvent) -> None:
        dec_type = getattr(event, "decision_type", getattr(event, "decision", None))
        outcome = "approved"
        if dec_type in (RiskDecisionType.REJECTED, "REJECTED", "REJECT"):
            outcome = "rejected"
            self.registry.inc_counter(
                "trading_risk_rejections_total",
                labels={"rule": event.reason or "unknown"},
            )
        elif dec_type in (RiskDecisionType.RESIZED, "RESIZED", "RESIZE"):
            outcome = "resized"

        self.registry.inc_counter(
            "trading_signals_total",
            labels={
                "strategy_id": event.strategy_id,
                "symbol": event.symbol,
                "outcome": outcome,
            },
        )

    async def _on_order_event(self, event: OrderEvent) -> None:
        # If latency is present in event or updated vs created
        if getattr(event, "latency_ms", None) is not None:
            self.registry.observe_histogram(
                "trading_order_execution_latency_seconds",
                value=event.latency_ms / 1000.0,
                labels={"symbol": event.symbol, "status": event.status.value},
            )

    async def _on_fill_event(self, event: FillEvent) -> None:
        pass

    async def _on_reconciliation_result(self, event: ReconciliationResultEvent) -> None:
        for mismatch in event.mismatches:
            self.registry.inc_counter(
                "trading_reconciliation_mismatches_total",
                labels={
                    "classification": mismatch.classification.value,
                    "severity": mismatch.severity.value,
                },
            )

    async def _on_kill_switch_triggered(self, event: KillSwitchTriggeredEvent) -> None:
        self.registry.set_gauge("trading_kill_switch_state", 1.0)

    async def _on_kill_switch_reset(self, event: KillSwitchResetEvent) -> None:
        self.registry.set_gauge("trading_kill_switch_state", 0.0)

    async def _on_portfolio_update(self, event: PortfolioUpdateEvent) -> None:
        self.registry.set_gauge("trading_portfolio_equity_usd", event.equity)
        self.registry.set_gauge("trading_portfolio_unrealized_pnl_usd", event.unrealized_pnl)
