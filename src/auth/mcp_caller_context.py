"""MCP caller authorization model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.types import LogWorkspace

MCP_CALLER_REQUEST_STATE_ATTR = "caller"


@dataclass(frozen=True, slots=True)
class AuthenticatedMcpCaller:
    """Database-backed MCP caller allowed to use tools for one workspace."""

    client_id: str
    client_type: str
    workspace: LogWorkspace
    allowed_projects: frozenset[str]


def get_request_mcp_caller(request: Any | None = None) -> AuthenticatedMcpCaller | None:
    """Return the DB-backed caller stored on the active FastMCP request."""

    if request is None:
        try:
            from fastmcp.server.dependencies import get_http_request

            request = get_http_request()
        except RuntimeError:
            return None
    state = getattr(request, "state", None)
    caller = getattr(state, MCP_CALLER_REQUEST_STATE_ATTR, None)
    return caller if isinstance(caller, AuthenticatedMcpCaller) else None


def set_request_mcp_caller(
    caller: AuthenticatedMcpCaller,
    *,
    request: Any,
) -> None:
    """Attach the DB-backed caller to request state for downstream decorators."""

    setattr(request.state, MCP_CALLER_REQUEST_STATE_ATTR, caller)
