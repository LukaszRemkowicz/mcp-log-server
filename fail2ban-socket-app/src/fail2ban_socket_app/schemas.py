"""Shared contracts and JSON-compatible shapes for the fail2ban socket app."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict


class ErrorPayload(TypedDict):
    """JSON error payload returned to socket clients."""

    message: str


class ErrorResponse(TypedDict):
    """JSON response returned when request validation or execution fails."""

    ok: Literal[False]
    error: ErrorPayload


class SuccessResponse(TypedDict):
    """JSON response returned when a fixed fail2ban operation succeeds."""

    ok: Literal[True]
    result: dict[str, Any]


SocketResponse = SuccessResponse | ErrorResponse


class Fail2banBackend(Protocol):
    """Abstract fail2ban operation backend used by the operation registry.

    `Fail2banSocketService` owns request-level validation and operation
    routing. The backend owns the actual fail2ban implementation, usually
    through `fail2ban-client -s <socket>`.

    The contract is intentionally narrow. Clients can request only these
    read-only methods, not arbitrary fail2ban-client arguments, shell commands,
    or mutation operations such as ban, unban, reload, or restart.
    """

    def list_jails(self) -> dict[str, Any]:
        """Return the known fail2ban jails and jail count."""

    def get_jail_bans(self, *, jail_name: str) -> dict[str, Any]:
        """Return banned IP information for one fail2ban jail."""

    def blocked_ips_summary(self) -> dict[str, Any]:
        """Return banned IPs grouped by jail."""
