"""Tests for runtime settings helpers."""

from settings import Settings


def test_settings_expose_uppercase_fields() -> None:
    settings = Settings()

    assert settings.DEFAULT_LOG_WINDOW == "24h"
    assert settings.WORKFLOW_ARCHIVE_RETENTION == "14d"
    assert settings.LOG_SNAPSHOT_RETENTION == "7d"
    assert settings.MCP_PATH == "/mcp"
    assert settings.MCP_JSON_RESPONSE is True
