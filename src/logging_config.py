"""Logging configuration helpers for the MCP log server."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from settings import Settings
from utils.types import JSONObject

LOGGER_NAME = "mcp_log_server"


class JsonFormatter(logging.Formatter):
    """Render log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: JSONObject = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=True)


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
            logging.Formatter(
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
