"""Integration tests for MCP audit middleware database writes."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import mcp.types as mt
import pytest
from fastmcp.server.auth import AccessToken
from fastmcp.server.middleware import CallNext, MiddlewareContext
from fastmcp.tools.base import ToolResult
from pytest_mock import MockerFixture

from database.models import AgentCall
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
        claims={"sub": "codex-subject", "client_type": "codex"},
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


def test_prepare_collect_logs_session_id_is_mandatory_for_collect_logs() -> None:
    """Verify collect_logs always gets an effective session id from middleware."""

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
async def test_audit_middleware_returns_agent_error_when_agent_call_create_fails(
    mocker: MockerFixture,
) -> None:
    """Verify collect_logs is not executed when AgentCall audit setup fails."""

    token = AccessToken(
        token="test-token",
        client_id="workflow-client",
        scopes=["logs:collect"],
        claims={"sub": "workflow-subject", "client_type": "workflow_agent"},
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
            arguments={"workspace": "workflow", "project_names": ["landingpage"]},
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
