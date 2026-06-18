"""Custom exceptions raised by the fail2ban socket app."""

from __future__ import annotations


class Fail2banSocketAppException(Exception):
    """Base class for expected fail2ban socket app exceptions."""


class ProtocolException(Fail2banSocketAppException, ValueError):
    """Raised when a socket request does not match the app's wire contract."""
