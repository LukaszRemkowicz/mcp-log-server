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
from fastmcp.exceptions import AuthorizationError, NotFoundError
from fastmcp.resources.base import Resource, ResourceResult
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult
from pydantic import ValidationError
from tortoise.exceptions import BaseORMException

from auth.mcp_caller_context import (
    AuthenticatedAgentSession,
    AuthenticatedMcpCaller,
    set_request_mcp_caller,
)
from core.types import LogWorkspace
from database.services.agent_sessions import AgentSessionService as AgentSessionDBService
from database.services.mcp_callers import McpCallerService
from database.services.project_manifests import ProjectManifestService as ProjectManifestDBService
from database.types import AgentSessionStatus
from logging_config import get_logger
from services.agent_calls import AgentCallAuditService, AgentCallCreateError
from services.log_collection import LogCollectionService
from utils.mcp_errors import AgentToolErrorResult, build_agent_tool_error_result
from utils.types import JSONObject

logger: logging.Logger = get_logger("middleware.audit")
agent_call_audit_service = AgentCallAuditService()
caller_service = McpCallerService()
project_manifest_db_service = ProjectManifestDBService()
agent_session_db_service = AgentSessionDBService()

WORKFLOW_AGENT_CLIENT_ID = "workflow-agent"
WORKFLOW_AGENT_CLIENT_TYPE = "workflow_agent"
SESSION_WORKSPACE_TOOLS = frozenset(
    {
        "close_agent_session",
    }
)
WORKSPACE_AGNOSTIC_TOOLS = frozenset(
    {
        "get_mcp_health_check",
        "get_mcp_service_status",
        "explain_project_source",
        "inspect_container_detail",
        "inspect_containers_health",
        "inspect_live_fail2ban_activity",
        "inspect_project_compose_state",
        "inspect_project_deployment",
        "inspect_project_scheduled_jobs",
        "inspect_project_runtime",
        "inspect_tls_certificate",
        "inspect_vps_containers",
        "inspect_vps_volumes",
        "list_container_directory",
        "list_projects",
        "list_project_directory",
        "read_container_file",
        "read_project_file",
        "read_project_manifest",
        "suggest_followup_window",
        "stat_container_path",
        "stat_project_path",
    }
)


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


def _unknown_tool_error(tool_name: str) -> AgentToolErrorResult:
    """Return an agent-facing error when a tool name is not available."""

    return build_agent_tool_error_result(
        error_code="unknown_tool",
        message=f"Unknown tool: {tool_name!r}.",
        retry_tips=[
            "Call tools/list to discover currently available tools for this token.",
            "Check that the token has the scope required for the tool.",
        ],
        details={"tool_name": tool_name},
    )


def _invalid_tool_arguments_error(
    *,
    tool_name: str,
    error: ValidationError,
) -> AgentToolErrorResult:
    """Return an agent-facing error for FastMCP/Pydantic argument validation."""

    invalid_arguments = sorted(
        {
            str(location[0])
            for item in error.errors()
            if (location := item.get("loc")) and isinstance(location, tuple)
        }
    )
    return build_agent_tool_error_result(
        error_code="invalid_tool_arguments",
        message="Tool arguments failed validation.",
        retry_tips=[
            "Check the tool schema from tools/list and retry with the expected argument types.",
            "Use arrays for list arguments such as project_names and source_keys.",
        ],
        details=cast(
            JSONObject,
            {
                "tool_name": tool_name,
                "invalid_arguments": invalid_arguments,
            },
        ),
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
            "Ask an administrator to add this client_id and client_type to mcp_callers.",
            "Retry with a JWT for an allowed MCP client.",
        ],
        details={
            "client_id": client_id,
            "client_type": client_type,
            "workspace": workspace,
        },
    )


def _collect_logs_workspace_argument_error() -> AgentToolErrorResult:
    """Return an agent-facing error when collect_logs receives private workspace input."""

    return build_agent_tool_error_result(
        error_code="invalid_tool_arguments",
        message="collect_logs workspace is owned by the authenticated MCP caller.",
        retry_tips=[
            "Retry without workspace; MCP injects it from the caller allowlist row.",
        ],
        details={"invalid_arguments": ["workspace"], "tool_name": "collect_logs"},
    )


def _caller_unavailable_error() -> AgentToolErrorResult:
    """Return an agent-facing error when the allowlist cannot be checked."""

    return build_agent_tool_error_result(
        error_code="caller_unavailable",
        message="MCP client caller allowlist is temporarily unavailable.",
        retry_tips=[
            "Retry later or ask administrator to check database connectivity and migrations.",
        ],
        details={},
    )


def _session_unavailable_error(session_id: str) -> AgentToolErrorResult:
    """Return an agent-facing error when an agent session cannot be prepared."""

    return build_agent_tool_error_result(
        error_code="session_unavailable",
        message="Agent session is temporarily unavailable.",
        retry_tips=[
            "Retry later or ask administrator to check database connectivity.",
        ],
        details={"session_id": session_id},
    )


def _session_not_found_error(session_id: str) -> AgentToolErrorResult:
    """Return an agent-facing error when a session belongs to another caller."""

    return build_agent_tool_error_result(
        error_code="session_not_found",
        message="Requested agent session was not found.",
        retry_tips=[
            "Retry with the exact session_id returned by collect_logs.",
            "Omit session_id to start a new interactive investigation session.",
        ],
        details={"session_id": session_id},
    )


def _session_closed_error(session_id: str) -> AgentToolErrorResult:
    """Return an agent-facing error when the requested session is closed."""

    return build_agent_tool_error_result(
        error_code="session_closed",
        message="Requested agent session is already closed.",
        retry_tips=[
            "Omit session_id to start a new interactive investigation session.",
            "Use an active session_id returned by collect_logs.",
        ],
        details={"session_id": session_id},
    )


def _get_valid_client_id(token: AccessToken) -> str | None:
    """Return the authenticated MCP caller id when it is present and non-empty."""

    claim_client_id = token.claims.get("client_id")
    if "client_id" in token.claims:
        if not isinstance(claim_client_id, str) or not claim_client_id.strip():
            return None
        return claim_client_id.strip()
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

    if tool_name in SESSION_WORKSPACE_TOOLS:
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
    allow_any_workspace: bool = False,
) -> AuthenticatedMcpCaller | AgentToolErrorResult | None:
    """Return the DB-backed caller when a matching allowlist row exists."""

    caller = await caller_service.get_allowed(
        client_id=client_id,
        client_type=client_type,
        workspace=workspace,
    )
    if caller is None and allow_any_workspace:
        caller = await caller_service.get_allowed_by_identity(
            client_id=client_id,
            client_type=client_type,
        )
    if caller is None:
        return None

    allowed_project_names = frozenset(caller.allowed_projects)
    if "all" in allowed_project_names:
        allowed_project_names = frozenset(
            project_manifest.project_key
            for project_manifest in await project_manifest_db_service.all()
        )
    return AuthenticatedMcpCaller(
        caller_id=caller.id,
        client_id=client_id,
        client_type=client_type,
        workspace=caller.workspace,
        allowed_projects=allowed_project_names,
    )


def _prepare_collect_logs_arguments(
    context: MiddlewareContext[mt.CallToolRequestParams],
    *,
    workspace: LogWorkspace,
) -> str:
    """Inject caller-owned collect_logs runtime arguments."""

    arguments: dict[str, Any] = dict(context.message.arguments or {})
    session_id = LogCollectionService.resolve_session_id(arguments.get("session_id"))

    arguments["workspace"] = workspace
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


async def _prepare_agent_session(
    *,
    caller: AuthenticatedMcpCaller,
    workspace: LogWorkspace,
    session_id: str,
) -> AuthenticatedAgentSession | AgentToolErrorResult:
    """Create/load and attach the agent session for a collect_logs request."""

    try:
        agent_session = await agent_session_db_service.get_or_create(
            name=session_id,
            caller_id=caller.caller_id,
        )
    except BaseORMException:
        logger.exception(
            "failed to prepare agent session",
            extra={
                "event": "agent_session_prepare_failed",
                "session_id": session_id,
                "caller_id": caller.caller_id,
            },
        )
        return _session_unavailable_error(session_id)
    if agent_session.caller_id != caller.caller_id:
        return _session_not_found_error(session_id)
    if workspace == LogWorkspace.SESSION and agent_session.status == AgentSessionStatus.CLOSED:
        return _session_closed_error(session_id)

    authenticated_agent_session = AuthenticatedAgentSession(
        id=agent_session.id,
        name=agent_session.name,
        caller_id=agent_session.caller_id,
    )
    return authenticated_agent_session


async def _authenticate_mcp_caller(
    *,
    token: AccessToken,
    workspace: LogWorkspace,
    tool_name: str,
    allow_any_workspace: bool = False,
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
            allow_any_workspace=allow_any_workspace,
        )
    except BaseORMException:
        logger.exception(
            "failed to check mcp client caller allowlist",
            extra={
                "event": "mcp_client_caller_check_failed",
                "client_id": client_id,
                "client_type": client_type,
            },
        )
        return _caller_unavailable_error()
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
    tool_name: str,
    session: AuthenticatedAgentSession,
    arguments: dict[str, Any] | None,
) -> UUID | AgentToolErrorResult:
    """Create one AgentCall row when a request has an effective session id."""

    result = await agent_call_audit_service.create_tool_call(
        session=session,
        event="mcp_call_tool",
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
        if token is not None:
            caller_result = await _authenticate_mcp_caller(
                token=token,
                workspace=LogWorkspace.WORKFLOW,
                tool_name="mcp_discovery",
                allow_any_workspace=True,
            )
            if isinstance(caller_result, AgentToolErrorResult):
                raise AuthorizationError(
                    str((caller_result.structured_content or {}).get("message"))
                )
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

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        """Log one authenticated `resources/list` request after filtering completes."""

        token = get_access_token()
        if token is not None:
            caller_result = await _authenticate_mcp_caller(
                token=token,
                workspace=LogWorkspace.WORKFLOW,
                tool_name="mcp_discovery",
                allow_any_workspace=True,
            )
            if isinstance(caller_result, AgentToolErrorResult):
                raise AuthorizationError(
                    str((caller_result.structured_content or {}).get("message"))
                )
        result = await call_next(context)
        logger.info(
            "mcp resources listed",
            extra={
                "event": "mcp_list_resources",
                "resource_count": len(result),
                **_build_auth_fields(token),
            },
        )
        return result

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]],
    ) -> Sequence[ResourceTemplate]:
        """Log authenticated `resources/templates/list` after filtering completes."""

        token = get_access_token()
        if token is not None:
            caller_result = await _authenticate_mcp_caller(
                token=token,
                workspace=LogWorkspace.WORKFLOW,
                tool_name="mcp_discovery",
                allow_any_workspace=True,
            )
            if isinstance(caller_result, AgentToolErrorResult):
                raise AuthorizationError(
                    str((caller_result.structured_content or {}).get("message"))
                )
        result = await call_next(context)
        logger.info(
            "mcp resource templates listed",
            extra={
                "event": "mcp_list_resource_templates",
                "resource_template_count": len(result),
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
        if token is not None:
            caller_result = await _authenticate_mcp_caller(
                token=token,
                workspace=LogWorkspace.WORKFLOW,
                tool_name="mcp_discovery",
                allow_any_workspace=True,
            )
            if isinstance(caller_result, AgentToolErrorResult):
                raise AuthorizationError(
                    str((caller_result.structured_content or {}).get("message"))
                )
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
        if tool_name == "collect_logs" and "workspace" in arguments:
            return _collect_logs_workspace_argument_error()
        workspace = _resolve_tool_workspace(tool_name=tool_name, arguments=arguments)
        allow_any_workspace = tool_name in WORKSPACE_AGNOSTIC_TOOLS or (
            tool_name == "collect_logs" and "workspace" not in arguments
        )
        caller_result = await _authenticate_mcp_caller(
            token=token,
            workspace=workspace,
            tool_name=tool_name,
            allow_any_workspace=allow_any_workspace,
        )
        if isinstance(caller_result, AgentToolErrorResult):
            return caller_result

        workspace = caller_result.workspace
        session_id = None
        if tool_name == "collect_logs":
            session_id = _prepare_collect_logs_arguments(context, workspace=workspace)
        elif isinstance(arguments.get("session_id"), str):
            session_id = arguments["session_id"]
        request_agent_session: AuthenticatedAgentSession | None = None
        if session_id is not None:
            agent_session_result = await _prepare_agent_session(
                caller=caller_result,
                workspace=workspace,
                session_id=session_id,
            )
            if isinstance(agent_session_result, AgentToolErrorResult):
                if tool_name == "collect_logs":
                    return agent_session_result
            else:
                request_agent_session = agent_session_result
        agent_call_pk: UUID | None = None
        if request_agent_session is not None:
            agent_call_result = await _create_agent_call(
                tool_name=tool_name,
                session=request_agent_session,
                arguments=context.message.arguments,
            )
            if isinstance(agent_call_result, AgentToolErrorResult):
                return agent_call_result
            agent_call_pk = agent_call_result

        try:
            result = await call_next(context)
        except NotFoundError:
            result = _unknown_tool_error(tool_name)
        except ValidationError as error:
            result = _invalid_tool_arguments_error(tool_name=tool_name, error=error)
        except Exception:
            duration_seconds = round(perf_counter() - started_at, 3)
            await agent_call_audit_service.complete_tool_call(
                agent_call_pk=agent_call_pk,
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
            tool_name=tool_name,
            duration_seconds=duration_seconds,
            success=not tool_error,
            error_code="mcp_tool_error" if tool_error else None,
        )
        return result
