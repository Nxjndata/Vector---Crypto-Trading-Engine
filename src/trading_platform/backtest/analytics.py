"""Performance metrics and quantitative risk-return analytics."""

import math
from datetime import datetime

from trading_platform.backtest.models import BacktestMetrics, TradeRecord
from trading_platform.models.candle import Candle


class PerformanceCalculator:
    """Calculates quantitative performance, risk-adjusted ratios, and drawdown analytics."""

    @classmethod
    def compute_metrics(
        cls,
        initial_capital: float,
        equity_curve: list[tuple[datetime, float]],
        trades: list[TradeRecord],
        candles: list[Candle] | None = None,
        risk_free_rate_annual: float = 0.0,
        total_fees_paid: float | None = None,
    ) -> BacktestMetrics:
        """Calculate comprehensive backtest performance analytics."""
        if not equity_curve:
            final_equity = initial_capital
            equity_curve = [(datetime.now(), initial_capital)]
        else:
            final_equity = equity_curve[-1][1]

        # 1. Total Return & CAGR
        total_return_pct = (
            ((final_equity - initial_capital) / initial_capital * 100.0)
            if initial_capital > 0
            else 0.0
        )

        total_seconds = (
            (equity_curve[-1][0] - equity_curve[0][0]).total_seconds()
            if len(equity_curve) > 1
            else 0.0
        )
        total_days = max(total_seconds / 86400.0, 1.0 / 1440.0)

        if final_equity > 0 and total_days >= 1.0:
            cagr_pct = ((final_equity / initial_capital) ** (365.25 / total_days) - 1.0) * 100.0
        else:
            cagr_pct = total_return_pct

        # 2. Buy-and-Hold Benchmark Comparison
        benchmark_return_pct = 0.0
        if candles and len(candles) >= 2:
            first_open = candles[0].open_price
            last_close = candles[-1].close_price
            if first_open > 0:
                benchmark_return_pct = ((last_close - first_open) / first_open) * 100.0

        alpha_vs_benchmark_pct = total_return_pct - benchmark_return_pct

        # 3. Period-over-period returns and Drawdown tracking
        returns: list[float] = []
        peak_equity = initial_capital
        max_drawdown_pct = 0.0
        curr_dd_duration = 0
        max_dd_duration = 0

        for i in range(len(equity_curve)):
            curr_equity = equity_curve[i][1]
            if curr_equity > peak_equity:
                peak_equity = curr_equity
                curr_dd_duration = 0
            else:
                curr_dd_duration += 1
                if curr_dd_duration > max_dd_duration:
                    max_dd_duration = curr_dd_duration

            dd_pct = ((peak_equity - curr_equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct

            if i > 0:
                prev_equity = equity_curve[i - 1][1]
                ret = (curr_equity - prev_equity) / prev_equity if prev_equity > 0 else 0.0
                returns.append(ret)

        # 4. Risk-Adjusted Ratios (Annualized Sharpe & Sortino)
        # Annualization factor for 1-minute sampling is sqrt(365.25 * 1440) = 725.26
        # If sampling frequency is fewer steps, scale by sqrt(periods_per_year)
        if len(returns) > 1:
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
            std_ret = math.sqrt(variance) if variance > 0 else 0.0

            # Downside deviation for Sortino
            downside_returns = [r for r in returns if r < 0]
            if downside_returns:
                downside_var = sum(r**2 for r in downside_returns) / len(downside_returns)
                downside_std = math.sqrt(downside_var) if downside_var > 0 else 0.0
            else:
                downside_std = 0.0

            periods_per_year = (len(returns) / total_days) * 365.25 if total_days > 0 else 525600.0
            ann_factor = math.sqrt(periods_per_year)

            rf_per_period = (risk_free_rate_annual / 100.0) / periods_per_year
            sharpe_ratio = (
                ((mean_ret - rf_per_period) / std_ret * ann_factor) if std_ret > 0 else 0.0
            )
            sortino_ratio = (
                ((mean_ret - rf_per_period) / downside_std * ann_factor)
                if downside_std > 0
                else (sharpe_ratio if sharpe_ratio > 0 else 0.0)
            )
        else:
            sharpe_ratio = 0.0
            sortino_ratio = 0.0

        # 5. Trade Statistics
        total_trades = len(trades)
        winning_trades_list = [t for t in trades if t.net_pnl > 0]
        losing_trades_list = [t for t in trades if t.net_pnl < 0]

        winning_trades = len(winning_trades_list)
        losing_trades = len(losing_trades_list)
        win_rate_pct = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_profit = sum(t.net_pnl for t in winning_trades_list)
        gross_loss = abs(sum(t.net_pnl for t in losing_trades_list))

        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = float("inf") if gross_profit > 0 else 0.0

        avg_trade_pnl = (sum(t.net_pnl for t in trades) / total_trades) if total_trades > 0 else 0.0
        avg_win_pnl = (gross_profit / winning_trades) if winning_trades > 0 else 0.0
        avg_loss_pnl = (-gross_loss / losing_trades) if losing_trades > 0 else 0.0
        final_fees_paid = (
            total_fees_paid if total_fees_paid is not None else sum(t.fee for t in trades)
        )

        return BacktestMetrics(
            initial_capital=initial_capital,
            final_equity=final_equity,
            total_return_pct=total_return_pct,
            cagr_pct=cagr_pct,
            benchmark_return_pct=benchmark_return_pct,
            alpha_vs_benchmark_pct=alpha_vs_benchmark_pct,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown_pct=max_drawdown_pct,
            max_drawdown_duration_candles=max_dd_duration,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate_pct=win_rate_pct,
            profit_factor=profit_factor,
            avg_trade_pnl=avg_trade_pnl,
            avg_win_pnl=avg_win_pnl,
            avg_loss_pnl=avg_loss_pnl,
            total_fees_paid=final_fees_paid,
            equity_curve=equity_curve,
            trades=trades,
        )
