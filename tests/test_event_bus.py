"""Tests for the asynchronous in-process EventBus."""

import pytest

from trading_platform.core.constants import OrderSide, OrderStatus, OrderType, SignalType
from trading_platform.core.events import (
    Event,
    EventBus,
    OrderEvent,
    SignalEvent,
    SystemStatusEvent,
)


@pytest.mark.asyncio
async def test_event_bus_single_subscription(event_bus: EventBus):
    """Verify an async handler receives a published event."""
    received_events: list[SignalEvent] = []

    async def on_signal(event: SignalEvent) -> None:
        received_events.append(event)

    event_bus.subscribe(SignalEvent, on_signal)

    signal = SignalEvent(
        strategy_id="strat_1",
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.05,
        mark_price=64000.0,
    )
    await event_bus.publish(signal)

    assert len(received_events) == 1
    assert received_events[0].symbol == "BTCUSDT"
    assert received_events[0].target_exposure == 0.05
    assert event_bus.metrics["published"]["SignalEvent"] == 1


@pytest.mark.asyncio
async def test_event_bus_multiple_subscribers(event_bus: EventBus):
    """Verify multiple handlers receive the same event concurrently."""
    h1_called = False
    h2_called = False

    async def handler_one(event: OrderEvent) -> None:
        nonlocal h1_called
        h1_called = True

    async def handler_two(event: OrderEvent) -> None:
        nonlocal h2_called
        h2_called = True

    event_bus.subscribe(OrderEvent, handler_one)
    event_bus.subscribe(OrderEvent, handler_two)

    order_event = OrderEvent(
        client_order_id="test_ord_1",
        strategy_id="strat_1",
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1.0,
        price=3400.0,
        status=OrderStatus.SUBMITTED,
    )
    await event_bus.publish(order_event)

    assert h1_called is True
    assert h2_called is True


@pytest.mark.asyncio
async def test_event_bus_polymorphic_subscription(event_bus: EventBus):
    """Verify subscribing to base Event receives all specialized event types."""
    received_all: list[Event] = []

    async def on_any_event(event: Event) -> None:
        received_all.append(event)

    event_bus.subscribe(Event, on_any_event)

    await event_bus.publish(
        SignalEvent(
            strategy_id="strat_1",
            symbol="BTCUSDT",
            signal_type=SignalType.BUY,
            target_exposure=0.10,
            mark_price=60000.0,
        )
    )
    await event_bus.publish(
        SystemStatusEvent(
            component="TestComp",
            status="OK",
            message="Testing polymorphism",
        )
    )

    assert len(received_all) == 2


@pytest.mark.asyncio
async def test_event_bus_error_isolation(event_bus: EventBus):
    """Verify an error in one handler does not crash the bus or block other handlers."""
    h2_executed = False

    async def failing_handler(event: SignalEvent) -> None:
        raise RuntimeError("Simulated handler crash")

    async def successful_handler(event: SignalEvent) -> None:
        nonlocal h2_executed
        h2_executed = True

    event_bus.subscribe(SignalEvent, failing_handler)
    event_bus.subscribe(SignalEvent, successful_handler)

    signal = SignalEvent(
        strategy_id="strat_1",
        symbol="SOLUSDT",
        signal_type=SignalType.SELL,
        target_exposure=0.0,
        mark_price=140.0,
    )
    # Should not raise exception
    await event_bus.publish(signal)

    assert h2_executed is True
    assert event_bus.metrics["errors"]["SignalEvent"] == 1


@pytest.mark.asyncio
async def test_event_bus_unsubscribe(event_bus: EventBus):
    """Verify unsubscribing a handler removes it from event dispatch."""
    call_count = 0

    async def handler(event: SystemStatusEvent) -> None:
        nonlocal call_count
        call_count += 1

    event_bus.subscribe(SystemStatusEvent, handler)
    await event_bus.publish(SystemStatusEvent(component="C", status="1", message="m"))
    assert call_count == 1

    event_bus.unsubscribe(SystemStatusEvent, handler)
    await event_bus.publish(SystemStatusEvent(component="C", status="2", message="m"))
    assert call_count == 1
