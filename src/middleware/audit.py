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

import mcp.types as mt
from fastmcp.resources.base import ResourceResult
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult

from conf import settings
from logging_config import get_logger
from services.project_authorization import ProjectAuthorizationError, ProjectAuthorizationService
from services.project_manifest import ProjectManifestService
from tools.errors import build_collect_logs_error_result
from tools.models import SnapshotWorkspace

logger: logging.Logger = get_logger("middleware.audit")
project_authorization_service = ProjectAuthorizationService()
project_manifest_service = ProjectManifestService()


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
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
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
        tool_arguments = context.message.arguments or {}

        if tool_name == "collect_logs" and token is not None:
            requested_project_names = tool_arguments.get("project_names")
            available_project_names = [
                project_summary.project_name
                for project_summary in project_manifest_service.all().root
            ]
            authorized_project_names = project_authorization_service.authorize_caller_for_projects(
                token,
                requested_project_names=(
                    requested_project_names if isinstance(requested_project_names, list) else None
                ),
                available_project_names=available_project_names,
            )
            if isinstance(authorized_project_names, ProjectAuthorizationError):
                message = authorized_project_names.message
                logger.info(
                    "mcp tool authorization rejected",
                    extra={
                        "event": "mcp_call_tool_authorization_error",
                        "tool_name": tool_name,
                        "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                        **_build_auth_fields(token),
                    },
                )
                return build_collect_logs_error_result(
                    message,
                    settings=settings,
                    access_token=token,
                    project_names=(
                        requested_project_names
                        if isinstance(requested_project_names, list)
                        else None
                    ),
                    workspace=cast(
                        SnapshotWorkspace,
                        tool_arguments.get("workspace") or "workflow",
                    ),
                    session_id=tool_arguments.get("session_id"),
                )

            updated_arguments = dict(tool_arguments)
            updated_arguments["project_names"] = authorized_project_names
            context = context.copy(
                message=context.message.model_copy(update={"arguments": updated_arguments})
            )

        try:
            result = await call_next(context)
        except Exception:
            logger.exception(
                "mcp tool call crashed",
                extra={
                    "event": "mcp_call_tool_exception",
                    "tool_name": tool_name,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                    **_build_auth_fields(token),
                },
            )
            raise

        logger.info(
            "mcp tool called",
            extra={
                "event": "mcp_call_tool",
                "tool_name": tool_name,
                "tool_error": _tool_result_is_error(result),
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                **_build_auth_fields(token),
            },
        )
        return result
