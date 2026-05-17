"""Tests for runtime settings helpers."""

from typing import Any

import pytest

from settings import Settings
from tests.conftest import override_settings


def test_settings_expose_uppercase_fields() -> None:
    settings = Settings()

    assert settings.DEFAULT_LOG_WINDOW == "24h"
    assert settings.WORKFLOW_ARCHIVE_RETENTION == "14d"
    assert settings.LOG_SNAPSHOT_RETENTION == "7d"
    assert settings.MCP_PATH == "/mcp"
    assert settings.MCP_JSON_RESPONSE is True
    assert settings.MCP_CALLER_MODEL == "database.models.McpCaller"


@pytest.mark.parametrize(
    ("database_config", "expected_dsn"),
    [
        (
            {
                "DATABASE_HOST": "127.0.0.1",
                "DATABASE_PORT": 5432,
                "DATABASE_NAME": "mcp_log_server",
                "DATABASE_USER": "mcp_log_server",
                "DATABASE_PASSWORD": "mcp-log-server-local-password",
            },
            "postgres://mcp_log_server:mcp-log-server-local-password@127.0.0.1:5432/mcp_log_server",
        ),
        (
            {
                "DATABASE_HOST": "db.internal",
                "DATABASE_PORT": 5433,
                "DATABASE_NAME": "mcp log/server",
                "DATABASE_USER": "mcp user",
                "DATABASE_PASSWORD": "pass/word@local",
            },
            "postgres://mcp%20user:pass%2Fword%40local@db.internal:5433/mcp%20log%2Fserver",
        ),
    ],
)
def test_settings_resolves_database_dsn(
    database_config: dict[str, Any],
    expected_dsn: str,
) -> None:
    with override_settings(**database_config) as test_settings:
        assert test_settings.db == expected_dsn
