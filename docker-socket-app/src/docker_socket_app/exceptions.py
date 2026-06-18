"""Custom exceptions raised by the Docker socket app."""

from __future__ import annotations


class DockerSocketAppException(Exception):
    """Base class for expected Docker socket app exceptions."""


class ProtocolException(DockerSocketAppException, ValueError):
    """Raised when a socket request does not match the app's wire contract."""
