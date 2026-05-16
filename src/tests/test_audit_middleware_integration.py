"""Integration tests for MCP audit middleware database writes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import mcp.types as mt
import pytest
from fastmcp.server.auth import AccessToken
from fastmcp.server.middleware import CallNext, MiddlewareContext
from fastmcp.tools.base import ToolResult
from pytest_mock import MockerFixture

from auth.mcp_caller_context import AuthenticatedMcpCaller
from cache import clear_cache
from core.types import LogWorkspace
from database.models import AgentCall, Authentication, ProjectManifest
from database.schemas import ProjectManifestUpdate
from database.services.project_manifests import ProjectManifestService
from middleware.audit import AccessAuditMiddleware, _prepare_collect_logs_session_id
from services.agent_calls import AGENT_CALL_UNAVAILABLE_RETRY_TIP, AgentCallCreateError


@pytest.mark.anyio
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
            "client_id": "codex-client",
            "client_type": "codex",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="collect_logs",
            arguments={
                "workspace": "session",
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
    session_id = UUID(structured_content["session_id"])
    rows = await AgentCall.objects.filter(session_id=session_id).order_by("created_at")

    assert len(rows) == 1
    assert rows[0].workspace == "session"
    assert rows[0].event == "mcp_call_tool"
    assert rows[0].tool_name == "collect_logs"
    assert rows[0].client_id == "codex-client"
    assert rows[0].client_type == "codex"
    assert rows[0].project_name == "landingpage"
    assert rows[0].source_keys == ["backend"]
    assert rows[0].arguments is not None
    assert rows[0].arguments["session_id"] == str(session_id)
    assert rows[0].success is True
    assert rows[0].duration_seconds is not None
    assert rows[0].duration_seconds < 1
    assert rows[0].error_code is None


def test_prepare_collect_logs_session_id_is_mandatory_for_session_collect_logs() -> None:
    """Verify session collect_logs gets an effective session id from middleware."""

    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="collect_logs",
            arguments={"workspace": "session", "session_id": None},
        )
    )

    session_id = _prepare_collect_logs_session_id(context)

    assert str(UUID(str(session_id))) == str(session_id)
    assert context.message.arguments is not None
    assert context.message.arguments["session_id"] == str(session_id)


def test_prepare_collect_logs_session_id_generates_for_workflow_collect_logs() -> None:
    """Verify workflow collect_logs also gets an effective session id."""

    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="collect_logs",
            arguments={"workspace": "workflow", "session_id": None},
        )
    )

    session_id = _prepare_collect_logs_session_id(context)

    assert str(UUID(str(session_id))) == str(session_id)
    assert context.message.arguments is not None
    assert context.message.arguments["session_id"] == str(session_id)


@pytest.mark.anyio
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
            "client_id": "workflow-client",
            "client_type": "workflow_agent",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    complete_tool_call = mocker.patch(
        "middleware.audit.agent_call_audit_service.complete_tool_call",
        new=mocker.AsyncMock(),
    )
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

    async def call_next(
        next_context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolResult:
        assert next_context.message.arguments is not None
        session_id = UUID(str(next_context.message.arguments["session_id"]))
        return ToolResult(
            content=[],
            structured_content={"workspace": "workflow", "session_id": str(session_id)},
        )

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    assert result.structured_content is not None
    session_id = UUID(result.structured_content["session_id"])
    assert result.structured_content == {"workspace": "workflow", "session_id": str(session_id)}
    complete_tool_call.assert_awaited_once()
    assert complete_tool_call.await_args.kwargs["agent_call_pk"] is not None


@pytest.mark.anyio
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
            "client_id": "codex-client",
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
            arguments={"workspace": "session", "project_names": ["landingpage"]},
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
async def test_audit_middleware_rejects_tool_call_without_access_token(
    mocker: MockerFixture,
) -> None:
    """Verify tool calls must have an authenticated access token."""

    mocker.patch("middleware.audit.get_access_token", return_value=None)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="close_agent_session",
            arguments={"session_id": "ef5e1daa-d06b-479c-926d-8107639bd467"},
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
            arguments={"session_id": "ef5e1daa-d06b-479c-926d-8107639bd467"},
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
async def test_audit_middleware_rejects_tool_call_for_unregistered_client(
    mocker: MockerFixture,
) -> None:
    """Verify JWT callers must be manually allowed in the Authentication table."""

    token = AccessToken(
        token="test-token",
        client_id="unknown-client",
        scopes=["sessions.close"],
        claims={
            "sub": "codex-subject",
            "client_id": "unknown-client",
            "client_type": "codex",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="close_agent_session",
            arguments={"session_id": "ef5e1daa-d06b-479c-926d-8107639bd467"},
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
            "client_id": "codex-client",
            "client_type": "workflow_agent",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="close_agent_session",
            arguments={"session_id": "ef5e1daa-d06b-479c-926d-8107639bd467"},
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
async def test_audit_middleware_rejects_tool_call_when_workspace_mismatches(
    mocker: MockerFixture,
) -> None:
    """Verify Authentication rows are scoped to workflow or session usage."""

    await Authentication.objects.create(
        client_id="workflow-only-client",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["landingpage"],
    )
    token = AccessToken(
        token="test-token",
        client_id="workflow-only-client",
        scopes=["sessions.close"],
        claims={
            "sub": "codex-subject",
            "client_id": "workflow-only-client",
            "client_type": "codex",
        },
    )
    mocker.patch("middleware.audit.get_access_token", return_value=token)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="close_agent_session",
            arguments={"session_id": "ef5e1daa-d06b-479c-926d-8107639bd467"},
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
async def test_audit_middleware_authorizes_container_health_as_session_tool(
    mocker: MockerFixture,
) -> None:
    """Verify container health diagnostics use the session caller allowlist."""

    await Authentication.objects.create(
        client_id="session-container-client",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["dockerpage"],
    )
    token = AccessToken(
        token="test-token",
        client_id="session-container-client",
        scopes=["container.files.read"],
        claims={
            "sub": "codex-subject",
            "client_id": "session-container-client",
            "client_type": "codex",
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
async def test_audit_middleware_sets_database_caller_on_request_state(
    mocker: MockerFixture,
) -> None:
    """Verify DB Authentication projects are attached to request state."""

    await Authentication.objects.create(
        client_id="project-override-client",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["landingpage"],
    )
    token = AccessToken(
        token="test-token",
        client_id="project-override-client",
        scopes=["logs.collect"],
        claims={
            "sub": "codex-subject",
            "client_id": "project-override-client",
            "client_type": "codex",
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
        assert caller.client_id == "project-override-client"
        assert caller.client_type == "codex"
        assert caller.workspace == LogWorkspace.WORKFLOW
        assert caller.allowed_projects == frozenset({"landingpage"})
        return ToolResult(content=[], structured_content={"ok": True})

    result = await middleware.on_call_tool(
        context,
        cast(CallNext[mt.CallToolRequestParams, ToolResult], call_next),
    )

    assert result.structured_content == {"ok": True}
    assert token.claims["allowed_projects"] == ["other-project"]


@pytest.mark.anyio
async def test_audit_middleware_expands_all_allowed_projects_from_manifests(
    mocker: MockerFixture,
) -> None:
    """Verify DB allowed_projects=['all'] becomes concrete manifest project names."""

    await Authentication.objects.create(
        client_id="all-project-client",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["all"],
    )
    token = AccessToken(
        token="test-token",
        client_id="all-project-client",
        scopes=["projects.read"],
        claims={
            "sub": "codex-subject",
            "client_id": "all-project-client",
            "client_type": "codex",
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
async def test_project_manifest_service_caches_all_manifest_rows() -> None:
    """Verify manifest listing reuses cached rows."""

    await clear_cache(ProjectManifestService.all)
    project_manifest_service = ProjectManifestService()
    await ProjectManifest.objects.create(
        project_key="cache-alpha",
        project_summary="Cache alpha.",
        static_asset_paths=[],
        static_asset_extensions=[],
        sources=[],
    )

    try:
        first_result = await project_manifest_service.all()
        await ProjectManifest.objects.create(
            project_key="cache-beta",
            project_summary="Cache beta.",
            static_asset_paths=[],
            static_asset_extensions=[],
            sources=[],
        )
        second_result = await project_manifest_service.all()
        first_project_keys = {row.project_key for row in first_result}
        second_project_keys = {row.project_key for row in second_result}

        assert "cache-alpha" in first_project_keys
        assert "cache-beta" not in first_project_keys
        assert second_result == first_result
        assert second_project_keys == first_project_keys
    finally:
        await clear_cache(ProjectManifestService.all)


@pytest.mark.anyio
async def test_project_manifest_service_caches_one_manifest_row() -> None:
    """Verify single manifest lookups reuse cached rows."""

    await clear_cache(ProjectManifestService.get)
    project_manifest_service = ProjectManifestService()
    row = await ProjectManifest.objects.create(
        project_key="cache-one",
        project_summary="Original summary.",
        static_asset_paths=[],
        static_asset_extensions=[],
        sources=[],
    )

    try:
        first_result = await project_manifest_service.get("cache-one")
        row.project_summary = "Changed summary."
        await row.save(update_fields=["project_summary", "updated_at"])

        second_result = await project_manifest_service.get("cache-one")

        assert first_result.project_summary == "Original summary."
        assert second_result.project_summary == "Original summary."
        assert second_result == first_result
    finally:
        await clear_cache(ProjectManifestService.get)


@pytest.mark.anyio
async def test_project_manifest_service_cache_is_clearable() -> None:
    """Verify manifest writes can clear the cached manifest listing."""

    await clear_cache(ProjectManifestService.all)
    project_manifest_service = ProjectManifestService()
    await ProjectManifest.objects.create(
        project_key="clear-cache-alpha",
        project_summary="Clear cache alpha.",
        static_asset_paths=[],
        static_asset_extensions=[],
        sources=[],
    )

    try:
        first_result = await project_manifest_service.all()
        await ProjectManifest.objects.create(
            project_key="clear-cache-beta",
            project_summary="Clear cache beta.",
            static_asset_paths=[],
            static_asset_extensions=[],
            sources=[],
        )
        second_result = await project_manifest_service.all()
        first_project_keys = {row.project_key for row in first_result}
        second_project_keys = {row.project_key for row in second_result}

        assert "clear-cache-alpha" in first_project_keys
        assert "clear-cache-beta" not in second_project_keys
        assert second_result == first_result

        await clear_cache(ProjectManifestService.all)

        refreshed_result = await project_manifest_service.all()
        refreshed_project_keys = {row.project_key for row in refreshed_result}
        assert "clear-cache-beta" in refreshed_project_keys
    finally:
        await clear_cache(ProjectManifestService.all)


@pytest.mark.anyio
async def test_project_manifest_service_update_clears_single_manifest_cache() -> None:
    """Verify service updates refresh cached single-manifest lookups."""

    await clear_cache(ProjectManifestService.get)
    project_manifest_service = ProjectManifestService()
    row = await ProjectManifest.objects.create(
        project_key="clear-one",
        project_summary="Original summary.",
        static_asset_paths=[],
        static_asset_extensions=[],
        sources=[],
    )

    try:
        first_result = await project_manifest_service.get("clear-one")

        await project_manifest_service.update(
            ProjectManifestUpdate(
                pk=row.id,
                project_summary="Updated summary.",
                static_asset_paths=[],
                static_asset_extensions=[],
                sources=[],
            )
        )
        second_result = await project_manifest_service.get("clear-one")

        assert first_result.project_summary == "Original summary."
        assert second_result.project_summary == "Updated summary."
    finally:
        await clear_cache(ProjectManifestService.get)
