"""Tests for runtime settings helpers."""

from settings import Settings


def test_settings_expose_uppercase_fields() -> None:
    settings = Settings()

    assert settings.DEFAULT_LOG_WINDOW == "24h"
    assert settings.WORKFLOW_ARCHIVE_RETENTION == "14d"
    assert settings.LOG_SNAPSHOT_RETENTION == "7d"
    assert settings.MCP_PATH == "/mcp"
    assert settings.MCP_JSON_RESPONSE is True


def test_settings_expose_database_defaults() -> None:
    settings = Settings()

    assert settings.DATABASE_HOST == "127.0.0.1"
    assert settings.DATABASE_PORT == 5432
    assert settings.DATABASE_NAME == "mcp_log_server"
    assert settings.DATABASE_USER == "mcp_log_server"
    assert settings.DATABASE_PASSWORD == "mcp-log-server-local-password"
    assert (
        settings.db
        == "postgres://mcp_log_server:mcp-log-server-local-password@127.0.0.1:5432/mcp_log_server"
    )


def test_db_escapes_credentials() -> None:
    settings = Settings(
        DATABASE_HOST="db.internal",
        DATABASE_PORT=5433,
        DATABASE_NAME="mcp log/server",
        DATABASE_USER="mcp user",
        DATABASE_PASSWORD="pass/word@local",
    )

    assert (
        settings.db
        == "postgres://mcp%20user:pass%2Fword%40local@db.internal:5433/mcp%20log%2Fserver"
    )
