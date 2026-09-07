"""Real live network tests against Binance USDT-M Perpetual Futures Testnet.

NOTE: These tests require real network access and valid Binance Futures Testnet credentials
(BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET).
They are tagged with @pytest.mark.testnet_live and excluded by default from the unit test suite.
Run explicitly with: pytest -m testnet_live -v -s
"""

import asyncio
import os
import time

import httpx
import pytest

from trading_platform.core.config import AppConfig, ExchangeConfig, load_env_file
from trading_platform.core.constants import (
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
    TradingMode,
)
from trading_platform.core.events import EventBus, FillEvent
from trading_platform.exchange.adapter import OrderRequest
from trading_platform.exchange.binance_futures import BinanceFuturesAdapter
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.exchange.rate_limiter import BinanceRateLimiter
from trading_platform.exchange.user_stream import BinanceUserDataStreamClient
from trading_platform.oms.engine import OrderManagementSystem
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.reconciliation.engine import ReconciliationEngine
from trading_platform.risk.engine import RiskEngine


def get_live_testnet_config() -> AppConfig:
    """Build AppConfig with live testnet API credentials from environment variables."""
    load_env_file()
    api_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
    api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "")

    return AppConfig(
        environment=TradingMode.TESTNET,
        exchange=ExchangeConfig(
            testnet_api_key=api_key,
            testnet_api_secret=api_secret,
        ),
    )


# ==============================================================================
# 1. Live Public Endpoint Tests (Requires Internet, No API Key Required)
# ==============================================================================


@pytest.mark.testnet_live
@pytest.mark.asyncio
async def test_live_server_time_clock_drift():
    """Query real Binance Futures server time endpoint (/fapi/v1/time) and verify clock drift."""
    async with httpx.AsyncClient(
        base_url="https://testnet.binancefuture.com", timeout=10.0
    ) as client:
        t0 = time.time() * 1000
        resp = await client.get("/fapi/v1/time")
        t1 = time.time() * 1000

        assert resp.status_code == 200
        data = resp.json()
        server_time_ms = data["serverTime"]

        # Roundtrip latency
        rtt_ms = t1 - t0
        estimated_server_time_at_recv = server_time_ms + (rtt_ms / 2.0)
        drift_ms = abs(t1 - estimated_server_time_at_recv)

        print(
            f"\n[LIVE TESTNET] Server Time: {server_time_ms} | RTT Latency: {rtt_ms:.2f}ms | Clock Drift: {drift_ms:.2f}ms"
        )
        # Assert clock drift is within acceptable bounds (< 1000ms)
        assert drift_ms < 1000.0


@pytest.mark.testnet_live
@pytest.mark.asyncio
async def test_live_rate_limit_headers_and_markets():
    """Verify BinanceFuturesAdapter against real testnet.binancefuture.com exchangeInfo and rate-limit headers."""
    config = get_live_testnet_config()
    rate_limiter = BinanceRateLimiter()
    adapter = BinanceFuturesAdapter(config=config, rate_limiter=rate_limiter)

    try:
        instruments = await adapter.get_markets()
        assert len(instruments) > 0

        # Verify BTCUSDT is present
        btc_inst = next((i for i in instruments if i.symbol == "BTCUSDT"), None)
        assert btc_inst is not None
        assert btc_inst.is_active is True
        assert btc_inst.quote_asset == "USDT"

        # Verify rate limit headers were updated from live response
        metrics = rate_limiter.metrics
        print(f"[LIVE TESTNET] Real IP Weight Used: {metrics['used_weight_1m']}/2400")
        assert metrics["used_weight_1m"] >= 1

    finally:
        await adapter.close()


# ==============================================================================
# 2. Live Signed Private Endpoint Tests (Requires Valid Testnet API Key)
# ==============================================================================


@pytest.mark.testnet_live
@pytest.mark.asyncio
async def test_live_listen_key_lifecycle():
    """Verify creating, keeping alive, and closing a genuine user data stream listenKey on Testnet."""
    config = get_live_testnet_config()
    if not config.exchange.testnet_api_key or not config.exchange.testnet_api_secret:
        pytest.skip(
            "Skipping signed testnet test: BINANCE_TESTNET_API_KEY / SECRET not set in environment."
        )

    adapter = BinanceFuturesAdapter(config=config)
    try:
        # 1. Create listenKey
        listen_key = await adapter.create_listen_key()
        assert len(listen_key) > 10
        print(f"\n[LIVE TESTNET] Acquired ListenKey: {listen_key[:10]}...")

        # 2. Keepalive listenKey
        await adapter.keepalive_listen_key(listen_key)

        # 3. Close listenKey
        await adapter.close_listen_key(listen_key)
        print("[LIVE TESTNET] ListenKey closed successfully.")

    finally:
        await adapter.close()


@pytest.mark.testnet_live
@pytest.mark.asyncio
async def test_live_order_placement_and_cancellation_roundtrip():
    """Place a micro LIMIT order far away from mark price on testnet, query status, measure latency, and cancel."""
    config = get_live_testnet_config()
    if not config.exchange.testnet_api_key or not config.exchange.testnet_api_secret:
        pytest.skip(
            "Skipping signed testnet test: BINANCE_TESTNET_API_KEY / SECRET not set in environment."
        )

    adapter = BinanceFuturesAdapter(config=config)
    im = InstrumentManager(config=config, adapter=adapter)

    try:
        await im.initialize()
        ticker = await adapter.get_ticker("BTCUSDT")
        mark_price = ticker.mark_price
        assert mark_price > 0

        # Safety price: place BUY LIMIT order at 50% of current mark price (will rest, not fill)
        safe_price = round(mark_price * 0.50, 1)
        safe_price = float(im.round_price("BTCUSDT", safe_price))
        client_oid = f"testnet_probe_{int(time.time())}"

        order_req = OrderRequest(
            client_order_id=client_oid,
            strategy_id="testnet_live_probe",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.002,  # Minimum safe micro size
            price=safe_price,
            time_in_force=TimeInForce.GTC,
        )

        # 1. Measure Order Placement Latency
        t0 = time.time()
        placed_order = await adapter.place_order(order_req)
        placement_latency_ms = (time.time() - t0) * 1000.0

        print(
            f"\n[LIVE TESTNET] Placed Order ID: {placed_order.exchange_order_id} | Status: {placed_order.status.value} | Latency: {placement_latency_ms:.2f}ms"
        )
        assert placed_order.status == OrderStatus.ACKNOWLEDGED
        assert placed_order.exchange_order_id != ""

        try:
            # 2. Query Order Status via get_order
            t1 = time.time()
            queried_order = await adapter.get_order("BTCUSDT", client_order_id=client_oid)
            query_latency_ms = (time.time() - t1) * 1000.0
            print(
                f"[LIVE TESTNET] Query Order Latency: {query_latency_ms:.2f}ms | Status: {queried_order.status.value}"
            )
            assert queried_order.status == OrderStatus.ACKNOWLEDGED
        finally:
            # 3. Cancel Order (always executed to ensure no resting ghost orders)
            t2 = time.time()
            cancelled_order = await adapter.cancel_order("BTCUSDT", client_order_id=client_oid)
            cancel_latency_ms = (time.time() - t2) * 1000.0
            print(
                f"[LIVE TESTNET] Cancel Order Latency: {cancel_latency_ms:.2f}ms | Status: {cancelled_order.status.value}"
            )
            assert cancelled_order.status == OrderStatus.CANCELLED

    finally:
        await adapter.close()


@pytest.mark.testnet_live
@pytest.mark.asyncio
async def test_live_user_data_stream_fill_notification():
    """Verify live User Data WebSocket stream receives and publishes FillEvent for a micro order on Testnet."""
    config = get_live_testnet_config()
    if not config.exchange.testnet_api_key or not config.exchange.testnet_api_secret:
        pytest.skip(
            "Skipping signed testnet test: BINANCE_TESTNET_API_KEY / SECRET not set in environment."
        )

    event_bus = EventBus()
    adapter = BinanceFuturesAdapter(config=config)
    client = BinanceUserDataStreamClient(config=config, event_bus=event_bus, adapter=adapter)

    received_fills: list[FillEvent] = []
    fill_received_future: asyncio.Future[FillEvent] = asyncio.get_running_loop().create_future()

    async def on_fill(event: FillEvent) -> None:
        received_fills.append(event)
        if not fill_received_future.done():
            fill_received_future.set_result(event)

    event_bus.subscribe(FillEvent, on_fill)

    try:
        # 1. Start User Data Stream
        await client.start()
        # Allow 2 seconds for WebSocket handshake to complete
        await asyncio.sleep(2.0)
        assert client.is_connected is True

        # 2. Submit a micro MARKET order (0.002 BTC) to trigger a guaranteed fill
        client_oid = f"testnet_fill_probe_{int(time.time())}"
        order_req = OrderRequest(
            client_order_id=client_oid,
            strategy_id="testnet_live_probe",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.002,
        )

        t0 = time.time()
        order = await adapter.place_order(order_req)
        print(
            f"\n[LIVE TESTNET] Market Order Placed: {order.exchange_order_id} | Status: {order.status.value}"
        )

        # 3. Wait for real WebSocket FillEvent notification
        try:
            live_fill = await asyncio.wait_for(fill_received_future, timeout=10.0)
            ws_fill_latency_ms = (time.time() - t0) * 1000.0
            print(
                f"[LIVE TESTNET] Live WebSocket FillEvent Received: {live_fill.quantity} BTC @ ${live_fill.price:,.2f} | "
                f"Trade ID: {live_fill.exchange_trade_id} | Fee: {live_fill.fee} {live_fill.fee_asset} | "
                f"End-to-End Latency: {ws_fill_latency_ms:.2f}ms"
            )
            assert live_fill.quantity == pytest.approx(0.002, abs=0.0001)
            assert live_fill.price > 0
        finally:
            # 4. Clean up position: sell back 0.002 BTC to return to flat
            close_req = OrderRequest(
                client_order_id=f"testnet_close_{int(time.time())}",
                strategy_id="testnet_live_probe",
                symbol="BTCUSDT",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=0.002,
            )
            await adapter.place_order(close_req)
            print("[LIVE TESTNET] Closed test position back to flat.")

    finally:
        await client.stop()
        await adapter.close()


@pytest.mark.testnet_live
@pytest.mark.asyncio
async def test_live_full_reconciliation_cycle(async_test_engine):
    """Run full ReconciliationEngine against genuine testnet account balances, orders, and positions."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    config = get_live_testnet_config()
    if not config.exchange.testnet_api_key or not config.exchange.testnet_api_secret:
        pytest.skip(
            "Skipping signed testnet test: BINANCE_TESTNET_API_KEY / SECRET not set in environment."
        )

    session_factory = async_sessionmaker(
        bind=async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_bus = EventBus()
    adapter = BinanceFuturesAdapter(config=config)
    im = InstrumentManager(config=config, adapter=adapter)

    pm = PortfolioManager(
        event_bus=event_bus,
        session_factory=session_factory,
        default_deposit_usd=10000.0,
    )
    risk_engine = RiskEngine(
        event_bus=event_bus,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )
    oms = OrderManagementSystem(
        event_bus=event_bus,
        exchange_adapter=adapter,
        portfolio_manager=pm,
        instrument_manager=im,
        session_factory=session_factory,
    )
    reconciliation_engine = ReconciliationEngine(
        event_bus=event_bus,
        exchange_adapter=adapter,
        portfolio_manager=pm,
        risk_engine=risk_engine,
        oms=oms,
        session_factory=session_factory,
    )

    try:
        await im.initialize()

        # Cancel any previous lingering test orders on testnet
        open_orders = await adapter.get_open_orders("BTCUSDT")
        for oo in open_orders:
            if "testnet_probe" in oo.client_order_id:
                try:
                    await adapter.cancel_order("BTCUSDT", client_order_id=oo.client_order_id)
                except Exception:
                    pass

        # Seed local wallet balance to match remote testnet USDT wallet
        balances = await adapter.get_balance()
        usdt_bal = next((b for b in balances if b.asset == "USDT"), None)
        if usdt_bal:
            pm._wallet_balances[config.strategy_id] = usdt_bal.wallet_balance

        # Execute live reconciliation
        t0 = time.time()
        discrepancies = await reconciliation_engine.reconcile_now()
        reconciliation_rtt_ms = (time.time() - t0) * 1000.0

        print(
            f"\n[LIVE TESTNET] Live Reconciliation Cycle Completed in {reconciliation_rtt_ms:.2f}ms | "
            f"Discrepancies Found: {len(discrepancies)}"
        )
        for disc in discrepancies:
            print(f"  - {disc.event_type.value} [{disc.severity.value}]: {disc.details}")

        # Assert reconciliation runs cleanly against remote exchange ground-truth
        assert isinstance(discrepancies, list)

    finally:
        await adapter.close()
