"""Allowlisted JSON logging; never serialize requests or environment settings."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_log_level: ContextVar[int] = ContextVar("request_log_level", default=logging.INFO)

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": "fleetlink-api",
            "event": record.getMessage(),
            "correlation_id": correlation_id.get(),
        }
        for field in ("status_code", "duration_ms", "error_type"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload)


class RequestLevelFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= request_log_level.get()


def configure_logging() -> None:
    """Install one process-wide sink; request levels belong to application context."""
    logger = logging.getLogger("fleetlink")
    if any(isinstance(handler.formatter, JsonFormatter) for handler in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestLevelFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
