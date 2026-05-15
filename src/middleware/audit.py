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
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult
from tortoise.exceptions import BaseORMException

from auth.mcp_caller_context import AuthenticatedMcpCaller, set_request_mcp_caller
from core.types import LogWorkspace
from database.services.authentications import AuthenticationService
from database.services.project_manifests import ProjectManifestService as ProjectManifestDBService
from logging_config import get_logger
from services.agent_calls import AgentCallAuditService, AgentCallCreateError
from services.log_collection import LogCollectionService
from utils.mcp_errors import AgentToolErrorResult, build_agent_tool_error_result
from utils.types import JSONObject

logger: logging.Logger = get_logger("middleware.audit")
agent_call_audit_service = AgentCallAuditService()
authentication_service = AuthenticationService()
project_manifest_db_service = ProjectManifestDBService()

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


def _is_workflow_agent(token: AccessToken) -> bool:
    """Return whether the authenticated caller is the fixed workflow agent."""

    return (
        token.client_id == WORKFLOW_AGENT_CLIENT_ID
        or token.claims.get("client_type") == WORKFLOW_AGENT_CLIENT_TYPE
    )


def _workflow_agent_session_error(token: AccessToken) -> AgentToolErrorResult:
    """Return an agent-facing error for workflow agents requesting session workspace."""

    return build_agent_tool_error_result(
        error_code="workspace_not_allowed",
        message="workflow-agent cannot use workspace='session'.",
        retry_tips=[
            "Retry with workspace='workflow' for scheduled workflow collection.",
            "Use a non-workflow agent token for interactive session investigations.",
        ],
        details={
            "client_id": token.client_id,
            "client_type": token.claims.get("client_type"),
            "workspace": "session",
        },
    )


def _client_id_missing_error() -> AgentToolErrorResult:
    """Return an agent-facing error when a JWT lacks stable caller identity."""

    return build_agent_tool_error_result(
        error_code="invalid_client_id",
        message="Authenticated JWT must include a non-empty client_id.",
        retry_tips=[
            "Retry with a JWT issued for a concrete MCP client.",
            "Regenerate local development JWTs if they were created before client_id was required.",
        ],
        details={"required_claim": "client_id"},
    )


def _access_token_missing_error() -> AgentToolErrorResult:
    """Return an agent-facing error when no authenticated token is available."""

    return build_agent_tool_error_result(
        error_code="missing_access_token",
        message="Authenticated access token is required for MCP tool calls.",
        retry_tips=[
            "Retry through the authenticated MCP HTTP path.",
            "Regenerate local development JWTs if the Authorization header is missing.",
        ],
        details={"required_header": "Authorization"},
    )


def _client_type_missing_error() -> AgentToolErrorResult:
    """Return an agent-facing error when a JWT lacks stable caller type."""

    return build_agent_tool_error_result(
        error_code="invalid_client_type",
        message="Authenticated JWT must include a non-empty client_type.",
        retry_tips=[
            "Retry with a JWT issued for a concrete MCP client type.",
            (
                "Regenerate local development JWTs if they were created before "
                "client_type was required."
            ),
        ],
        details={"required_claim": "client_type"},
    )


def _client_not_authorized_error(
    *,
    client_id: str,
    client_type: str,
    workspace: LogWorkspace,
) -> AgentToolErrorResult:
    """Return an agent-facing error when the caller is not allowlisted."""

    return build_agent_tool_error_result(
        error_code="mcp_client_not_authorized",
        message="Authenticated MCP client is not allowed to call tools.",
        retry_tips=[
            "Ask an administrator to add this client_id and client_type to authentications.",
            "Retry with a JWT for an allowed MCP client.",
        ],
        details={
            "client_id": client_id,
            "client_type": client_type,
            "workspace": workspace,
        },
    )


def _client_has_no_allowed_projects_error(
    *,
    client_id: str,
    client_type: str,
    workspace: LogWorkspace,
) -> AgentToolErrorResult:
    """Return an agent-facing error when an allowlisted caller has no projects."""

    return build_agent_tool_error_result(
        error_code="mcp_client_has_no_allowed_projects",
        message="Authenticated MCP client is not allowed to access any project.",
        retry_tips=[
            "Ask an administrator to add at least one project to authentications.allowed_projects.",
        ],
        details={
            "client_id": client_id,
            "client_type": client_type,
            "workspace": workspace,
        },
    )


def _authentication_unavailable_error() -> AgentToolErrorResult:
    """Return an agent-facing error when the allowlist cannot be checked."""

    return build_agent_tool_error_result(
        error_code="authentication_unavailable",
        message="MCP client authentication allowlist is temporarily unavailable.",
        retry_tips=[
            "Retry later or ask administrator to check database connectivity and migrations.",
        ],
        details={},
    )


def _get_valid_client_id(token: AccessToken) -> str | None:
    """Return the authenticated MCP caller id when it is present and non-empty."""

    claim_client_id = token.claims.get("client_id")
    if not isinstance(claim_client_id, str) or not claim_client_id.strip():
        return None
    if not token.client_id or not token.client_id.strip():
        return None
    return token.client_id.strip()


def _get_valid_client_type(token: AccessToken) -> str | None:
    """Return the authenticated MCP caller type when it is present and non-empty."""

    claim_client_type = token.claims.get("client_type")
    if not isinstance(claim_client_type, str) or not claim_client_type.strip():
        return None
    return claim_client_type.strip()


def _resolve_tool_workspace(
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> LogWorkspace:
    """Return the workspace context implied by one tool call."""

    if tool_name == "close_agent_session":
        return LogWorkspace.SESSION
    if tool_name == "collect_logs":
        try:
            return LogWorkspace(str(arguments.get("workspace", LogWorkspace.WORKFLOW)))
        except ValueError:
            return LogWorkspace.WORKFLOW
    if arguments.get("session_id") is not None:
        return LogWorkspace.SESSION
    return LogWorkspace.WORKFLOW


async def _authorize_mcp_client(
    *,
    client_id: str,
    client_type: str,
    workspace: LogWorkspace,
    allow_empty_projects: bool = False,
) -> AuthenticatedMcpCaller | AgentToolErrorResult | None:
    """Return the DB-backed caller when a matching allowlist row exists."""

    authentication = await authentication_service.get_allowed(
        client_id=client_id,
        client_type=client_type,
        workspace=workspace,
    )
    if authentication is None:
        return None

    allowed_project_names = frozenset(authentication.allowed_projects)
    if "all" in allowed_project_names:
        allowed_project_names = frozenset(
            project_manifest.project_key
            for project_manifest in await project_manifest_db_service.all()
        )
    if not allowed_project_names and not allow_empty_projects:
        return _client_has_no_allowed_projects_error(
            client_id=client_id,
            client_type=client_type,
            workspace=workspace,
        )
    return AuthenticatedMcpCaller(
        client_id=client_id,
        client_type=client_type,
        workspace=workspace,
        allowed_projects=allowed_project_names,
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


def _attach_request_caller(caller: AuthenticatedMcpCaller) -> None:
    """Attach the DB-backed caller to the active HTTP request when available."""

    try:
        request = get_http_request()
    except RuntimeError:
        return
    set_request_mcp_caller(caller, request=request)


async def _authenticate_mcp_caller(
    *,
    token: AccessToken,
    workspace: LogWorkspace,
    tool_name: str,
) -> AuthenticatedMcpCaller | AgentToolErrorResult:
    """Authorize the JWT caller against DB allowlist and attach request state."""

    client_id = _get_valid_client_id(token)
    if client_id is None:
        return _client_id_missing_error()
    client_type = _get_valid_client_type(token)
    if client_type is None:
        return _client_type_missing_error()
    try:
        caller = await _authorize_mcp_client(
            client_id=client_id,
            client_type=client_type,
            workspace=workspace,
            allow_empty_projects=tool_name == "get_mcp_service_status",
        )
    except BaseORMException:
        logger.exception(
            "failed to check mcp client authentication allowlist",
            extra={
                "event": "mcp_client_authentication_check_failed",
                "client_id": client_id,
                "client_type": client_type,
            },
        )
        return _authentication_unavailable_error()
    if isinstance(caller, AgentToolErrorResult):
        return caller
    if caller is None:
        return _client_not_authorized_error(
            client_id=client_id,
            client_type=client_type,
            workspace=workspace,
        )
    _attach_request_caller(caller)
    return caller


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
        if token is None:
            return _access_token_missing_error()
        started_at = perf_counter()
        tool_name = context.message.name
        arguments: dict[str, Any] = dict(context.message.arguments or {})
        workspace = _resolve_tool_workspace(tool_name=tool_name, arguments=arguments)
        if (
            tool_name == "collect_logs"
            and workspace == LogWorkspace.SESSION
            and _is_workflow_agent(token)
        ):
            return _workflow_agent_session_error(token)
        caller_result = await _authenticate_mcp_caller(
            token=token,
            workspace=workspace,
            tool_name=tool_name,
        )
        if isinstance(caller_result, AgentToolErrorResult):
            return caller_result

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
