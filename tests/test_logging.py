"""Tests for structured logging and context variable propagation."""

import json
import logging

from trading_platform.core.logging import (
    ConsoleFormatter,
    JSONFormatter,
    clear_log_context,
    get_current_log_context,
    set_log_context,
)


def test_log_context_lifecycle():
    """Verify setting and clearing log context."""
    clear_log_context()
    assert get_current_log_context() == {}

    set_log_context(strategy_id="strat_v1", symbol="BTCUSDT", correlation_id="req_123")
    ctx = get_current_log_context()
    assert ctx["strategy_id"] == "strat_v1"
    assert ctx["symbol"] == "BTCUSDT"
    assert ctx["correlation_id"] == "req_123"

    clear_log_context()
    assert get_current_log_context() == {}


def test_json_formatter_structure():
    """Verify JSONFormatter produces valid JSON containing standard and context fields."""
    formatter = JSONFormatter()
    set_log_context(strategy_id="momentum_test", order_id="ord_999")

    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="Risk threshold evaluated successfully",
        args=(),
        exc_info=None,
    )

    formatted_str = formatter.format(record)
    parsed = json.loads(formatted_str)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test_logger"
    assert parsed["message"] == "Risk threshold evaluated successfully"
    assert parsed["context"]["strategy_id"] == "momentum_test"
    assert parsed["context"]["order_id"] == "ord_999"
    assert "timestamp" in parsed


def test_console_formatter_output():
    """Verify ConsoleFormatter produces human-readable string with context."""
    formatter = ConsoleFormatter()
    set_log_context(symbol="SOLUSDT")

    record = logging.LogRecord(
        name="oms.executor",
        level=logging.WARNING,
        pathname=__file__,
        lineno=10,
        msg="Order execution latency elevated",
        args=(),
        exc_info=None,
    )

    formatted_str = formatter.format(record)
    assert "WARNING" in formatted_str
    assert "oms.executor" in formatted_str
    assert "symbol=SOLUSDT" in formatted_str
    assert "Order execution latency elevated" in formatted_str
