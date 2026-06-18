"""JWT auth provider wiring tests."""

from __future__ import annotations

from auth.auth_provider import build_auth_provider
from conf import Settings


def test_build_auth_provider_uses_shared_secret_without_jwks(mocker) -> None:
    verifier = mocker.patch("auth.auth_provider.JWTVerifier", return_value=object())
    runtime_settings = Settings(
        {
            "JWT_SHARED_SECRET": "local-secret",
            "JWT_JWKS_URI": "",
            "JWT_ISSUER": "mcp-log-server-dev",
            "JWT_AUDIENCE": "mcp-log-server",
            "JWT_ALGORITHM": "HS256",
        }
    )

    build_auth_provider(runtime_settings)

    verifier.assert_called_once_with(
        public_key="local-secret",
        issuer="mcp-log-server-dev",
        audience="mcp-log-server",
        algorithm="HS256",
    )


def test_build_auth_provider_uses_keycloak_jwks_when_configured(mocker) -> None:
    verifier = mocker.patch("auth.auth_provider.JWTVerifier", return_value=object())
    runtime_settings = Settings(
        {
            "JWT_SHARED_SECRET": "unused-local-secret",
            "JWT_JWKS_URI": "https://auth.example.com/realms/mcp/protocol/openid-connect/certs",
            "JWT_ISSUER": "https://auth.example.com/realms/mcp",
            "JWT_AUDIENCE": "mcp-log-server",
            "JWT_ALGORITHM": "HS256",
        }
    )

    build_auth_provider(runtime_settings)

    verifier.assert_called_once_with(
        jwks_uri="https://auth.example.com/realms/mcp/protocol/openid-connect/certs",
        issuer="https://auth.example.com/realms/mcp",
        audience="mcp-log-server",
    )
