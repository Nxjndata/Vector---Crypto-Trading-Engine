"""Backtesting execution simulation engine."""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_platform.backtest.analytics import PerformanceCalculator
from trading_platform.backtest.models import BacktestConfig, BacktestMetrics, TradeRecord
from trading_platform.core.constants import OrderSide, RiskDecisionType
from trading_platform.core.events import (
    CandleEvent,
    EventBus,
    FillEvent,
)
from trading_platform.core.logging import get_logger
from trading_platform.exchange.instrument_manager import InstrumentManager
from trading_platform.models.candle import Candle
from trading_platform.portfolio.manager import PortfolioManager
from trading_platform.risk.engine import RiskEngine
from trading_platform.risk.rules import (
    InstrumentNotionalPrecisionRule,
    LiquidationDistanceRule,
    MaxDrawdownRule,
    MaxLeverageRule,
    MaxPositionSizeRule,
    MinAvailableBalanceRule,
    RateOfTradeRule,
    RiskRule,
    TradingEnabledRule,
)
from trading_platform.strategy.base import Strategy

logger = get_logger("backtest.engine")


def get_default_backtest_risk_rules() -> list[RiskRule]:
    """Return standard deterministic risk rules applicable to historical backtesting.

    NOTE: MarketDataHealthRule is intentionally the ONLY rule excluded from backtesting because
    it verifies real-time WebSocket connection heartbeat staleness (e.g. wall-clock lag > 10s),
    which does not apply to historical data replay. All other 8 risk rules run identically to live/paper.
    """
    return [
        TradingEnabledRule(),
        MaxDrawdownRule(max_drawdown_pct=15.0),
        MaxPositionSizeRule(max_position_pct=0.20),
        MaxLeverageRule(max_aggregate_leverage=3.0),
        MinAvailableBalanceRule(),
        LiquidationDistanceRule(min_liquidation_distance_pct=15.0),
        InstrumentNotionalPrecisionRule(),
        RateOfTradeRule(max_trades_per_window=10, window_seconds=60),
    ]


class BacktestEngine:
    """Simulates the entire platform trading pipeline against historical candlestick data.

    Pipeline:
    Historical Data -> Strategy -> Signal -> Risk Engine -> Execution Simulation -> Portfolio -> Performance
    """

    def __init__(
        self,
        config: BacktestConfig | None = None,
        instrument_manager: InstrumentManager | None = None,
        risk_rules: list[RiskRule] | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self.instrument_manager = instrument_manager
        self.risk_rules = risk_rules or get_default_backtest_risk_rules()
        self.session_factory = session_factory

    async def run(
        self,
        strategy: Strategy,
        candles: list[Candle],
    ) -> BacktestMetrics:
        """Run backtest for an unmodified Strategy instance over historical candle series.

        Args:
            strategy: Concrete Strategy instance (e.g. SMAMomentumStrategy).
            candles: List of historical Candle models sorted chronologically.

        Returns:
            BacktestMetrics containing complete quantitative performance analytics.
        """
        if not candles:
            logger.warning("Backtest called with zero candles.")
            return PerformanceCalculator.compute_metrics(
                initial_capital=self.config.initial_capital,
                equity_curve=[],
                trades=[],
                candles=[],
            )

        strategy_id = strategy.strategy_id
        symbol = self.config.symbol.upper()

        logger.info(
            f"Starting backtest for strategy '{strategy_id}' on {symbol} "
            f"({len(candles)} candles from {candles[0].open_time.isoformat()} to {candles[-1].close_time.isoformat()})..."
        )

        # 1. Initialize EventBus and Portfolio Manager
        event_bus = EventBus()
        portfolio_manager = PortfolioManager(
            event_bus=event_bus,
            session_factory=self.session_factory,
            default_deposit_usd=self.config.initial_capital,
        )

        # 2. Initialize Risk Engine with real deterministic rules
        risk_engine = RiskEngine(
            event_bus=event_bus,
            portfolio_manager=portfolio_manager,
            instrument_manager=self.instrument_manager,
            session_factory=self.session_factory,
            rules=self.risk_rules,
        )

        # State tracking for trades & equity curve
        equity_curve: list[tuple[datetime, float]] = []
        completed_trades: list[TradeRecord] = []
        open_trade_entry: dict[str, Any] | None = None
        trade_counter = 0
        cumulative_fees_paid = 0.0

        # Initial equity point
        equity_curve.append((candles[0].open_time, self.config.initial_capital))

        # 3. Step-by-step historical candle simulation loop
        for candle in candles:
            close_price = candle.close_price
            close_time = candle.close_time

            # Update mark price in PortfolioManager and RiskEngine
            portfolio_manager._latest_mark_prices[symbol] = close_price
            risk_engine.record_market_data_tick(symbol, close_time)

            # Build platform-standard CandleEvent
            candle_event = CandleEvent(
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                open_time=candle.open_time,
                close_time=candle.close_time,
                open_price=candle.open_price,
                high_price=candle.high_price,
                low_price=candle.low_price,
                close_price=candle.close_price,
                volume=candle.volume,
                quote_volume=candle.quote_volume,
                trades_count=candle.trades_count,
                is_closed=candle.is_closed,
            )

            # A. Strategy generates pure intent signals
            signals = strategy.on_candle(candle_event)

            # B. Process any emitted signals through Risk Engine & Execution Simulator
            for sig in signals:
                decision = await risk_engine.evaluate_signal(sig)

                if decision.decision_type in (RiskDecisionType.APPROVED, RiskDecisionType.RESIZED):
                    approved_exp = decision.approved_target_exposure
                    port = portfolio_manager.get_portfolio(strategy_id)
                    pos = portfolio_manager.get_position(strategy_id, symbol)

                    # Compute target position size in units
                    target_notional = abs(approved_exp) * port.equity
                    direction = 1.0 if approved_exp > 0 else (-1.0 if approved_exp < 0 else 0.0)
                    target_size = (
                        (target_notional / close_price) * direction if close_price > 0 else 0.0
                    )

                    current_size = pos.size
                    delta_qty = target_size - current_size

                    # Apply instrument precision rounding if manager is present
                    if self.instrument_manager:
                        rounded_delta = float(
                            self.instrument_manager.round_quantity(symbol, abs(delta_qty))
                        )
                        if rounded_delta <= 0:
                            continue
                        delta_qty = rounded_delta if delta_qty > 0 else -rounded_delta

                    if abs(delta_qty) > 1e-8:
                        side = OrderSide.BUY if delta_qty > 0 else OrderSide.SELL
                        exec_qty = abs(delta_qty)

                        # C. Execution Simulation: Slippage & Fees
                        slippage_mult = 1.0 + (
                            (self.config.slippage_bps / 10000.0)
                            if side == OrderSide.BUY
                            else -(self.config.slippage_bps / 10000.0)
                        )
                        fill_price = close_price * slippage_mult
                        fee = exec_qty * fill_price * self.config.taker_fee_rate
                        cumulative_fees_paid += fee

                        # D. Trade Attribution Tracking
                        if current_size != 0.0 and (
                            (current_size > 0 and side == OrderSide.SELL)
                            or (current_size < 0 and side == OrderSide.BUY)
                        ):
                            # Closing or reducing position
                            closed_qty = min(abs(current_size), exec_qty)
                            if open_trade_entry:
                                trade_counter += 1
                                entry_p = open_trade_entry["entry_price"]
                                entry_t = open_trade_entry["entry_time"]
                                entry_fee_portion = (
                                    closed_qty * entry_p * self.config.taker_fee_rate
                                )
                                exit_fee = closed_qty * fill_price * self.config.taker_fee_rate
                                trade_fee = entry_fee_portion + exit_fee

                                if current_size > 0:
                                    gross_pnl = closed_qty * (fill_price - entry_p)
                                else:
                                    gross_pnl = closed_qty * (entry_p - fill_price)

                                net_pnl = gross_pnl - trade_fee
                                ret_pct = (
                                    (net_pnl / (closed_qty * entry_p) * 100.0)
                                    if (closed_qty * entry_p) > 0
                                    else 0.0
                                )

                                completed_trades.append(
                                    TradeRecord(
                                        trade_id=f"T_{trade_counter}",
                                        strategy_id=strategy_id,
                                        symbol=symbol,
                                        side=open_trade_entry["side"],
                                        entry_time=entry_t,
                                        exit_time=close_time,
                                        entry_price=entry_p,
                                        exit_price=fill_price,
                                        quantity=closed_qty,
                                        notional=closed_qty * entry_p,
                                        gross_pnl=gross_pnl,
                                        fee=trade_fee,
                                        net_pnl=net_pnl,
                                        return_pct=ret_pct,
                                    )
                                )

                                remaining = abs(current_size) - closed_qty
                                if remaining <= 1e-8:
                                    open_trade_entry = None
                                else:
                                    open_trade_entry["quantity"] = remaining

                        # If position opened or flipped
                        if not pos.is_open or (open_trade_entry is None and target_size != 0.0):
                            open_trade_entry = {
                                "side": side,
                                "entry_price": fill_price,
                                "entry_time": close_time,
                                "quantity": abs(target_size),
                            }

                        # E. Apply fill to PortfolioManager
                        fill_event = FillEvent(
                            strategy_id=strategy_id,
                            symbol=symbol,
                            client_order_id=f"bt_{close_time.timestamp()}",
                            exchange_trade_id=f"bt_trade_{close_time.timestamp()}",
                            side=side,
                            quantity=exec_qty,
                            price=fill_price,
                            fee=fee,
                            fee_asset="USDT",
                            timestamp=close_time,
                        )
                        await portfolio_manager.apply_fill(fill_event)

            # Re-mark position unrealized PnL at candle close
            pos_after = portfolio_manager.get_position(strategy_id, symbol)
            if pos_after.is_open:
                pos_after.recalculate_unrealized_pnl(close_price)

            curr_port = portfolio_manager.get_portfolio(strategy_id)
            equity_curve.append((close_time, curr_port.equity))

        # 4. Compute full performance metrics & analytics
        metrics = PerformanceCalculator.compute_metrics(
            initial_capital=self.config.initial_capital,
            equity_curve=equity_curve,
            trades=completed_trades,
            candles=candles,
            total_fees_paid=cumulative_fees_paid,
        )

        logger.info(
            f"Backtest complete for '{strategy_id}': "
            f"Return: {metrics.total_return_pct:+.2f}%, Final Equity: ${metrics.final_equity:,.2f}, "
            f"Trades: {metrics.total_trades} (Win Rate: {metrics.win_rate_pct:.1f}%), "
            f"Sharpe: {metrics.sharpe_ratio:.2f}, Max Drawdown: {metrics.max_drawdown_pct:.2f}%."
        )

        return metrics
