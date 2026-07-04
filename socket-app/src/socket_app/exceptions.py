"""Custom exceptions raised by the generic socket app."""

from __future__ import annotations


class DockerSocketAppException(Exception):
    """Base class for expected generic socket app exceptions."""


class ProtocolException(DockerSocketAppException, ValueError):
    """Raised when a socket request does not match the app's wire contract."""


class DockerBackendError(DockerSocketAppException, RuntimeError):
    """Expected Docker SDK operation failure."""
