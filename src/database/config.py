"""Tortoise ORM configuration for the MCP log server."""

from __future__ import annotations

from typing import Any

from conf import settings


def build_tortoise_config() -> dict[str, Any]:
    """Build Tortoise ORM configuration from process settings."""

    return {
        "connections": {
            "default": settings.db,
        },
        "apps": {
            "models": {
                "models": ["database.models", "aerich.models"],
                "default_connection": "default",
                "migrations": "migrations/models",
            },
        },
    }


TORTOISE_ORM = build_tortoise_config()
