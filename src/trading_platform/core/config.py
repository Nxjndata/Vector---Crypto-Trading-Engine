"""Configuration system using Pydantic Settings and YAML with fail-fast validation."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_platform.core.constants import MarginMode, TradingMode
from trading_platform.core.exceptions import ConfigurationError


class DatabaseConfig(BaseModel):
    """Database connection and pool settings."""

    host: str = "localhost"
    port: int = 5433
    user: str = "postgres"
    password: str = "postgres"
    name: str = "algo_trading_db"
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False
    sqlite_path: str | None = None  # For fast unit tests without live Postgres

    @property
    def async_url(self) -> str:
        """Generate SQLAlchemy async connection URL."""
        if self.sqlite_path:
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        )

    @property
    def sync_url(self) -> str:
        """Generate SQLAlchemy sync connection URL (e.g. for Alembic)."""
        if self.sqlite_path:
            return f"sqlite:///{self.sqlite_path}"
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class ExchangeConfig(BaseModel):
    """Binance Futures exchange connectivity parameters."""

    testnet_api_key: str = ""
    testnet_api_secret: str = ""
    live_api_key: str = ""
    live_api_secret: str = ""
    recv_window: int = 5000
    rate_limit_margin_ms: int = 50

    def get_credentials(self, environment: TradingMode) -> tuple[str, str]:
        """Return dedicated API credentials for the target trading environment."""
        if environment == TradingMode.LIVE:
            return self.live_api_key, self.live_api_secret
        if environment == TradingMode.TESTNET:
            return self.testnet_api_key, self.testnet_api_secret
        return "", ""


class RiskConfig(BaseModel):
    """Risk management rules and threshold constraints."""

    max_position_size_usd: float = Field(default=1000.0, gt=0.0)
    max_portfolio_exposure_usd: float = Field(default=5000.0, gt=0.0)
    max_leverage: float = Field(default=5.0, ge=1.0, le=125.0)
    max_daily_loss_usd: float = Field(default=200.0, gt=0.0)
    max_drawdown_pct: float = Field(default=10.0, gt=0.0, le=100.0)
    min_liquidation_distance_pct: float = Field(default=15.0, gt=0.0, le=100.0)
    max_concurrent_positions: int = Field(default=3, ge=1)
    min_available_balance_usd: float = Field(default=50.0, ge=0.0)
    default_margin_mode: MarginMode = Field(default=MarginMode.ISOLATED)
    default_symbol_leverage: int = Field(default=5, ge=1, le=125)
    symbol_leverages: dict[str, int] = Field(default_factory=dict)


class MarketDataConfig(BaseModel):
    """Market data ingestion and symbol universe settings."""

    candle_timeframe: str = "1m"
    active_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    @field_validator("active_symbols", mode="before")
    @classmethod
    def validate_active_symbols(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            import json

            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(s).upper().strip() for s in parsed if str(s).strip()]
            except Exception:
                pass
            return [s.upper().strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            if not v:
                raise ValueError("active_symbols list must contain at least one symbol")
            return [str(s).upper().strip() for s in v if str(s).strip()]
        raise ValueError(f"Invalid active_symbols format: {v}")


class LoggingConfig(BaseModel):
    """Structured logging configuration."""

    level: str = "INFO"
    json_format: bool = False
    log_file: str | None = "logs/platform.log"


class AlertingConfig(BaseModel):
    """Webhook alerting configuration for Slack, Telegram, and generic HTTP endpoints."""

    enabled: bool = False
    webhook_url: str = ""
    webhook_type: str = "slack"  # "slack" | "telegram" | "generic"
    telegram_chat_id: str = ""
    min_severity: str = "WARNING"
    ws_disconnect_threshold_seconds: float = 10.0


class AppConfig(BaseSettings):
    """Root platform configuration."""

    model_config = SettingsConfigDict(
        env_prefix="PLATFORM__",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: TradingMode = TradingMode.PAPER
    strategy_id: str = "sma_momentum_v1"
    live_trading_enabled: bool = False
    clock_max_drift_ms: int = 1000

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    alerting: AlertingConfig = Field(default_factory=AlertingConfig)

    @model_validator(mode="after")
    def enforce_live_safety(self) -> "AppConfig":
        """Non-negotiable rule: LIVE environment requires explicit live_trading_enabled flag."""
        if self.environment == TradingMode.LIVE and not self.live_trading_enabled:
            raise ConfigurationError(
                "CRITICAL SAFETY ERROR: Platform is configured for LIVE mode, "
                "but 'live_trading_enabled' is False. Execution blocked."
            )
        return self


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dictionary update into base dictionary."""
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_env_file(env_path: str | Path = ".env") -> None:
    """Load key-value pairs from a .env file into os.environ if not already defined."""
    path = Path(env_path)
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass


def _apply_env_overrides(data: dict[str, Any], prefix: str = "PLATFORM__") -> dict[str, Any]:
    """Scan os.environ for PLATFORM__ keys and direct BINANCE_* keys, overriding nested dict values."""
    load_env_file()

    # 1. PLATFORM__ prefixed keys mapping
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        key_path = env_key[len(prefix) :].lower().split("__")
        curr = data
        for part in key_path[:-1]:
            if part not in curr or not isinstance(curr[part], dict):
                curr[part] = {}
            curr = curr[part]

        final_key = key_path[-1]
        if env_val == "" and final_key in curr and curr[final_key]:
            continue

        # Type coercion helper
        parsed_val: Any = env_val
        if env_val.lower() in ("true", "1", "yes"):
            parsed_val = True
        elif env_val.lower() in ("false", "0", "no"):
            parsed_val = False
        else:
            try:
                if "." in env_val:
                    parsed_val = float(env_val)
                else:
                    parsed_val = int(env_val)
            except ValueError:
                parsed_val = env_val
        curr[final_key] = parsed_val

    # 2. Direct Binance keys mapping (takes highest precedence)
    direct_mappings = {
        "BINANCE_TESTNET_API_KEY": ("exchange", "testnet_api_key"),
        "BINANCE_TESTNET_API_SECRET": ("exchange", "testnet_api_secret"),
        "BINANCE_LIVE_API_KEY": ("exchange", "live_api_key"),
        "BINANCE_LIVE_API_SECRET": ("exchange", "live_api_secret"),
    }
    for env_key, (section, field) in direct_mappings.items():
        if env_key in os.environ and os.environ[env_key]:
            if section not in data or not isinstance(data[section], dict):
                data[section] = {}
            data[section][field] = os.environ[env_key]

    return data


def load_config(
    config_dir: str | Path = "config",
    env_override: TradingMode | str | None = None,
) -> AppConfig:
    """Load configuration from default.yaml, merged with environment-specific yaml and env vars.

    Args:
        config_dir: Directory containing YAML configuration files.
        env_override: Optional override for environment (e.g. 'paper', 'testnet').

    Returns:
        Validated AppConfig instance.
    """
    config_path = Path(config_dir)
    data: dict[str, Any] = {}

    # 1. Load default.yaml if present
    default_yaml = config_path / "default.yaml"
    if default_yaml.exists():
        with open(default_yaml, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if loaded:
                data = loaded

    # 2. Determine target environment to load env-specific overrides
    target_env = env_override or data.get("environment", TradingMode.PAPER)
    if isinstance(target_env, TradingMode):
        target_env = target_env.value.lower()
    else:
        target_env = str(target_env).lower()

    env_yaml = config_path / f"{target_env}.yaml"
    if env_yaml.exists():
        with open(env_yaml, encoding="utf-8") as f:
            loaded_env = yaml.safe_load(f)
            if loaded_env:
                data = _deep_merge(data, loaded_env)

    # 3. Apply Environment Variable overrides
    data = _apply_env_overrides(data)

    # 4. If explicit programmatic env_override was passed, ensure it is preserved
    if env_override:
        if isinstance(env_override, TradingMode):
            data["environment"] = env_override
        else:
            data["environment"] = TradingMode(str(env_override).upper())

    if "environment" in data and isinstance(data["environment"], str):
        try:
            data["environment"] = TradingMode(data["environment"].upper())
        except ValueError:
            pass

    try:
        # 5. Instantiate and validate Pydantic AppConfig
        return AppConfig(**data)
    except Exception as e:
        if isinstance(e, ConfigurationError):
            raise
        raise ConfigurationError(f"Failed to validate platform configuration: {e}") from e
