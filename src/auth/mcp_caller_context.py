"""MCP caller authorization model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from core.types import LogWorkspace

MCP_CALLER_REQUEST_STATE_ATTR = "caller"


@dataclass(frozen=True, slots=True)
class AuthenticatedMcpCaller:
    """Database-backed MCP caller allowed to use tools for one workspace.

    `client_id` is the external authenticated identity from the JWT and is useful
    for logs, diagnostics, and matching token claims, for example
    `"workflow-agent"` or `"codex-agent-123"`.

    `caller_id` is the internal `mcp_callers.id` primary key and should be used
    for database ownership checks such as task, session, and audit-row filtering,
    for example `tasks.caller_id = 42`.
    """

    client_id: str
    client_type: str
    workspace: LogWorkspace
    allowed_projects: frozenset[str]
    caller_id: int


@dataclass(frozen=True, slots=True)
class AuthenticatedAgentSession:
    """Database-backed agent session resolved during one MCP request."""

    id: int
    name: str
    caller_id: int


def get_request_mcp_caller(request: Any | None = None) -> AuthenticatedMcpCaller:
    """Return the DB-backed caller stored on the active FastMCP request."""

    if request is None:
        from fastmcp.server.dependencies import get_http_request

        request = get_http_request()
    return cast(AuthenticatedMcpCaller, request.state.caller)


def set_request_mcp_caller(
    caller: AuthenticatedMcpCaller,
    *,
    request: Any,
) -> None:
    """Attach the DB-backed caller to request state for downstream decorators."""

    setattr(request.state, MCP_CALLER_REQUEST_STATE_ATTR, caller)
