"""Permissive auth provider used until real auth rollout is connected."""

from __future__ import annotations

from auth.base import AuthProvider
from auth.models import AuthContext


class AllowAllAuthProvider(AuthProvider):
    """Temporary provider that treats local calls as trusted."""

    def authenticate(self) -> AuthContext:
        return AuthContext(
            subject="local-development",
            roles=("admin",),
            scopes=("mcp:*",),
        )
