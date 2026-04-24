"""FastMCP JWT auth provider wiring."""

from __future__ import annotations

from fastmcp.server.auth import AuthProvider, JWTVerifier

from settings import Settings


def build_auth_provider(settings: Settings) -> AuthProvider | None:
    """Build the FastMCP JWT auth provider."""

    return JWTVerifier(
        public_key=settings.jwt_shared_secret,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        algorithm=settings.jwt_algorithm,
    )
