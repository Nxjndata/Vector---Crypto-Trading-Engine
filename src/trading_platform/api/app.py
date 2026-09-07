"""FastAPI application factory for the Trading Platform Monitoring & Control API."""

import asyncio
import traceback
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from trading_platform.api.container import PlatformContainer, set_container
from trading_platform.api.routes.markets import router as markets_router
from trading_platform.api.routes.metrics import router as metrics_router
from trading_platform.api.routes.orders import router as orders_router
from trading_platform.api.routes.portfolio import router as portfolio_router
from trading_platform.api.routes.positions import router as positions_router
from trading_platform.api.routes.risk import router as risk_router
from trading_platform.api.routes.strategy import router as strategy_router
from trading_platform.api.routes.system import router as system_router
from trading_platform.api.ws import WebSocketConnectionManager
from trading_platform.core.constants import TradingMode
from trading_platform.core.events import MarketDataEvent
from trading_platform.core.logging import get_logger
from trading_platform.db.session import get_db_session
from trading_platform.exchange.binance_futures import BinanceFuturesAdapter
from trading_platform.exchange.user_stream import BinanceUserDataStreamClient
from trading_platform.market_data.ws_client import BinanceWebSocketClient
from trading_platform.monitoring.alerting import AlertDispatcher
from trading_platform.monitoring.metrics import MetricsCollector

logger = get_logger("api.app")


def create_app(container: PlatformContainer | None = None) -> FastAPI:
    """Create and configure the FastAPI monitoring application."""
    if container:
        set_container(container)

    # Initialize Metrics Collector and Alerting Dispatcher
    metrics_collector: MetricsCollector | None = None
    alert_dispatcher: AlertDispatcher | None = None
    if container:
        metrics_collector = MetricsCollector(event_bus=container.event_bus)
        alert_dispatcher = AlertDispatcher(
            config=container.config.alerting,
            event_bus=container.event_bus,
        )

        # Update PortfolioManager mark prices and container market_tickers on live ticks
        async def on_market_data(event: MarketDataEvent) -> None:
            container.portfolio_manager._latest_mark_prices[event.symbol] = event.mark_price
            ticker = container.market_tickers.get(event.symbol, {})
            ticker["symbol"] = event.symbol
            ticker["mark_price"] = event.mark_price
            if event.last_price is not None:
                ticker["last_price"] = event.last_price
            if event.index_price is not None:
                ticker["index_price"] = event.index_price
            if event.funding_rate is not None:
                ticker["funding_rate"] = event.funding_rate
            if event.price_change_percent_24h is not None:
                ticker["price_change_percent_24h"] = event.price_change_percent_24h
            if event.high_price_24h is not None:
                ticker["high_price_24h"] = event.high_price_24h
            if event.low_price_24h is not None:
                ticker["low_price_24h"] = event.low_price_24h
            if event.volume_24h is not None:
                ticker["volume_24h"] = event.volume_24h
            if event.quote_volume_24h is not None:
                ticker["quote_volume_24h"] = event.quote_volume_24h
            ticker["last_updated"] = datetime.now(UTC)
            container.market_tickers[event.symbol] = ticker

        container.event_bus.subscribe(MarketDataEvent, on_market_data)

    # Initialize WebSocket manager
    ws_manager: WebSocketConnectionManager | None = None
    if container:
        ws_manager = WebSocketConnectionManager(event_bus=container.event_bus)
        ws_manager.subscribe_to_event_bus()

    # Background periodic portfolio telemetry task
    async def periodic_portfolio_push() -> None:
        while True:
            try:
                await asyncio.sleep(2.0)
                if ws_manager and ws_manager.active_connections and container:
                    portfolio = container.portfolio_manager.get_portfolio(container.strategy_id)
                    lev = (
                        (portfolio.total_exposure / portfolio.equity)
                        if portfolio.equity > 0
                        else 0.0
                    )
                    await ws_manager.broadcast(
                        {
                            "event_type": "portfolio_update",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "data": {
                                "equity": round(portfolio.equity, 2),
                                "wallet_balance": round(portfolio.wallet_balance, 2),
                                "available_balance": round(portfolio.available_balance, 2),
                                "unrealized_pnl": round(portfolio.unrealized_pnl, 2),
                                "realized_pnl": round(portfolio.realized_pnl, 2),
                                "drawdown_pct": round(portfolio.drawdown_pct, 2),
                                "effective_leverage": round(lev, 2),
                                "total_exposure": round(portfolio.total_exposure, 2),
                            },
                        }
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic portfolio push: {e}")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logger.info("Starting up Trading Platform API Service...")

        # Strict fail-fast Database connectivity check on API server startup
        if container and container.session_factory:
            logger.info("Executing API Server fail-fast database connectivity check...")
            try:
                async with get_db_session(container.session_factory) as session:
                    await session.execute(text("SELECT 1"))
                logger.info("API Server database connection verified successfully.")
            except Exception as e:
                logger.critical(
                    f"FATAL: API Server startup aborted. Database connectivity check failed: {e}",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"API Server startup aborted: Database connectivity check failed: {e}"
                ) from e

        # 2. Restore Portfolio and OMS state from PostgreSQL
        if container and container.portfolio_manager:
            try:
                await container.portfolio_manager.restore_from_db()
                logger.info("API Server hydrated PortfolioManager state from database.")
            except Exception as e:
                logger.warning(f"PortfolioManager database restoration notice: {e}")

        if container and container.oms:
            try:
                await container.oms.restore_from_db()
                logger.info("API Server hydrated OMS resting orders and in-flight guard from database.")
            except Exception as e:
                logger.warning(f"OMS database restoration notice: {e}")

        # 3. Initialize InstrumentManager trading universe specifications & margin/leverage
        if container and container.instrument_manager:
            try:
                await container.instrument_manager.initialize()
                logger.info("API Server initialized InstrumentManager trading universe.")
            except Exception as e:
                logger.warning(f"Failed to fetch market specs on startup: {e}")

        # 4. Start live market data WebSocket and User Data Stream if in TESTNET or LIVE mode
        market_data_client: BinanceWebSocketClient | None = None
        user_data_client: BinanceUserDataStreamClient | None = None

        if container and container.config.environment in (TradingMode.TESTNET, TradingMode.LIVE):
            try:
                market_data_client = BinanceWebSocketClient(
                    config=container.config,
                    event_bus=container.event_bus,
                )
                await market_data_client.start()
                logger.info("API Server connected live Binance market data WebSocket stream.")
            except Exception as e:
                logger.error(f"Failed to connect API market data client: {e}")

            if isinstance(container.exchange_adapter, BinanceFuturesAdapter):
                try:
                    user_data_client = BinanceUserDataStreamClient(
                        config=container.config,
                        event_bus=container.event_bus,
                        adapter=container.exchange_adapter,
                    )
                    await user_data_client.start()
                    logger.info("API Server connected live Binance User Data WebSocket stream.")
                except Exception as e:
                    logger.error(f"Failed to connect Binance User Data stream: {e}")

        # 5. Start autonomous StrategyRunner on the shared EventBus
        if container and container.strategy_runner:
            try:
                await container.strategy_runner.start()
                logger.info("API Server started autonomous StrategyRunner on unified EventBus.")
            except Exception as e:
                logger.error(f"Failed to start StrategyRunner: {e}")

        # 6. Background periodic tasks: Portfolio Push & Periodic Reconciliation Audit
        async def periodic_reconciliation_audit() -> None:
            while True:
                try:
                    await asyncio.sleep(15.0)
                    if container and container.reconciliation_engine:
                        results = await container.reconciliation_engine.reconcile_now()
                        container.last_reconciliation_result = {
                            "status": "HEALTHY" if not results else "DEGRADED",
                            "last_time": datetime.now(UTC),
                            "mismatch_count": len(results),
                        }
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Periodic reconciliation audit notice: {e}")

        push_task = asyncio.create_task(periodic_portfolio_push(), name="portfolio_ws_push")
        recon_task = asyncio.create_task(periodic_reconciliation_audit(), name="recon_audit")
        yield
        logger.info("Shutting down Trading Platform API Service...")
        push_task.cancel()
        recon_task.cancel()
        try:
            await asyncio.gather(push_task, recon_task, return_exceptions=True)
        except Exception:
            pass

        if container and container.strategy_runner:
            try:
                await container.strategy_runner.stop()
            except Exception:
                pass

        if user_data_client:
            try:
                await user_data_client.stop()
            except Exception:
                pass

        if market_data_client:
            try:
                await market_data_client.stop()
            except Exception:
                pass

    app = FastAPI(
        title="Binance USDT-M Futures Trading Platform API",
        description="Institutional Monitoring & Risk Control REST/WebSocket API",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Configure explicit CORS for Next.js frontend
    allowed_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["*"],
    )

    # Global unhandled exception logging handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        tb = traceback.format_exc()
        logger.error(f"Unhandled Exception on {request.method} {request.url.path}:\n{tb}")
        if alert_dispatcher:
            alert_dispatcher.alert_unhandled_exception(
                path=request.url.path,
                error=str(exc),
                traceback_str=tb,
            )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": str(exc) or "Internal Server Error",
                "type": exc.__class__.__name__,
                "path": request.url.path,
            },
        )

    # Attach WebSocket Manager to app state
    app.state.ws_manager = ws_manager
    app.state.alert_dispatcher = alert_dispatcher
    app.state.metrics_collector = metrics_collector

    # Include REST routers under /api
    app.include_router(portfolio_router, prefix="/api")
    app.include_router(positions_router, prefix="/api")
    app.include_router(orders_router, prefix="/api")
    app.include_router(strategy_router, prefix="/api")
    app.include_router(risk_router, prefix="/api")
    app.include_router(system_router, prefix="/api")
    app.include_router(markets_router, prefix="/api")
    app.include_router(metrics_router)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        """Simple liveness probe."""
        return {"status": "HEALTHY", "service": "trading-platform-api"}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """Real-time multicast WebSocket stream."""
        if not ws_manager:
            await websocket.close(code=1011, reason="WebSocket manager uninitialized")
            return

        await ws_manager.connect(websocket)
        try:
            while True:
                # Receive client heartbeats or ping frames
                data = await websocket.receive_text()
                # Handle client ping
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            await ws_manager.disconnect(websocket)
        except Exception:
            await ws_manager.disconnect(websocket)

    return app
