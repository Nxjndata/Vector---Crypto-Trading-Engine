"""Tests for Prometheus metrics collection, latency benchmarking, and webhook alerting."""

import json
import time
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from trading_platform.core.config import AlertingConfig, AppConfig
from trading_platform.core.constants import OrderSide, OrderStatus, OrderType, RiskDecisionType, SignalType, TimeInForce
from trading_platform.core.events import (
    EventBus,
    KillSwitchTriggeredEvent,
    RiskDecisionEvent,
    SignalEvent,
)
from trading_platform.exchange.adapter import ExchangeAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.models.instrument import Instrument
from trading_platform.models.order import Order
from trading_platform.monitoring.alerting import AlertDispatcher
from trading_platform.monitoring.metrics import MetricsCollector, PrometheusRegistry
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.risk.engine import RiskEngine


def test_prometheus_metrics_generation():
    """Verify PrometheusRegistry formats metrics correctly in standard text exposition format."""
    registry = PrometheusRegistry()
    registry.inc_counter(
        "trading_signals_total", value=5, labels={"strategy_id": "s1", "outcome": "approved"}
    )
    registry.set_gauge("trading_kill_switch_state", value=1.0)
    registry.observe_histogram(
        "trading_order_execution_latency_seconds", value=0.015, labels={"symbol": "BTCUSDT"}
    )

    output = registry.generate_prometheus_text()
    assert "# HELP trading_signals_total" in output
    assert "# TYPE trading_signals_total counter" in output
    assert 'trading_signals_total{outcome="approved",strategy_id="s1"} 5' in output
    assert "trading_kill_switch_state 1" in output
    assert 'trading_order_execution_latency_seconds_bucket{le="0.025",symbol="BTCUSDT"} 1' in output
    assert 'trading_order_execution_latency_seconds_count{symbol="BTCUSDT"} 1' in output


def test_isolated_metrics_recording_latency():
    """BENCHMARK 1 (Isolated Microbenchmark): Direct metric observation overhead."""
    registry = PrometheusRegistry()

    for _ in range(100):
        registry.inc_counter("trading_signals_total", labels={"outcome": "approved"})

    iterations = 50000
    start = time.perf_counter()
    for _ in range(iterations):
        registry.inc_counter(
            "trading_signals_total",
            labels={"strategy_id": "strat_v1", "outcome": "approved"},
        )
        registry.observe_histogram("trading_order_execution_latency_seconds", value=0.012)
    elapsed = time.perf_counter() - start

    avg_latency_microseconds = (elapsed / iterations) * 1_000_000
    print(
        f"\n[BENCHMARK 1 - Microbenchmark] Average latency per direct metric observation: {avg_latency_microseconds:.3f} µs"
    )
    assert avg_latency_microseconds < 10.0


@pytest.mark.asyncio
async def test_realistic_pipeline_latency_benchmark_with_and_without_metrics():
    """BENCHMARK 2 (Full Pipeline Benchmark): Measures end-to-end Signal -> Risk -> OMS pipeline
    execution latency with MetricsCollector enabled vs disabled across 1000 signal evaluations.
    """
    iterations = 1000
    symbol = "BTCUSDT"
    strat_id = "bench_strat_v1"

    config = AppConfig()
    mock_adapter = AsyncMock(spec=ExchangeAdapter)
    mock_adapter.get_markets.return_value = [
        Instrument(
            symbol=symbol,
            base_asset="BTC",
            quote_asset="USDT",
            tick_size=Decimal("0.10"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5.0"),
            is_active=True,
        )
    ]
    mock_adapter.place_order.side_effect = lambda req: Order(
        client_order_id=req.client_order_id or "c_test",
        exchange_order_id="ex_123",
        strategy_id=strat_id,
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        time_in_force=TimeInForce.GTC,
        quantity=req.quantity,
        price=req.price,
        status=OrderStatus.FILLED,
    )

    # --- Pipeline A: WITHOUT Metrics Collector ---
    bus_without = EventBus()
    im_without = InstrumentManager(config, mock_adapter)
    await im_without.initialize()
    pm_without = PortfolioManager(bus_without, session_factory=None, default_deposit_usd=100000.0)
    _ = RiskEngine(event_bus=bus_without, portfolio_manager=pm_without, instrument_manager=im_without)
    _ = OrderManagementSystem(
        event_bus=bus_without,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm_without,
        instrument_manager=im_without,
        session_factory=None,
    )

    # --- Pipeline B: WITH Metrics Collector Enabled ---
    bus_with = EventBus()
    im_with = InstrumentManager(config, mock_adapter)
    await im_with.initialize()
    pm_with = PortfolioManager(bus_with, session_factory=None, default_deposit_usd=100000.0)
    _ = RiskEngine(event_bus=bus_with, portfolio_manager=pm_with, instrument_manager=im_with)
    _ = OrderManagementSystem(
        event_bus=bus_with,
        exchange_adapter=mock_adapter,
        portfolio_manager=pm_with,
        instrument_manager=im_with,
        session_factory=None,
    )
    registry = PrometheusRegistry()
    _ = MetricsCollector(event_bus=bus_with, registry=registry)

    # Warmup both pipelines
    for _ in range(100):
        sig = SignalEvent(strategy_id=strat_id, symbol=symbol, signal_type=SignalType.BUY, target_exposure=0.01, mark_price=70000.0)
        await bus_without.publish(sig)
        await bus_with.publish(sig)

    # Interleaved benchmark execution to eliminate order-of-execution JIT bias
    durations_without = []
    durations_with = []

    for _ in range(iterations):
        sig = SignalEvent(strategy_id=strat_id, symbol=symbol, signal_type=SignalType.BUY, target_exposure=0.01, mark_price=70000.0)

        t0 = time.perf_counter()
        await bus_without.publish(sig)
        durations_without.append(time.perf_counter() - t0)

        t1 = time.perf_counter()
        await bus_with.publish(sig)
        durations_with.append(time.perf_counter() - t1)

    avg_without_us = (sum(durations_without) / len(durations_without)) * 1_000_000
    avg_with_us = (sum(durations_with) / len(durations_with)) * 1_000_000
    overhead_delta_us = avg_with_us - avg_without_us

    print("\n[BENCHMARK 2 - Full Pipeline Interleaved Overhead Benchmark]")
    print(f"   -> Average Pipeline Latency WITHOUT metrics: {avg_without_us:.3f} µs/signal")
    print(f"   -> Average Pipeline Latency WITH metrics:    {avg_with_us:.3f} µs/signal")
    print(f"   -> Real Pipeline Latency Delta (Overhead):   {overhead_delta_us:+.3f} µs/signal ({overhead_delta_us/1000:+.4f} ms)")

    # Invariant: Metrics overhead on complete end-to-end pipeline must be sub-0.05 ms (< 50 µs)
    assert abs(overhead_delta_us) < 50.0


@pytest.mark.asyncio
async def test_metrics_collector_event_bus_integration():
    """Verify EventBus domain events update Prometheus metrics automatically."""
    event_bus = EventBus()
    registry = PrometheusRegistry()
    _ = MetricsCollector(event_bus, registry=registry)

    # 1. Publish SignalEvent
    await event_bus.publish(
        SignalEvent(
            strategy_id="sma_v1",
            symbol="ETHUSDT",
            signal_type=SignalType.BUY,
            target_exposure=0.05,
            mark_price=2450.0,
        )
    )

    # 2. Publish RiskDecisionEvent (Rejection)
    await event_bus.publish(
        RiskDecisionEvent(
            decision_id="dec_01",
            signal_id="sig_01",
            strategy_id="sma_v1",
            symbol="ETHUSDT",
            decision_type=RiskDecisionType.REJECTED,
            original_target_exposure=0.05,
            approved_target_exposure=0.0,
            reason="MAX_LEVERAGE_EXCEEDED",
        )
    )

    # 3. Publish KillSwitchTriggeredEvent
    await event_bus.publish(KillSwitchTriggeredEvent(reason="Operator emergency stop"))

    output = registry.generate_prometheus_text()
    assert 'trading_signals_total{outcome="received",strategy_id="sma_v1",symbol="ETHUSDT"} 1' in output
    assert 'trading_signals_total{outcome="rejected",strategy_id="sma_v1",symbol="ETHUSDT"} 1' in output
    assert 'trading_risk_rejections_total{rule="MAX_LEVERAGE_EXCEEDED"} 1' in output
    assert "trading_kill_switch_state 1" in output


@pytest.mark.asyncio
async def test_webhook_alerting_slack_format_and_payload():
    """Verify Slack formatted webhook payload on Kill Switch trigger and print exact payload."""
    config = AlertingConfig(
        enabled=True,
        webhook_url="https://hooks.slack.com/services/MOCK/SLACK/WEBHOOK",
        webhook_type="slack",
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = httpx.Response(status_code=200)

    dispatcher = AlertDispatcher(config=config, http_client=mock_client)
    await dispatcher._send_alert_safe(
        title="KILL-SWITCH ENGAGED",
        message="Maximum drawdown limit breached (12.4% > 10.0%)",
        severity="EMERGENCY",
        fields={
            "Strategy": "momentum_trend_v1",
            "Max Allowed Drawdown": "10.0%",
            "Current Drawdown": "12.4%",
            "Action Taken": "All trading halted, open orders cancelled",
        },
    )

    assert mock_client.post.called
    call_args = mock_client.post.call_args
    json_payload = call_args[1]["json"]
    print("\n[SLACK WEBHOOK PAYLOAD CAPTURED]")
    print(json.dumps(json_payload, indent=2))

    assert "*EMERGENCY Alert*: KILL-SWITCH ENGAGED" in json_payload["text"]
    assert json_payload["attachments"][0]["color"] == "#8B0000"
    assert len(json_payload["attachments"][0]["fields"]) == 4


@pytest.mark.asyncio
async def test_webhook_alerting_telegram_format_and_payload():
    """Verify Telegram formatted webhook payload on Critical Reconciliation Breach and print exact payload."""
    config = AlertingConfig(
        enabled=True,
        webhook_url="https://api.telegram.org/bot123456:MOCK/sendMessage",
        webhook_type="telegram",
        telegram_chat_id="-100987654321",
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = httpx.Response(status_code=200)

    dispatcher = AlertDispatcher(config=config, http_client=mock_client)
    await dispatcher._send_alert_safe(
        title="CRITICAL RECONCILIATION BREACH",
        message="Exchange position mismatch detected during periodic audit",
        severity="CRITICAL",
        fields={
            "Symbol": "BTCUSDT",
            "Local Position Size": "0.5000 BTC",
            "Exchange Ground-Truth": "0.7000 BTC",
            "Drift Delta": "+0.2000 BTC",
            "Action Taken": "EMERGENCY HALT TRIGGERED",
        },
    )

    assert mock_client.post.called
    json_payload = mock_client.post.call_args[1]["json"]
    print("\n[TELEGRAM WEBHOOK PAYLOAD CAPTURED]")
    print(json.dumps(json_payload, indent=2))

    assert json_payload["chat_id"] == "-100987654321"
    assert "🚨 *[CRITICAL] CRITICAL RECONCILIATION BREACH*" in json_payload["text"]
    assert "• *Symbol*: `BTCUSDT`" in json_payload["text"]
    assert "• *Drift Delta*: `+0.2000 BTC`" in json_payload["text"]


@pytest.mark.asyncio
async def test_webhook_alerting_fault_isolation():
    """CRITICAL SAFETY TEST: Verify webhook network timeout/failure NEVER raises into the trading pipeline."""
    config = AlertingConfig(
        enabled=True,
        webhook_url="https://broken-network-destination.invalid/webhook",
        webhook_type="slack",
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.side_effect = httpx.ConnectTimeout("Connection to Slack timed out")

    dispatcher = AlertDispatcher(config=config, http_client=mock_client)

    # Calling _send_alert_safe directly should swallow the error without raising
    await dispatcher._send_alert_safe(
        title="SYSTEM TEST ALERT",
        message="Testing fault isolation",
        severity="WARNING",
    )
    assert mock_client.post.called
