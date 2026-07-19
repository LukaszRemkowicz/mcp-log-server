"""Small structured-JSON logging setup for the socket app."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from .settings import LOG_LEVEL

_STRUCTURED_FIELDS = (
    "socket_path",
    "operation",
    "ok",
    "duration_ms",
    "error_category",
    "error_code",
)


class JsonFormatter(logging.Formatter):
    """Format socket lifecycle and request events as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field_name in _STRUCTURED_FIELDS:
            if hasattr(record, field_name):
                payload[field_name] = getattr(record, field_name)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging() -> logging.Logger:
    """Configure the socket app logger to emit JSON lines to stdout."""

    logger = logging.getLogger("socket_app")
    logger.handlers.clear()
    logger.setLevel(LOG_LEVEL)
    logger.propagate = False
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger
