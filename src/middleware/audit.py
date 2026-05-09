"""Authenticated MCP audit middleware.

This module owns shared audit logging around authenticated MCP usage.
It does not verify JWTs itself. FastMCP auth still performs token validation
and scope enforcement. The middleware simply observes the already-authenticated
request path and records:

- who called the MCP server
- which MCP method was used
- which tool or resource was accessed
- how long the call took
- whether a tool returned an MCP error result

That keeps request-level audit concerns out of individual tool
implementations while preserving deterministic, tool-specific logs inside the
tool layer.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from time import perf_counter
from typing import Any, cast
from uuid import UUID

import mcp.types as mt
from fastmcp.resources.base import ResourceResult
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult

from logging_config import get_logger
from services.agent_calls import AgentCallAuditService, AgentCallCreateError
from services.log_collection import LogCollectionService
from utils.mcp_errors import AgentToolErrorResult, build_agent_tool_error_result
from utils.types import JSONObject

logger: logging.Logger = get_logger("middleware.audit")
agent_call_audit_service = AgentCallAuditService()
WORKFLOW_AGENT_CLIENT_ID = "workflow-agent"
WORKFLOW_AGENT_CLIENT_TYPE = "workflow_agent"


def _build_auth_fields(token: AccessToken | None) -> dict[str, Any]:
    """Serialize authenticated caller identity into log-friendly fields.

    The audit log should capture stable caller metadata without persisting the
    raw bearer token itself.
    """

    if token is None:
        return {"authenticated": False}

    claims = token.claims
    return {
        "authenticated": True,
        "client_id": token.client_id,
        "scope_count": len(token.scopes),
        "subject": claims.get("sub", token.client_id),
        "client_type": claims.get("client_type"),
        "allowed_projects": claims.get("allowed_projects"),
        "projects_access": claims.get("projects_access"),
    }


def _tool_result_is_error(result: ToolResult) -> bool:
    """Return whether one tool result is flagged as an MCP error result.

    FastMCP tools may return agent-facing MCP errors while still using an HTTP
    200 response. The audit layer records that distinction explicitly.
    """

    try:
        return bool(getattr(result.to_mcp_result(), "isError", False))
    except Exception:
        return False


def _is_workflow_agent(token: AccessToken | None) -> bool:
    """Return whether the authenticated caller is the fixed workflow agent."""

    if token is None:
        return False
    return (
        token.client_id == WORKFLOW_AGENT_CLIENT_ID
        or token.claims.get("client_type") == WORKFLOW_AGENT_CLIENT_TYPE
    )


def _workflow_agent_session_error(token: AccessToken | None) -> AgentToolErrorResult:
    """Return an agent-facing error for workflow agents requesting session workspace."""

    return build_agent_tool_error_result(
        error_code="workspace_not_allowed",
        message="workflow-agent cannot use workspace='session'.",
        retry_tips=[
            "Retry with workspace='workflow' for scheduled workflow collection.",
            "Use a non-workflow agent token for interactive session investigations.",
        ],
        details={
            "client_id": token.client_id if token is not None else None,
            "client_type": (token.claims.get("client_type") if token is not None else None),
            "workspace": "session",
        },
    )


def _prepare_collect_logs_session_id(
    context: MiddlewareContext[mt.CallToolRequestParams],
) -> UUID:
    """Inject the effective collect_logs session id into tool arguments."""

    arguments: dict[str, Any] = dict(context.message.arguments or {})
    session_id = LogCollectionService.resolve_session_id(arguments.get("session_id"))

    arguments["session_id"] = str(session_id)
    context.message.arguments = arguments
    return session_id


async def _create_agent_call(
    *,
    token: AccessToken | None,
    tool_name: str,
    session_id: UUID,
    arguments: dict[str, Any] | None,
) -> UUID | AgentToolErrorResult:
    """Create one AgentCall row when a request has an effective session id."""

    result = await agent_call_audit_service.create_tool_call(
        session_id=session_id,
        workspace=str((arguments or {}).get("workspace", "workflow")),
        event="mcp_call_tool",
        token=token,
        tool_name=tool_name,
        arguments=arguments,
    )
    if isinstance(result, AgentCallCreateError):
        return build_agent_tool_error_result(
            error_code=result.error_code,
            message=result.message,
            retry_tips=result.retry_tips,
            details=cast(JSONObject, result.details),
        )
    return result


class AccessAuditMiddleware(Middleware):
    """Log authenticated MCP usage around JWT-protected request handling.

    This middleware is intentionally narrow. It logs the shared request
    lifecycle for `tools/list`, `tools/call`, and `resources/read`.
    Tool-specific fields such as snapshot ids or grouped-error counts still
    belong in the tool modules themselves.
    """

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Log one authenticated `tools/list` request after filtering completes."""

        token = get_access_token()
        result = await call_next(context)
        logger.info(
            "mcp tools listed",
            extra={
                "event": "mcp_list_tools",
                "tool_count": len(result),
                **_build_auth_fields(token),
            },
        )
        return result

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        """Log one authenticated resource read with timing and caller identity."""

        token = get_access_token()
        started_at = perf_counter()
        result = await call_next(context)
        logger.info(
            "mcp resource read",
            extra={
                "event": "mcp_read_resource",
                "uri": str(context.message.uri),
                "duration_seconds": round(perf_counter() - started_at, 3),
                **_build_auth_fields(token),
            },
        )
        return result

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Log one authenticated tool call with timing and MCP error status.

        Unexpected Python exceptions are logged with stack traces and then
        re-raised so FastMCP can preserve its normal error handling behavior.
        """

        token = get_access_token()
        started_at = perf_counter()
        tool_name = context.message.name
        arguments: dict[str, Any] = dict(context.message.arguments or {})
        if (
            tool_name == "collect_logs"
            and arguments.get("workspace") == "session"
            and _is_workflow_agent(token)
        ):
            return _workflow_agent_session_error(token)

        session_id = (
            _prepare_collect_logs_session_id(context) if tool_name == "collect_logs" else None
        )
        agent_call_pk: UUID | None = None
        if session_id is not None:
            agent_call_result = await _create_agent_call(
                token=token,
                tool_name=tool_name,
                session_id=session_id,
                arguments=context.message.arguments,
            )
            if isinstance(agent_call_result, AgentToolErrorResult):
                return agent_call_result
            agent_call_pk = agent_call_result

        try:
            result = await call_next(context)
        except Exception:
            duration_seconds = round(perf_counter() - started_at, 3)
            await agent_call_audit_service.complete_tool_call(
                agent_call_pk=agent_call_pk,
                session_id=session_id,
                tool_name=tool_name,
                duration_seconds=duration_seconds,
                success=False,
                error_code="mcp_call_tool_exception",
            )
            logger.exception(
                "mcp tool call crashed",
                extra={
                    "event": "mcp_call_tool_exception",
                    "tool_name": tool_name,
                    "session_id": str(session_id) if session_id is not None else None,
                    "duration_seconds": duration_seconds,
                    **_build_auth_fields(token),
                },
            )
            raise

        tool_error = _tool_result_is_error(result)
        duration_seconds = round(perf_counter() - started_at, 3)
        await agent_call_audit_service.complete_tool_call(
            agent_call_pk=agent_call_pk,
            session_id=session_id,
            tool_name=tool_name,
            duration_seconds=duration_seconds,
            success=not tool_error,
            error_code="mcp_tool_error" if tool_error else None,
        )
        return result
