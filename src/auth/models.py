"""Authentication models shared across auth providers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class AuthContext:
    """Normalized authenticated caller context."""

    subject: str
    roles: tuple[str, ...] = field(default_factory=tuple)
    scopes: tuple[str, ...] = field(default_factory=tuple)
    is_authenticated: bool = True
