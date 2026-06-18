"""Tests for runtime settings helpers."""

from typing import Any

import pytest

import settings as settings_module
from conf import Settings, validate_runtime_settings
from tests.conftest import override_settings


def test_settings_expose_uppercase_fields() -> None:
    runtime_settings = Settings()

    assert runtime_settings.MCP_HOST == settings_module.MCP_HOST
    assert runtime_settings.MCP_PORT == settings_module.MCP_PORT
    assert runtime_settings.LOG_FORMAT == "json"
    assert runtime_settings.DEFAULT_LOG_WINDOW == "24h"
    assert runtime_settings.WORKFLOW_ARCHIVE_RETENTION == "14d"
    assert runtime_settings.LOG_SNAPSHOT_RETENTION == "7d"
    assert (
        runtime_settings.FAIL2BAN_SOCKET_APP_SOCKET_PATH.as_posix()
        == "/run/fail2ban-socket-app/gateway.sock"
    )
    assert (
        runtime_settings.DOCKER_SOCKET_APP_SOCKET_PATH.as_posix()
        == "/run/docker-socket-app/gateway.sock"
    )
    assert runtime_settings.FAIL2BAN_JAILS == [
        "portfolio-nginx-probes",
        "portfolio-traefik-probes",
        "portfolio-keycloak-token",
    ]
    assert runtime_settings.JWT_JWKS_URI == ""
    assert runtime_settings.SITE_DOMAIN == settings_module.SITE_DOMAIN
    assert runtime_settings.TLS_CERTIFICATE_SUBDOMAINS == ["admin", "stage", "mcp"]
    assert runtime_settings.TLS_CERTIFICATE_TIMEOUT_SECONDS == 5
    assert runtime_settings.TLS_CERTIFICATE_EXPIRY_WARNING_DAYS == 30
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


def test_settings_can_override_tls_certificate_defaults() -> None:
    runtime_settings = Settings(
        {
            "SITE_DOMAIN": "example.com",
            "TLS_CERTIFICATE_SUBDOMAINS": ["admin", "mcp"],
            "TLS_CERTIFICATE_TIMEOUT_SECONDS": 3,
            "TLS_CERTIFICATE_EXPIRY_WARNING_DAYS": 10,
        }
    )

    assert runtime_settings.SITE_DOMAIN == "example.com"
    assert runtime_settings.TLS_CERTIFICATE_SUBDOMAINS == ["admin", "mcp"]
    assert runtime_settings.TLS_CERTIFICATE_TIMEOUT_SECONDS == 3
    assert runtime_settings.TLS_CERTIFICATE_EXPIRY_WARNING_DAYS == 10


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


@pytest.mark.parametrize(
    ("settings_overrides", "expected_message"),
    [
        (
            {"ENVIRONMENT": "prod", "JWT_SHARED_SECRET": "change-me-local-dev-secret"},
            "JWT_SHARED_SECRET must be set to a production secret",
        ),
        (
            {"ENVIRONMENT": "prod", "DATABASE_PASSWORD": "local-secret"},
            "DATABASE_PASSWORD must be set to a production secret",
        ),
    ],
)
def test_validate_runtime_settings_rejects_production_insecure_defaults(
    settings_overrides: dict[str, Any],
    expected_message: str,
) -> None:
    runtime_settings = Settings(
        {
            "ENVIRONMENT": "prod",
            "JWT_SHARED_SECRET": "prod-secret",
            "JWT_JWKS_URI": "",
            "DATABASE_PASSWORD": "prod-db-secret",
        },
        **settings_overrides,
    )

    with pytest.raises(RuntimeError, match=expected_message):
        validate_runtime_settings(runtime_settings)


def test_validate_runtime_settings_allows_production_jwks_without_shared_secret() -> None:
    runtime_settings = Settings(
        {
            "ENVIRONMENT": "prod",
            "JWT_SHARED_SECRET": "change-me-local-dev-secret",
            "JWT_JWKS_URI": "https://auth.example.com/realms/mcp/protocol/openid-connect/certs",
            "JWT_ISSUER": "https://auth.example.com/realms/mcp",
            "JWT_AUDIENCE": "mcp-log-server",
            "DATABASE_PASSWORD": "prod-db-secret",
        }
    )

    validate_runtime_settings(runtime_settings)


@pytest.mark.parametrize(
    ("settings_overrides", "expected_message"),
    [
        (
            {"JWT_ISSUER": "mcp-log-server-dev"},
            "JWT_ISSUER must be set to the production token issuer",
        ),
        (
            {"JWT_AUDIENCE": ""},
            "JWT_AUDIENCE must be set for production token validation",
        ),
    ],
)
def test_validate_runtime_settings_rejects_incomplete_production_jwks_config(
    settings_overrides: dict[str, Any],
    expected_message: str,
) -> None:
    runtime_settings = Settings(
        {
            "ENVIRONMENT": "prod",
            "JWT_SHARED_SECRET": "change-me-local-dev-secret",
            "JWT_JWKS_URI": "https://auth.example.com/realms/mcp/protocol/openid-connect/certs",
            "JWT_ISSUER": "https://auth.example.com/realms/mcp",
            "JWT_AUDIENCE": "mcp-log-server",
            "DATABASE_PASSWORD": "prod-db-secret",
        },
        **settings_overrides,
    )

    with pytest.raises(RuntimeError, match=expected_message):
        validate_runtime_settings(runtime_settings)


def test_validate_runtime_settings_allows_development_defaults() -> None:
    runtime_settings = Settings(
        {
            "ENVIRONMENT": "dev",
            "JWT_SHARED_SECRET": "change-me-local-dev-secret",
            "DATABASE_PASSWORD": "local-secret",
        }
    )

    validate_runtime_settings(runtime_settings)
