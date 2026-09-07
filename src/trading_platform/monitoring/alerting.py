"""Non-blocking Webhook Alerting System for Slack, Telegram, and Generic Webhooks."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from trading_platform.core.config import AlertingConfig
from trading_platform.core.events import (
    EventBus,
    KillSwitchResetEvent,
    KillSwitchTriggeredEvent,
    ReconciliationResultEvent,
)
from trading_platform.core.logging import get_logger

logger = get_logger("monitoring.alerting")


class AlertDispatcher:
    """Dispatches high-priority system alerts asynchronously without blocking the hot execution path."""

    def __init__(
        self,
        config: AlertingConfig,
        event_bus: EventBus | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self._http_client = http_client
        self._owns_client = http_client is None

        if self.event_bus:
            self._subscribe_events()

    def _subscribe_events(self) -> None:
        """Attach listeners to platform EventBus."""
        if not self.event_bus:
            return
        self.event_bus.subscribe(KillSwitchTriggeredEvent, self._on_kill_switch_triggered)
        self.event_bus.subscribe(KillSwitchResetEvent, self._on_kill_switch_reset)
        self.event_bus.subscribe(ReconciliationResultEvent, self._on_reconciliation_result)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=5.0)
            self._owns_client = True
        return self._http_client

    async def close(self) -> None:
        """Close internal HTTP client."""
        if self._owns_client and self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def dispatch(
        self,
        title: str,
        message: str,
        severity: str = "CRITICAL",
        fields: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget dispatch of an alert in a background asyncio task.

        Guaranteed never to raise, block, or throw into the caller's execution stack.
        """
        if not self.config.enabled or not self.config.webhook_url:
            return

        try:
            asyncio.create_task(
                self._send_alert_safe(
                    title=title,
                    message=message,
                    severity=severity,
                    fields=fields,
                ),
                name=f"alert_{int(datetime.now(UTC).timestamp())}",
            )
        except RuntimeError:
            # Event loop not running (e.g. during immediate unit test teardown)
            pass

    async def _send_alert_safe(
        self,
        title: str,
        message: str,
        severity: str = "CRITICAL",
        fields: dict[str, Any] | None = None,
    ) -> None:
        """Internal worker sending formatted payload with full fault-isolation."""
        try:
            payload = self._build_payload(title, message, severity, fields or {})
            client = await self._get_client()

            headers = {"Content-Type": "application/json"}
            resp = await client.post(self.config.webhook_url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(f"Alert webhook returned HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                logger.info(f"Alert successfully dispatched to {self.config.webhook_type}: {title}")
        except Exception as e:
            # Completely swallow any network/formatting exceptions to protect trading pipeline
            logger.warning(f"Failed to dispatch alert webhook ({type(e).__name__}): {e}")

    def _build_payload(
        self,
        title: str,
        message: str,
        severity: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Construct vendor-specific payload (Slack, Telegram, Generic)."""
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        webhook_type = self.config.webhook_type.lower()

        color_map = {
            "INFO": "#2EB886",
            "WARNING": "#DAA520",
            "CRITICAL": "#FF0000",
            "EMERGENCY": "#8B0000",
        }
        color = color_map.get(severity.upper(), "#FF0000")

        if webhook_type == "slack":
            field_attachments = [
                {"title": k, "value": str(v), "short": True} for k, v in fields.items()
            ]
            return {
                "text": f"*{severity.upper()} Alert*: {title}",
                "attachments": [
                    {
                        "color": color,
                        "title": title,
                        "text": message,
                        "fields": field_attachments,
                        "footer": f"Binance Trading Platform • {now_str}",
                    }
                ],
            }
        elif webhook_type == "telegram":
            text_lines = [
                f"🚨 *[{severity.upper()}] {title}*",
                f"_{now_str}_",
                "",
                message,
            ]
            if fields:
                text_lines.append("")
                for k, v in fields.items():
                    text_lines.append(f"• *{k}*: `{v}`")

            payload: dict[str, Any] = {
                "text": "\n".join(text_lines),
                "parse_mode": "Markdown",
            }
            if self.config.telegram_chat_id:
                payload["chat_id"] = self.config.telegram_chat_id
            return payload
        else:
            # Generic JSON webhook
            return {
                "title": title,
                "message": message,
                "severity": severity,
                "timestamp": now_str,
                "fields": fields,
            }

    # --------------------------------------------------------------------------
    # EventBus Event Handlers
    # --------------------------------------------------------------------------
    async def _on_kill_switch_triggered(self, event: KillSwitchTriggeredEvent) -> None:
        self.dispatch(
            title="KILL-SWITCH ENGAGED - TRADING HALTED",
            message=f"Emergency kill-switch activated by operator/risk engine: {event.reason}",
            severity="EMERGENCY",
            fields={
                "Reason": event.reason,
                "Timestamp": event.timestamp.isoformat(),
                "Status": "ALL TRADING HALTED",
            },
        )

    async def _on_kill_switch_reset(self, event: KillSwitchResetEvent) -> None:
        self.dispatch(
            title="KILL-SWITCH DISENGAGED - TRADING RESUMED",
            message=f"Emergency kill-switch manually reset by operator: {event.reason}",
            severity="INFO",
            fields={
                "Reason": event.reason,
                "Timestamp": event.timestamp.isoformat(),
                "Status": "NORMAL TRADING OPERATIONAL",
            },
        )

    async def _on_reconciliation_result(self, event: ReconciliationResultEvent) -> None:
        critical_mismatches = [
            m
            for m in event.mismatches
            if getattr(m.severity, "value", str(m.severity)).upper() == "CRITICAL"
        ]
        if critical_mismatches or event.is_breached:
            self.dispatch(
                title="RECONCILIATION HARD BREACH DETECTED",
                message=f"Reconciliation engine detected {len(event.mismatches)} discrepancies against exchange ground-truth.",
                severity="CRITICAL",
                fields={
                    "Strategy ID": event.strategy_id,
                    "Total Mismatches": len(event.mismatches),
                    "Critical Count": len(critical_mismatches),
                    "Action": "HALTING TRADING & ALERTING OPERATOR",
                },
            )

    def alert_websocket_dropout(self, duration_seconds: float) -> None:
        """Triggered when exchange WebSocket connection is lost beyond threshold duration."""
        self.dispatch(
            title="EXCHANGE WEBSOCKET DISCONNECTED",
            message=f"Exchange market data stream disconnected for {duration_seconds:.1f}s (Threshold: {self.config.ws_disconnect_threshold_seconds}s).",
            severity="WARNING",
            fields={
                "Duration Seconds": round(duration_seconds, 1),
                "Threshold Seconds": self.config.ws_disconnect_threshold_seconds,
            },
        )

    def alert_unhandled_exception(self, path: str, error: str, traceback_str: str) -> None:
        """Triggered when an unhandled 500 error occurs in the application."""
        self.dispatch(
            title=f"UNHANDLED EXCEPTION ON {path}",
            message=f"Unhandled internal server exception: {error}",
            severity="CRITICAL",
            fields={
                "Endpoint": path,
                "Error": error[:100],
                "Traceback": traceback_str[:300],
            },
        )
