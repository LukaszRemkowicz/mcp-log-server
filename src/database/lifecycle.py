"""Database initialization and shutdown helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from tortoise import Tortoise

from database.config import TORTOISE_ORM
from database.signals import register_database_signals


async def initialize_database(config: dict[str, Any]) -> None:
    """Initialize Tortoise ORM for the configured database."""

    await Tortoise.init(config=config)
    register_database_signals()


async def close_database() -> None:
    """Close Tortoise ORM connections."""

    await Tortoise.close_connections()


@asynccontextmanager
async def database_lifespan(_app: Any) -> AsyncIterator[None]:
    """Run database startup and shutdown around the FastMCP application."""

    await initialize_database(TORTOISE_ORM)
    try:
        yield
    finally:
        await close_database()
