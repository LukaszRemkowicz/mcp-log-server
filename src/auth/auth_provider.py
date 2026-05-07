"""FastMCP JWT auth provider wiring."""

from __future__ import annotations

from fastmcp.server.auth import AuthProvider, JWTVerifier

from settings import Settings


def build_auth_provider(settings: Settings) -> AuthProvider | None:
    """Build the FastMCP JWT auth provider."""

    return JWTVerifier(
        public_key=settings.JWT_SHARED_SECRET,
        issuer=settings.JWT_ISSUER,
        audience=settings.JWT_AUDIENCE,
        algorithm=settings.JWT_ALGORITHM,
    )
