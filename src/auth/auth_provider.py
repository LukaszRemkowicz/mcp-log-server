"""FastMCP JWT auth provider wiring."""

from __future__ import annotations

from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier

from conf import Settings


def build_auth_provider(settings: Settings) -> AuthProvider | None:
    """Build the FastMCP JWT auth provider."""

    jwks_uri = settings.JWT_JWKS_URI
    if jwks_uri:
        return JWTVerifier(
            jwks_uri=jwks_uri,
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
        )

    return JWTVerifier(
        public_key=settings.JWT_SHARED_SECRET,
        issuer=settings.JWT_ISSUER,
        audience=settings.JWT_AUDIENCE,
        algorithm=settings.JWT_ALGORITHM,
    )
