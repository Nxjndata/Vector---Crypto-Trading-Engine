"""Tests for the Pydantic Settings and YAML configuration system."""

import pytest
from pydantic import ValidationError

from trading_platform.core.config import AppConfig, MarketDataConfig, RiskConfig, load_config
from trading_platform.core.constants import TradingMode
from trading_platform.core.exceptions import ConfigurationError


def test_default_config_loading():
    """Verify default.yaml loads with expected default values."""
    config = load_config(config_dir="config", env_override="paper")
    assert config.environment == TradingMode.PAPER
    assert config.strategy_id == "sma_momentum_v1"
    assert config.live_trading_enabled is False
    assert config.risk.max_position_size_usd == 1000.0
    assert config.risk.max_portfolio_exposure_usd == 5000.0
    assert "BTCUSDT" in config.market_data.active_symbols


def test_environment_override_merging(monkeypatch):
    """Verify backtest.yaml overrides logging level and environment."""
    monkeypatch.delenv("PLATFORM__LOGGING__LEVEL", raising=False)
    monkeypatch.setattr("trading_platform.core.config.load_env_file", lambda *a, **kw: None)
    config = load_config(config_dir="config", env_override="backtest")
    assert config.environment == TradingMode.BACKTEST
    assert config.logging.level == "WARNING"


def test_environment_variable_override(monkeypatch):
    """Verify environment variables take top precedence over YAML files."""
    monkeypatch.setenv("PLATFORM__RISK__MAX_LEVERAGE", "10.0")
    monkeypatch.setenv("PLATFORM__STRATEGY_ID", "custom_momentum_v2")

    config = load_config(config_dir="config", env_override="paper")
    assert config.risk.max_leverage == 10.0
    assert config.strategy_id == "custom_momentum_v2"


def test_live_mode_safety_guard():
    """Verify that LIVE mode without live_trading_enabled raises ConfigurationError."""
    with pytest.raises(ConfigurationError, match="CRITICAL SAFETY ERROR"):
        AppConfig(
            environment=TradingMode.LIVE,
            live_trading_enabled=False,
        )


def test_live_mode_allowed_when_explicit():
    """Verify that LIVE mode is allowed only when live_trading_enabled is True."""
    config = AppConfig(
        environment=TradingMode.LIVE,
        live_trading_enabled=True,
    )
    assert config.environment == TradingMode.LIVE
    assert config.live_trading_enabled is True


def test_invalid_risk_parameters():
    """Verify Pydantic validates bounds on risk configuration."""
    with pytest.raises(ValidationError):
        RiskConfig(max_leverage=150.0)  # > 125x not allowed

    with pytest.raises(ValidationError):
        RiskConfig(max_position_size_usd=-500.0)  # Must be > 0


def test_empty_symbols_rejected():
    """Verify empty symbols list is rejected."""
    with pytest.raises(
        ValidationError, match="active_symbols list must contain at least one symbol"
    ):
        MarketDataConfig(active_symbols=[])
