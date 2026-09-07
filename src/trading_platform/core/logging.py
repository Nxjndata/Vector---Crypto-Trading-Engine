"""Structured logging system with context variables and JSON/Console formatting."""

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Context variables for request/event tracing
_context_strategy_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "strategy_id", default=None
)
_context_symbol: contextvars.ContextVar[str | None] = contextvars.ContextVar("symbol", default=None)
_context_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
_context_order_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "order_id", default=None
)


def set_log_context(
    strategy_id: str | None = None,
    symbol: str | None = None,
    correlation_id: str | None = None,
    order_id: str | None = None,
) -> None:
    """Set logging context for the current async task or thread."""
    if strategy_id is not None:
        _context_strategy_id.set(strategy_id)
    if symbol is not None:
        _context_symbol.set(symbol)
    if correlation_id is not None:
        _context_correlation_id.set(correlation_id)
    if order_id is not None:
        _context_order_id.set(order_id)


def clear_log_context() -> None:
    """Clear contextual logging variables."""
    _context_strategy_id.set(None)
    _context_symbol.set(None)
    _context_correlation_id.set(None)
    _context_order_id.set(None)


def get_current_log_context() -> dict[str, Any]:
    """Retrieve active logging context dictionary."""
    ctx: dict[str, Any] = {}
    if (val := _context_strategy_id.get()) is not None:
        ctx["strategy_id"] = val
    if (val := _context_symbol.get()) is not None:
        ctx["symbol"] = val
    if (val := _context_correlation_id.get()) is not None:
        ctx["correlation_id"] = val
    if (val := _context_order_id.get()) is not None:
        ctx["order_id"] = val
    return ctx


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured log aggregators and audits."""

    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }

        # Include active context
        context = get_current_log_context()
        if context:
            log_record["context"] = context

        # Include custom extra fields attached to record
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            log_record["extra"] = record.extra_fields

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_record)


class ConsoleFormatter(logging.Formatter):
    """Clean, human-readable console formatter with color and context indicators."""

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        ctx_parts = []
        ctx = get_current_log_context()
        for k, v in ctx.items():
            ctx_parts.append(f"{k}={v}")
        ctx_str = f" [{', '.join(ctx_parts)}]" if ctx_parts else ""

        msg = f"{timestamp} | {color}{record.levelname:<8}{self.RESET} | {record.name}{ctx_str} - {record.getMessage()}"
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return msg


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    log_file: str | None = None,
) -> logging.Logger:
    """Initialize structured root logger with console and optional file handlers.

    Args:
        level: Minimum log level string (e.g. DEBUG, INFO, WARNING, ERROR).
        json_format: If True, outputs JSON format to stdout; otherwise colored console.
        log_file: Optional path to append log entries.

    Returns:
        Configured root logger.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates on re-init
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    if json_format:
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(ConsoleFormatter())
    root_logger.addHandler(console_handler)

    # File Handler
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(file_path), encoding="utf-8")
        # Files are always written in JSON for structured searchability
        file_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(file_handler)

    # Silence overly verbose external libraries
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Convenience helper to retrieve named logger."""
    return logging.getLogger(name)
