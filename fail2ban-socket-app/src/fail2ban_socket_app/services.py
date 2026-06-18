"""Fixed operation registry for the fail2ban socket app."""

from __future__ import annotations

from typing import Any

from .exceptions import ProtocolException
from .schemas import Fail2banBackend


class Fail2banSocketService:
    """Validate and dispatch fixed fail2ban diagnostic operations.

    This service is the socket app's service layer. It validates that each
    request uses a known operation name, validates primitive parameter types,
    and calls one explicit backend method per operation.

    The service intentionally does not know about MCP callers, projects, JWTs,
    audit records, or response shaping. The consuming app must authorize callers
    before sending requests here. This app only provides fixed read-only
    fail2ban diagnostics over a Unix socket.
    """

    def __init__(self, backend: Fail2banBackend) -> None:
        """Create a fail2ban socket service backed by one implementation."""

        self.backend = backend

    def dispatch(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and run one supported operation."""

        if operation == "list_jails":
            self._reject_params(params)
            return self.backend.list_jails()
        if operation == "get_jail_bans":
            return self.backend.get_jail_bans(jail_name=self._required_string(params, "jail_name"))
        if operation == "blocked_ips_summary":
            self._reject_params(params)
            return self.backend.blocked_ips_summary()

        raise ProtocolException(f"Unsupported fail2ban socket operation: {operation}")

    @staticmethod
    def _required_string(params: dict[str, Any], key: str) -> str:
        """Return a required non-empty string parameter or raise `ProtocolException`."""

        value = params.get(key)
        if not isinstance(value, str) or not value:
            raise ProtocolException(f"Parameter '{key}' must be a non-empty string.")
        return value

    @staticmethod
    def _reject_params(params: dict[str, Any]) -> None:
        """Reject parameters for operations whose contract takes no arguments."""

        if params:
            raise ProtocolException("This operation does not accept parameters.")
