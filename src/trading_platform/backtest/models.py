"""Domain models for backtesting configurations, trade logs, performance metrics, and walk-forward windows."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from trading_platform.core.constants import OrderSide


class BacktestConfig(BaseModel):
    """Configuration parameters for historical backtesting."""

    symbol: str = "BTCUSDT"
    timeframe: str = "1m"
    start_time: datetime | None = None
    end_time: datetime | None = None
    initial_capital: float = Field(default=10000.0, gt=0.0)
    slippage_bps: float = Field(default=1.0, ge=0.0)  # 1.0 bps = 0.01%
    maker_fee_rate: float = Field(default=0.0002, ge=0.0)  # 0.02%
    taker_fee_rate: float = Field(default=0.0005, ge=0.0)  # 0.05%
    leverage: float = Field(default=1.0, ge=1.0, le=125.0)


@dataclass
class TradeRecord:
    """Individual trade execution record for performance attribution."""

    trade_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    notional: float
    gross_pnl: float
    fee: float
    net_pnl: float
    return_pct: float
    holding_period_candles: int = 1


@dataclass
class BacktestMetrics:
    """Consolidated quantitative performance metrics for a backtest run."""

    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float

    # Benchmark Comparison
    benchmark_return_pct: float
    alpha_vs_benchmark_pct: float

    # Risk-Adjusted Returns
    sharpe_ratio: float
    sortino_ratio: float

    # Drawdowns
    max_drawdown_pct: float
    max_drawdown_duration_candles: int

    # Trade Statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_trade_pnl: float
    avg_win_pnl: float
    avg_loss_pnl: float
    total_fees_paid: float

    # Equity Curve: list of (timestamp, equity)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Return human-readable summary dictionary."""
        return {
            "Initial Capital": f"${self.initial_capital:,.2f}",
            "Final Equity": f"${self.final_equity:,.2f}",
            "Total Return": f"{self.total_return_pct:+.2f}%",
            "CAGR": f"{self.cagr_pct:+.2f}%",
            "Benchmark (Buy & Hold)": f"{self.benchmark_return_pct:+.2f}%",
            "Alpha vs Benchmark": f"{self.alpha_vs_benchmark_pct:+.2f}%",
            "Sharpe Ratio": f"{self.sharpe_ratio:.2f}",
            "Sortino Ratio": f"{self.sortino_ratio:.2f}",
            "Max Drawdown": f"{self.max_drawdown_pct:.2f}%",
            "Total Trades": self.total_trades,
            "Win Rate": f"{self.win_rate_pct:.1f}%",
            "Profit Factor": f"{self.profit_factor:.2f}",
            "Total Fees Paid": f"${self.total_fees_paid:,.2f}",
        }


@dataclass
class WalkForwardWindow:
    """Represents a single walk-forward train (in-sample) and test (out-of-sample) time window."""

    window_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    in_sample_metrics: BacktestMetrics | None = None
    out_of_sample_metrics: BacktestMetrics | None = None
