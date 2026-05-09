"""Create the Compose test database when it does not exist."""

from __future__ import annotations

import asyncio

import asyncpg  # type: ignore[import-untyped]

from conf import settings


def _quote_identifier(value: str) -> str:
    """Return a Postgres identifier quoted for use in CREATE DATABASE."""

    escaped = value.replace('"', '""')
    return f'"{escaped}"'


async def ensure_test_database() -> None:
    """Create the configured test database without touching the app database."""

    connection = await asyncpg.connect(
        host=settings.DATABASE_HOST,
        port=settings.DATABASE_PORT,
        user=settings.DATABASE_USER,
        password=settings.DATABASE_PASSWORD,
        database="postgres",
    )
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            settings.DATABASE_NAME,
        )
        if exists is None:
            await connection.execute(f"CREATE DATABASE {_quote_identifier(settings.DATABASE_NAME)}")
    finally:
        await connection.close()


def main() -> None:
    """CLI entrypoint for Docker Compose test setup."""

    asyncio.run(ensure_test_database())


if __name__ == "__main__":
    main()
