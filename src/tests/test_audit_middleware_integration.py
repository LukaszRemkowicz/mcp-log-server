"""Integration tests for MCP audit middleware database writes."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, cast

import mcp.types as mt
import pytest
from fastmcp.server.auth import AccessToken
from fastmcp.server.middleware import CallNext, MiddlewareContext
from fastmcp.tools.base import ToolResult
from pytest_mock import MockerFixture

from auth.mcp_caller_context import AuthenticatedMcpCaller
from core.types import LogWorkspace
from database.models import AgentCall, AgentSession, McpCaller, ProjectManifest
from database.schemas import ProjectManifestUpdate
from database.services.project_manifests import ProjectManifestService
from middleware.audit import AccessAuditMiddleware, _prepare_collect_logs_arguments
from services.agent_calls import AGENT_CALL_UNAVAILABLE_RETRY_TIP, AgentCallCreateError
from services.session_ids import generate_session_id
from tests.factories import McpCallerFactory, ProjectManifestFactory

SESSION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}-[a-f0-9]{4}$")


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_persists_agent_call_for_collect_logs(
    mocker: MockerFixture,
) -> None:
    """Verify middleware creates and completes one AgentCall row."""

    token = AccessToken(
        token="test-token",
        client_id="codex-client",
        scopes=["logs:collect"],
        claims={
            "sub": "codex-subject",
            "client_type": "codex",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    request = SimpleNamespace(state=SimpleNamespace())
    mocker.patch("middleware.audit.get_http_request", return_value=request)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="collect_logs",
            arguments={
                "project_names": ["landingpage"],
                "source_keys": ["backend"],
            },
        )
    )
    middleware = AccessAuditMiddleware()

    async def call_next(
        next_context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolResult:
        arguments: dict[str, Any] = next_context.message.arguments or {}
        return ToolResult(
            content=[],
            structured_content={"session_id": arguments["session_id"]},
        )

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )
    structured_content = result.structured_content
    assert structured_content is not None
    session_id = structured_content["session_id"]
    rows = await AgentCall.objects.filter(session__name=session_id).order_by("created_at")
    agent_session = await AgentSession.objects.get(name=session_id)
    caller = await McpCaller.objects.get(
        client_id="codex-client",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
    )

    assert len(rows) == 1
    assert agent_session.name == session_id
    assert getattr(agent_session, "caller_id") == caller.id
    assert not hasattr(request.state, "agent_session")
    assert rows[0].event == "mcp_call_tool"
    assert rows[0].tool_name == "collect_logs"
    assert getattr(rows[0], "caller_id") == caller.id
    assert rows[0].project_name == "landingpage"
    assert rows[0].source_keys == ["backend"]
    assert rows[0].arguments is not None
    assert rows[0].arguments["session_id"] == session_id
    assert rows[0].success is True
    assert rows[0].duration_seconds is not None
    assert rows[0].duration_seconds < 1
    assert rows[0].error_code is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_persists_agent_call_for_session_analysis_tool(
    mocker: MockerFixture,
) -> None:
    """Verify session analysis tools get AgentCall timing rows."""

    token = AccessToken(
        token="test-token",
        client_id="codex-client",
        scopes=["logs:collect"],
        claims={
            "sub": "codex-subject",
            "client_type": "codex",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    request = SimpleNamespace(state=SimpleNamespace())
    mocker.patch("middleware.audit.get_http_request", return_value=request)
    caller = await McpCaller.objects.get(
        client_id="codex-client",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
    )
    session = await AgentSession.objects.create(
        name=generate_session_id(),
        caller=caller,
    )
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="group_errors",
            arguments={
                "project_name": "landingpage",
                "session_id": session.name,
                "source_keys": ["backend"],
            },
        )
    )
    middleware = AccessAuditMiddleware()

    async def call_next(
        next_context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolResult:
        return ToolResult(
            content=[],
            structured_content={
                "action": next_context.message.name,
                "session_id": session.name,
            },
        )

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    rows = await AgentCall.objects.filter(session__name=session.name).order_by("created_at")
    assert result.structured_content == {
        "action": "group_errors",
        "session_id": session.name,
    }
    assert len(rows) == 1
    assert rows[0].tool_name == "group_errors"
    assert rows[0].project_name == "landingpage"
    assert rows[0].source_keys == ["backend"]
    assert rows[0].arguments == {
        "project_name": "landingpage",
        "session_id": session.name,
        "source_keys": ["backend"],
    }
    assert rows[0].success is True
    assert rows[0].duration_seconds is not None
    assert rows[0].error_code is None


@pytest.mark.anyio
async def test_audit_middleware_rejects_collect_logs_workspace_argument(
    mocker: MockerFixture,
) -> None:
    """Verify collect_logs does not accept caller-supplied workspace."""

    token = AccessToken(
        token="test-token",
        client_id="workflow-client",
        scopes=["logs:collect"],
        claims={
            "sub": "workflow-subject",
            "client_type": "workflow_agent",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="collect_logs",
            arguments={
                "workspace": "workflow",
                "project_names": ["landingpage"],
                "source_keys": ["backend"],
            },
        )
    )
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock()

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    call_next.assert_not_awaited()
    assert result.structured_content is not None
    assert result.structured_content["error_code"] == "invalid_tool_arguments"
    assert result.structured_content["details"]["invalid_arguments"] == ["workspace"]


def test_prepare_collect_logs_arguments_injects_session_workspace_and_session_id() -> None:
    """Verify session collect_logs gets caller-owned workspace and session id."""

    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="collect_logs",
            arguments={"workspace": "workflow", "session_id": None},
        )
    )

    session_id = _prepare_collect_logs_arguments(context, workspace=LogWorkspace.SESSION)

    assert SESSION_ID_PATTERN.fullmatch(session_id)
    assert len(session_id) <= 24
    assert context.message.arguments is not None
    assert context.message.arguments["workspace"] == LogWorkspace.SESSION
    assert context.message.arguments["session_id"] == str(session_id)


def test_prepare_collect_logs_arguments_injects_workflow_workspace_and_session_id() -> None:
    """Verify workflow collect_logs gets caller-owned workspace and session id."""

    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="collect_logs",
            arguments={"workspace": "session", "session_id": None},
        )
    )

    session_id = _prepare_collect_logs_arguments(context, workspace=LogWorkspace.WORKFLOW)

    assert SESSION_ID_PATTERN.fullmatch(session_id)
    assert len(session_id) <= 24
    assert context.message.arguments is not None
    assert context.message.arguments["workspace"] == LogWorkspace.WORKFLOW
    assert context.message.arguments["session_id"] == str(session_id)


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_completes_workflow_collect_logs_with_generated_session(
    mocker: MockerFixture,
) -> None:
    """Verify workflow collect_logs gets an audit row with generated session id."""

    token = AccessToken(
        token="test-token",
        client_id="workflow-client",
        scopes=["logs:collect"],
        claims={
            "sub": "codex-subject",
            "client_type": "workflow_agent",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    request = SimpleNamespace(state=SimpleNamespace())
    mocker.patch("middleware.audit.get_http_request", return_value=request)
    complete_tool_call = mocker.patch(
        "middleware.audit.agent_call_audit_service.complete_tool_call",
        new=mocker.AsyncMock(),
    )
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="collect_logs",
            arguments={
                "project_names": ["landingpage"],
                "source_keys": ["backend"],
            },
        )
    )
    middleware = AccessAuditMiddleware()

    async def call_next(
        next_context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolResult:
        assert next_context.message.arguments is not None
        assert next_context.message.arguments["workspace"] == LogWorkspace.WORKFLOW
        generated_session_id = str(next_context.message.arguments["session_id"])
        return ToolResult(
            content=[],
            structured_content={"workspace": "workflow", "session_id": generated_session_id},
        )

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    assert result.structured_content is not None
    session_id = result.structured_content["session_id"]
    assert SESSION_ID_PATTERN.fullmatch(session_id)
    assert len(session_id) <= 24
    assert result.structured_content == {"workspace": "workflow", "session_id": session_id}
    agent_session = await AgentSession.objects.get(name=session_id)
    caller = await McpCaller.objects.get(
        client_id="workflow-client",
        client_type="workflow_agent",
        workspace=LogWorkspace.WORKFLOW,
    )
    assert getattr(agent_session, "caller_id") == caller.id
    assert not hasattr(request.state, "agent_session")
    complete_tool_call.assert_awaited_once()
    assert complete_tool_call.await_args.kwargs["agent_call_pk"] is not None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_returns_agent_error_when_agent_call_create_fails(
    mocker: MockerFixture,
) -> None:
    """Verify collect_logs is not executed when AgentCall audit setup fails."""

    token = AccessToken(
        token="test-token",
        client_id="codex-client",
        scopes=["logs:collect"],
        claims={
            "sub": "codex-subject",
            "client_type": "codex",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    mocker.patch(
        "middleware.audit.agent_call_audit_service.create_tool_call",
        new=mocker.AsyncMock(
            return_value=AgentCallCreateError(details={"tool_name": "collect_logs"})
        ),
    )
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="collect_logs",
            arguments={"project_names": ["landingpage"]},
        )
    )
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock()

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    call_next.assert_not_called()
    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "agent_call_unavailable"
    assert mcp_result.structuredContent["message"] == "collect_logs is temporarily unavailable."
    assert mcp_result.structuredContent["retry_tips"] == [AGENT_CALL_UNAVAILABLE_RETRY_TIP]


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_rejects_tool_call_without_access_token(
    mocker: MockerFixture,
) -> None:
    """Verify tool calls must have an authenticated access token."""

    mocker.patch("middleware.audit.get_access_token", return_value=None)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="close_agent_session",
            arguments={"session_id": generate_session_id()},
        )
    )
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock()

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    call_next.assert_not_called()
    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "missing_access_token"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_rejects_tool_call_without_client_id(
    mocker: MockerFixture,
) -> None:
    """Verify authenticated tool calls require a stable JWT client_id."""

    token = AccessToken(
        token="test-token",
        client_id="",
        scopes=["logs.collect"],
        claims={"sub": "codex-subject", "client_type": "codex"},
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="close_agent_session",
            arguments={"session_id": generate_session_id()},
        )
    )
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock()

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    call_next.assert_not_called()
    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "invalid_client_id"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_rejects_tool_call_for_unregistered_client(
    mocker: MockerFixture,
) -> None:
    """Verify JWT callers must be manually allowed in the McpCaller table."""

    token = AccessToken(
        token="test-token",
        client_id="unknown-client",
        scopes=["sessions.close"],
        claims={
            "sub": "codex-subject",
            "client_type": "codex",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="close_agent_session",
            arguments={"session_id": generate_session_id()},
        )
    )
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock()

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    call_next.assert_not_called()
    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "mcp_client_not_authorized"
    assert mcp_result.structuredContent["details"] == {
        "client_id": "unknown-client",
        "client_type": "codex",
        "workspace": "session",
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_rejects_tool_call_when_client_type_mismatches(
    mocker: MockerFixture,
) -> None:
    """Verify allowlist checks require both client_id and client_type."""

    token = AccessToken(
        token="test-token",
        client_id="codex-client",
        scopes=["sessions.close"],
        claims={
            "sub": "codex-subject",
            "client_type": "workflow_agent",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="close_agent_session",
            arguments={"session_id": generate_session_id()},
        )
    )
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock()

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    call_next.assert_not_called()
    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "mcp_client_not_authorized"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_rejects_tool_call_when_workspace_mismatches(
    mocker: MockerFixture,
) -> None:
    """Verify McpCaller rows are scoped to workflow or session usage."""

    caller = await McpCallerFactory.save_to_db(
        client_id="workflow-only-client",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["landingpage"],
    )
    token = AccessToken(
        token="test-token",
        client_id=caller.client_id,
        scopes=["sessions.close"],
        claims={
            "sub": "codex-subject",
            "client_type": caller.client_type,
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="close_agent_session",
            arguments={"session_id": generate_session_id()},
        )
    )
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock()

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    call_next.assert_not_called()
    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "mcp_client_not_authorized"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_authorizes_container_health_as_session_tool(
    mocker: MockerFixture,
) -> None:
    """Verify container health diagnostics use the session caller allowlist."""

    caller = await McpCallerFactory.save_to_db(
        client_id="session-container-client",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["dockerpage"],
    )
    token = AccessToken(
        token="test-token",
        client_id=caller.client_id,
        scopes=["container.files.read"],
        claims={
            "sub": "codex-subject",
            "client_type": caller.client_type,
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="inspect_containers_health",
            arguments={"project_name": "dockerpage", "source_key": "backend"},
        )
    )
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock(
        return_value=ToolResult(content=[], structured_content={"ok": True})
    )

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    call_next.assert_awaited_once()
    assert result.structured_content == {"ok": True}


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_authorizes_container_file_tools_as_session_tools(
    mocker: MockerFixture,
) -> None:
    """Verify file/path container diagnostics use the session caller allowlist."""

    db_caller = await McpCallerFactory.save_to_db(
        client_id="session-container-file-client",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["dockerpage"],
    )
    token = AccessToken(
        token="test-token",
        client_id=db_caller.client_id,
        scopes=["container.files.read"],
        claims={
            "sub": "codex-subject",
            "client_type": db_caller.client_type,
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    request = SimpleNamespace(state=SimpleNamespace())
    mocker.patch("middleware.audit.get_http_request", return_value=request)
    middleware = AccessAuditMiddleware()

    for tool_name in ("stat_container_path", "read_container_file", "list_container_directory"):
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(
                name=tool_name,
                arguments={"project_name": "dockerpage", "source_key": "backend", "path": "/app"},
            )
        )
        call_next = mocker.AsyncMock(
            return_value=ToolResult(content=[], structured_content={"ok": tool_name})
        )

        result = await middleware.on_call_tool(
            context,
            cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
        )

        call_next.assert_awaited_once()
        request_caller = request.state.caller
        assert isinstance(request_caller, AuthenticatedMcpCaller)
        assert request_caller.client_id == db_caller.client_id
        assert request_caller.client_type == db_caller.client_type
        assert request_caller.workspace == db_caller.workspace
        assert result.structured_content == {"ok": tool_name}


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("tool_name", ["list_projects", "inspect_live_fail2ban_activity"])
async def test_audit_middleware_authorizes_workspace_agnostic_tools_for_session_callers(
    mocker: MockerFixture,
    tool_name: str,
) -> None:
    """Verify no-workspace utility tools use the caller's actual DB workspace."""

    caller = await McpCallerFactory.save_to_db(
        client_id="session-utility-client",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["landingpage"],
    )
    token = AccessToken(
        token="test-token",
        client_id=caller.client_id,
        scopes=["projects.read"],
        claims={
            "sub": "codex-subject",
            "client_type": caller.client_type,
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    request = SimpleNamespace(state=SimpleNamespace())
    mocker.patch("middleware.audit.get_http_request", return_value=request)
    context = MiddlewareContext(message=mt.CallToolRequestParams(name=tool_name, arguments={}))
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock(
        return_value=ToolResult(content=[], structured_content={"ok": True})
    )

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    call_next.assert_awaited_once()
    caller = request.state.caller
    assert isinstance(caller, AuthenticatedMcpCaller)
    assert caller.workspace == LogWorkspace.SESSION
    assert result.structured_content == {"ok": True}


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_list_projects_uses_session_caller_without_workflow_row(
    mocker: MockerFixture,
) -> None:
    """Verify list_projects does not require a workflow caller row for Codex."""

    await McpCaller.objects.filter(client_id="session-discovery-client").delete()
    await ProjectManifest.all().delete()
    caller = await McpCallerFactory.save_to_db(
        client_id="session-discovery-client",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["all"],
    )
    token = AccessToken(
        token="test-token",
        client_id=caller.client_id,
        scopes=["projects.read"],
        claims={
            "sub": "codex-subject",
            "client_type": caller.client_type,
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    request = SimpleNamespace(state=SimpleNamespace())
    mocker.patch("middleware.audit.get_http_request", return_value=request)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(name="list_projects", arguments={})
    )
    middleware = AccessAuditMiddleware()
    call_next = mocker.AsyncMock(
        return_value=ToolResult(content=[], structured_content={"result": []})
    )

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    call_next.assert_awaited_once()
    request_caller = request.state.caller
    assert isinstance(request_caller, AuthenticatedMcpCaller)
    assert request_caller.workspace == LogWorkspace.SESSION
    assert request_caller.allowed_projects == frozenset()
    assert result.structured_content == {"result": []}


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_sets_database_caller_on_request_state(
    mocker: MockerFixture,
) -> None:
    """Verify DB McpCaller projects are attached to request state."""

    db_caller = await McpCallerFactory.save_to_db(
        client_id="project-override-client",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["landingpage"],
    )
    token = AccessToken(
        token="test-token",
        client_id=db_caller.client_id,
        scopes=["logs.collect"],
        claims={
            "sub": "codex-subject",
            "client_type": db_caller.client_type,
            "allowed_projects": ["other-project"],
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    request = SimpleNamespace(state=SimpleNamespace())
    mocker.patch("middleware.audit.get_http_request", return_value=request)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="grep_log_snapshot",
            arguments={"project_name": "landingpage", "grep": "needle"},
        )
    )
    middleware = AccessAuditMiddleware()

    async def call_next(
        next_context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolResult:
        assert next_context.message.arguments is not None
        assert "caller" not in next_context.message.arguments
        caller = request.state.caller
        assert isinstance(caller, AuthenticatedMcpCaller)
        assert caller.client_id == db_caller.client_id
        assert caller.client_type == db_caller.client_type
        assert caller.workspace == db_caller.workspace
        assert caller.allowed_projects == frozenset({"landingpage"})
        return ToolResult(content=[], structured_content={"ok": True})

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    assert result.structured_content == {"ok": True}
    assert token.claims["allowed_projects"] == ["other-project"]


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_audit_middleware_expands_all_allowed_projects_from_manifests(
    mocker: MockerFixture,
) -> None:
    """Verify DB allowed_projects=['all'] becomes concrete manifest project names."""

    db_caller = await McpCallerFactory.save_to_db(
        client_id="all-project-client",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["all"],
    )
    token = AccessToken(
        token="test-token",
        client_id=db_caller.client_id,
        scopes=["projects.read"],
        claims={
            "sub": "codex-subject",
            "client_type": db_caller.client_type,
            "allowed_projects": ["other-project"],
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    request = SimpleNamespace(state=SimpleNamespace())
    mocker.patch("middleware.audit.get_http_request", return_value=request)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(name="list_projects", arguments={})
    )
    middleware = AccessAuditMiddleware()

    async def call_next(
        next_context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolResult:
        caller = request.state.caller
        assert isinstance(caller, AuthenticatedMcpCaller)
        assert "all" not in caller.allowed_projects
        assert caller.allowed_projects >= frozenset({"landingpage", "shop", "dockerpage"})
        return ToolResult(content=[], structured_content={"ok": True})

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    assert result.structured_content == {"ok": True}


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_project_manifest_service_all_returns_fresh_rows() -> None:
    """Verify manifest listings reflect rows created by separate service calls."""

    await ProjectManifest.clear_cache()
    project_manifest_service = ProjectManifestService()
    await ProjectManifestFactory.save_to_db(
        project_key="fresh-alpha",
        project_summary="Fresh alpha.",
    )

    try:
        first_result = await project_manifest_service.all()
        await ProjectManifestFactory.save_to_db(
            project_key="fresh-beta",
            project_summary="Fresh beta.",
        )
        second_result = await project_manifest_service.all()
        first_project_keys = {row.project_key for row in first_result}
        second_project_keys = {row.project_key for row in second_result}

        assert "fresh-alpha" in first_project_keys
        assert "fresh-beta" not in first_project_keys
        assert "fresh-beta" in second_project_keys
        assert second_result != first_result
    finally:
        await ProjectManifest.clear_cache()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_project_manifest_service_get_returns_fresh_row_after_save() -> None:
    """Verify single-manifest lookups reflect direct row saves."""

    project_manifest_service = ProjectManifestService()
    project_manifest = await ProjectManifestFactory.save_to_db()
    original_project_summary = project_manifest.project_summary

    try:
        first_result = await project_manifest_service.get(project_manifest.project_key)
        project_manifest.project_summary = "Changed summary."
        await project_manifest.save(update_fields=["project_summary", "updated_at"])

        second_result = await project_manifest_service.get(project_manifest.project_key)

        assert first_result.project_summary == original_project_summary
        assert second_result.project_summary == "Changed summary."
        assert second_result is not first_result
    finally:
        await ProjectManifest.clear_cache()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_project_manifest_service_all_does_not_require_cache_clear() -> None:
    """Verify manifest listings do not require process-local cache invalidation."""

    project_manifest_service = ProjectManifestService()
    first_project_manifest = await ProjectManifestFactory.save_to_db()

    try:
        first_result = await project_manifest_service.all()
        second_project_manifest = await ProjectManifestFactory.save_to_db()
        second_result = await project_manifest_service.all()
        first_project_keys = {row.project_key for row in first_result}
        second_project_keys = {row.project_key for row in second_result}

        assert first_project_manifest.project_key in first_project_keys
        assert second_project_manifest.project_key in second_project_keys
        assert second_result != first_result

        third_project_manifest = await ProjectManifestFactory.save_to_db()
        third_result = await project_manifest_service.all()
        third_project_keys = {row.project_key for row in third_result}
        assert third_project_manifest.project_key in third_project_keys
    finally:
        await ProjectManifest.clear_cache()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_project_manifest_service_update_refreshes_single_manifest_lookup() -> None:
    """Verify service updates are visible to later single-manifest lookups."""

    project_manifest_service = ProjectManifestService()
    project_manifest = await ProjectManifestFactory.save_to_db()
    original_project_summary = project_manifest.project_summary

    try:
        first_result = await project_manifest_service.get(project_manifest.project_key)

        await project_manifest_service.update(
            ProjectManifestUpdate(
                pk=project_manifest.id,
                project_summary="Updated summary.",
                static_asset_paths=[],
                static_asset_extensions=[],
                sources=[],
            )
        )
        second_result = await project_manifest_service.get(project_manifest.project_key)

        assert first_result.project_summary == original_project_summary
        assert second_result.project_summary == "Updated summary."
    finally:
        await ProjectManifest.clear_cache()
