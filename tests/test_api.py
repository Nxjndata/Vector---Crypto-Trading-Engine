"""Comprehensive tests for the FastAPI Monitoring & Control REST/WebSocket API."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.api.app import create_app
from trading_platform.api.container import PlatformContainer
from trading_platform.core.config import load_config
from trading_platform.core.constants import (
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskDecisionType,
    SignalType,
    TimeInForce,
)
from trading_platform.core.events import (
    CandleEvent,
    EventBus,
    FillEvent,
    SignalEvent,
)
from trading_platform.exchange.paper import PaperExchangeAdapter
from trading_platform.models.order import Order
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.risk.engine import RiskEngine


@pytest.fixture
def api_test_setup(async_test_engine):
    """Set up platform container and TestClient with full mocked/in-memory subsystem stack."""
    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    config = load_config(config_dir="config", env_override="paper")
    event_bus = EventBus()

    # Seed mock credentials to test credential leakage protection
    config.exchange.testnet_api_key = "SECRET_TESTNET_KEY_12345"
    config.exchange.testnet_api_secret = "SECRET_TESTNET_SECRET_67890"

    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        session_factory=session_factory,
    )
    adapter = PaperExchangeAdapter(initial_wallet_balance=10000.0)
    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=adapter,
        portfolio_manager=pm,
        session_factory=session_factory,
    )

    container = PlatformContainer(
        config=config,
        event_bus=event_bus,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        oms=oms,
        exchange_adapter=adapter,
        session_factory=session_factory,
    )

    app = create_app(container)
    client = TestClient(app)
    return container, client


def test_api_health_check(api_test_setup):
    """Verify health endpoint."""
    _, client = api_test_setup
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "HEALTHY"


def test_api_portfolio_endpoint(api_test_setup):
    """Verify portfolio REST endpoint returns correct initial account balances and PnL."""
    container, client = api_test_setup
    resp = client.get("/api/portfolio")
    assert resp.status_code == 200
    data = resp.json()

    assert data["strategy_id"] == container.strategy_id
    assert data["equity"] == 10000.0
    assert data["wallet_balance"] == 10000.0
    assert data["available_balance"] == 10000.0
    assert data["unrealized_pnl"] == 0.0
    assert data["drawdown_pct"] == 0.0
    assert data["effective_leverage"] == 0.0


def test_api_positions_endpoint(api_test_setup):
    """Verify positions REST endpoint reflects open positions."""
    container, client = api_test_setup
    pm = container.portfolio_manager

    # Simulate an open position
    pos = pm.get_position(container.strategy_id, "BTCUSDT")
    pos.size = 0.50
    pos.entry_price = 70000.0
    pos.mark_price = 71000.0
    pos.unrealized_pnl = 500.0
    pos.leverage = 5.0
    pos.margin_mode = MarginMode.ISOLATED

    resp = client.get("/api/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    p = data[0]
    assert p["symbol"] == "BTCUSDT"
    assert p["side"] == "LONG"
    assert p["quantity"] == 0.5
    assert p["entry_price"] == 70000.0
    assert p["mark_price"] == 71000.0
    assert p["unrealized_pnl"] == 500.0
    assert p["unrealized_pnl_pct"] == pytest.approx(7.14, abs=0.01)


@pytest.mark.asyncio
async def test_api_orders_endpoints(api_test_setup):
    """Verify open and history order endpoints."""
    container, client = api_test_setup

    # Seed an order directly into DB
    async with container.session_factory() as session:
        order = Order(
            client_order_id="test_order_api_001",
            exchange_order_id="binance_999888",
            strategy_id=container.strategy_id,
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            quantity=0.10,
            price=70000.0,
            status=OrderStatus.ACKNOWLEDGED,
            filled_qty=0.05,
            avg_fill_price=70000.0,
            cum_quote=3500.0,
            fee=1.40,
            fee_asset="USDT",
        )
        session.add(order)
        await session.commit()

    resp_open = client.get("/api/orders/open")
    assert resp_open.status_code == 200
    open_orders = resp_open.json()
    assert len(open_orders) == 1
    assert open_orders[0]["client_order_id"] == "test_order_api_001"
    assert open_orders[0]["status"] == "ACKNOWLEDGED"
    assert open_orders[0]["filled_qty"] == 0.05
    assert open_orders[0]["remaining_qty"] == 0.05

    resp_hist = client.get("/api/orders/history?limit=10")
    assert resp_hist.status_code == 200
    assert len(resp_hist.json()) == 1


def test_api_orders_endpoint_db_outage_returns_503(api_test_setup, mocker):
    """Verify that a database outage raises 503 Service Unavailable rather than silently faking an empty list."""
    container, client = api_test_setup

    # Mock get_db_session to simulate an unexpected DB connection drop
    mocker.patch(
        "trading_platform.api.routes.orders.get_db_session",
        side_effect=RuntimeError("Connection to PostgreSQL lost"),
    )

    resp_open = client.get("/api/orders/open")
    assert resp_open.status_code == 503
    assert "Database service unavailable" in resp_open.json()["detail"]

    resp_hist = client.get("/api/orders/history")
    assert resp_hist.status_code == 503
    assert "Database service unavailable" in resp_hist.json()["detail"]


def test_api_system_reports_db_offline_on_connection_failure(api_test_setup, mocker):
    """Verify that /api/system dynamically checks DB and reports OFFLINE when DB is unreachable."""
    container, client = api_test_setup

    mocker.patch(
        "trading_platform.api.routes.system.get_db_session",
        side_effect=RuntimeError("Network timeout connecting to PostgreSQL"),
    )

    resp = client.get("/api/system")
    assert resp.status_code == 200
    data = resp.json()
    db_comp = next(c for c in data["components"] if c["name"] == "DatabaseStorage")
    assert db_comp["status"] == "OFFLINE"
    assert "Database unreachable" in db_comp["details"]


def test_api_strategy_endpoint(api_test_setup):
    """Verify strategy parameters and metrics endpoint."""
    container, client = api_test_setup
    resp = client.get("/api/strategy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["strategy_id"] == container.strategy_id
    assert "parameters" in data
    assert data["parameters"]["fast_window"] == 10
    assert data["is_active"] is True


def test_api_risk_endpoint(api_test_setup):
    """Verify risk status and limit utilization metrics."""
    container, client = api_test_setup
    resp = client.get("/api/risk")
    assert resp.status_code == 200
    data = resp.json()
    assert data["trading_enabled"] is True
    assert data["kill_switch_active"] is False
    assert "max_position_size_usd" in data
    assert "max_portfolio_exposure_usd" in data


def test_api_system_endpoint(api_test_setup):
    """Verify system telemetry and component health cards."""
    _, client = api_test_setup
    resp = client.get("/api/system")
    assert resp.status_code == 200
    data = resp.json()
    assert data["environment"] == "PAPER"
    assert len(data["components"]) >= 5


def test_api_markets_endpoint(api_test_setup):
    """Verify active markets and pricing endpoint."""
    _, client = api_test_setup
    resp = client.get("/api/markets")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    symbols = [m["symbol"] for m in data]
    assert "BTCUSDT" in symbols


def test_credential_leakage_security(api_test_setup):
    """CRITICAL SECURITY TEST: Ensure exchange API credentials are NEVER returned by any endpoint."""
    _, client = api_test_setup
    endpoints = [
        "/api/portfolio",
        "/api/positions",
        "/api/orders/open",
        "/api/orders/history",
        "/api/strategy",
        "/api/risk",
        "/api/system",
        "/api/markets",
        "/health",
    ]

    for endpoint in endpoints:
        resp = client.get(endpoint)
        assert resp.status_code == 200
        text = resp.text
        assert "SECRET_TESTNET_KEY_12345" not in text
        assert "SECRET_TESTNET_SECRET_67890" not in text
        assert "testnet_api_key" not in text
        assert "testnet_api_secret" not in text
        assert "live_api_key" not in text
        assert "live_api_secret" not in text


@pytest.mark.asyncio
async def test_kill_switch_control_actions_end_to_end(api_test_setup):
    """Verify manual kill switch trigger and reset control actions end-to-end with RiskEngine."""
    container, client = api_test_setup
    risk_engine = container.risk_engine

    assert risk_engine.is_kill_switch_active is False

    # 1. Trigger kill switch without confirmation -> 400 Bad Request
    bad_resp = client.post(
        "/api/risk/kill-switch/trigger",
        json={"reason": "Test Trigger", "confirm": False},
    )
    assert bad_resp.status_code == 400

    # 2. Trigger kill switch with explicit confirm: true
    resp = client.post(
        "/api/risk/kill-switch/trigger",
        json={"reason": "Manual operator intervention during high volatility", "confirm": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["kill_switch_active"] is True
    assert risk_engine.is_kill_switch_active is True
    assert "Manual operator intervention" in risk_engine._kill_switch_reason

    # 2b. Verify GET /api/risk immediately reflects the triggered state for UI hydration
    risk_get = client.get("/api/risk")
    assert risk_get.status_code == 200
    risk_data = risk_get.json()
    assert risk_data["kill_switch_active"] is True
    assert "Manual operator intervention" in risk_data["kill_switch_reason"]

    # 3. Verify subsequent Strategy Signal is REJECTED by RiskEngine
    signal = SignalEvent(
        strategy_id=container.strategy_id,
        symbol="BTCUSDT",
        signal_type=SignalType.BUY,
        target_exposure=0.10,
        mark_price=70000.0,
    )

    decision = await risk_engine.evaluate_signal(signal)
    assert decision.decision_type == RiskDecisionType.REJECTED
    assert "Kill-Switch is active" in decision.reason or "KILL_SWITCH_ACTIVE" in decision.reason

    # 4. Reset kill switch
    reset_resp = client.post(
        "/api/risk/kill-switch/reset",
        json={"reason": "Operator cleared all risk parameters", "confirm": True},
    )
    assert reset_resp.status_code == 200
    reset_data = reset_resp.json()
    assert reset_data["success"] is True
    assert reset_data["kill_switch_active"] is False
    assert risk_engine.is_kill_switch_active is False

    # 4b. Verify GET /api/risk immediately reflects the cleared state for UI hydration
    risk_get_after = client.get("/api/risk")
    assert risk_get_after.status_code == 200
    risk_data_after = risk_get_after.json()
    assert risk_data_after["kill_switch_active"] is False
    assert risk_data_after["kill_switch_reason"] is None

    # 5. Verify subsequent Signal is now APPROVED
    decision_after = await risk_engine.evaluate_signal(signal)
    assert decision_after.decision_type == RiskDecisionType.APPROVED


def test_websocket_event_bus_streaming(api_test_setup):
    """Verify that WebSocket clients receive multicast JSON frames from EventBus events."""
    container, client = api_test_setup
    event_bus = container.event_bus

    with client.websocket_connect("/ws") as ws:
        # Read handshake connection ack
        ack = ws.receive_json()
        assert ack["event_type"] == "connection_ack"
        assert ack["data"]["status"] == "CONNECTED"

        # Publish a CandleEvent on EventBus synchronously (or via event loop)
        candle = CandleEvent(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC),
            close_time=datetime(2026, 8, 31, 12, 1, 0, tzinfo=UTC),
            open_price=78000.0,
            high_price=78150.0,
            low_price=77950.0,
            close_price=78100.0,
            volume=12.5,
            quote_volume=976250.0,
            trades_count=142,
            is_closed=True,
        )

        import asyncio

        loop = asyncio.get_event_loop()
        loop.run_until_complete(event_bus.publish(candle))

        # Receive pushed candle JSON frame
        msg = ws.receive_json()
        assert msg["event_type"] == "candle"
        assert msg["data"]["symbol"] == "BTCUSDT"
        assert msg["data"]["close"] == 78100.0

        # Publish a FillEvent
        fill = FillEvent(
            client_order_id="test_client_oid_123",
            exchange_trade_id="trade_456789",
            strategy_id=container.strategy_id,
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=78100.0,
            quantity=0.05,
            fee=1.562,
            fee_asset="USDT",
        )
        loop.run_until_complete(event_bus.publish(fill))

        fill_msg = ws.receive_json()
        assert fill_msg["event_type"] == "fill"
        assert fill_msg["data"]["price"] == 78100.0
        assert fill_msg["data"]["quantity"] == 0.05
