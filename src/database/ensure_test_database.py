"""Recreate the Compose test database before running tests."""

from __future__ import annotations

import asyncio

import asyncpg  # type: ignore[import-untyped]

from conf import settings


def _quote_identifier(value: str) -> str:
    """Return a Postgres identifier quoted for use in CREATE DATABASE."""

    escaped = value.replace('"', '""')
    return f'"{escaped}"'


async def ensure_test_database() -> None:
    """Recreate the configured test database without touching the app database."""

    if not settings.DATABASE_NAME.endswith("_test"):
        msg = (
            "Refusing to reset a non-test database. "
            f"DATABASE_NAME must end with '_test', got {settings.DATABASE_NAME!r}."
        )
        raise RuntimeError(msg)

    connection = await asyncpg.connect(
        host=settings.DATABASE_HOST,
        port=settings.DATABASE_PORT,
        user=settings.DATABASE_USER,
        password=settings.DATABASE_PASSWORD,
        database="postgres",
    )
    try:
        await connection.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1
              AND pid <> pg_backend_pid()
            """,
            settings.DATABASE_NAME,
        )
        await connection.execute(
            f"DROP DATABASE IF EXISTS {_quote_identifier(settings.DATABASE_NAME)}"
        )
        await connection.execute(f"CREATE DATABASE {_quote_identifier(settings.DATABASE_NAME)}")
    finally:
        await connection.close()


def main() -> None:
    """CLI entrypoint for Docker Compose test setup."""

    asyncio.run(ensure_test_database())


if __name__ == "__main__":
    main()
