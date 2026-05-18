"""Tests for runtime settings helpers."""

from typing import Any

import pytest

import settings as settings_module
from conf import Settings
from tests.conftest import override_settings


def test_settings_expose_uppercase_fields() -> None:
    runtime_settings = Settings()

    assert runtime_settings.MCP_HOST == settings_module.MCP_HOST
    assert runtime_settings.MCP_PORT == settings_module.MCP_PORT
    assert runtime_settings.LOG_FORMAT == "json"
    assert runtime_settings.DEFAULT_LOG_WINDOW == "24h"
    assert runtime_settings.WORKFLOW_ARCHIVE_RETENTION == "14d"
    assert runtime_settings.LOG_SNAPSHOT_RETENTION == "7d"
    assert runtime_settings.FAIL2BAN_SOCKET_PATH.as_posix() == "/var/run/fail2ban/fail2ban.sock"
    assert runtime_settings.FAIL2BAN_CLIENT_COMMAND == "fail2ban-client"
    assert runtime_settings.FAIL2BAN_JAILS == ["portfolio-nginx-probes", "portfolio-traefik-probes"]
    assert runtime_settings.FAIL2BAN_COMMAND_TIMEOUT_SECONDS == 5
    assert runtime_settings.MCP_PATH == "/mcp"
    assert runtime_settings.MCP_JSON_RESPONSE is True
    assert runtime_settings.CALLER_AUTH == "database.models.McpCaller"


def test_settings_can_load_injected_source() -> None:
    """Verify settings can be built from an explicit source object."""

    runtime_settings = Settings(
        {
            "LOG_LEVEL": "DEBUG",
            "LOG_FORMAT": "json",
        }
    )

    assert runtime_settings.LOG_LEVEL == "DEBUG"
    assert runtime_settings.LOG_FORMAT == "json"


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
