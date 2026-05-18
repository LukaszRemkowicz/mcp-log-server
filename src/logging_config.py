"""Logging configuration for the MCP log server."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from conf import Settings
from utils.types import JSONObject

LOGGER_NAME = "mcp_log_server"
_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def _normalize_log_value(value: Any) -> Any:
    """Convert one extra log value into a JSON-safe representation."""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_normalize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_log_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_log_value(item) for key, item in value.items()}
    return str(value)


class JsonFormatter(logging.Formatter):
    """Render log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: JSONObject = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = _normalize_log_value(value)
        return json.dumps(payload, ensure_ascii=True)


class PlainFormatter(logging.Formatter):
    """Render plain-text logs with appended structured extras."""

    def format(self, record: logging.LogRecord) -> str:
        base_message = super().format(record)
        extra_parts: list[str] = []
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS or key.startswith("_"):
                continue
            normalized = _normalize_log_value(value)
            extra_parts.append(f"{key}={normalized!r}")
        if not extra_parts:
            return base_message
        return f"{base_message} {' '.join(sorted(extra_parts))}"


def configure_logging(settings: Settings) -> logging.Logger:
    """Configure process logging and return the project logger."""

    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(settings.LOG_LEVEL.upper())
    logger.propagate = False

    handler = logging.StreamHandler()
    if settings.LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            PlainFormatter(
                fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )

    logger.addHandler(handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the base project logger or a namespaced child logger."""

    base_logger = logging.getLogger(LOGGER_NAME)
    if name is None:
        return base_logger
    return base_logger.getChild(name)
