"""Base interfaces for auth providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from auth.models import AuthContext


class AuthProvider(ABC):
    """Normalized interface for caller authentication."""

    @abstractmethod
    def authenticate(self) -> AuthContext:
        """Return the current caller identity."""
