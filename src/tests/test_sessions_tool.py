from __future__ import annotations

from inspect import signature
from typing import cast

import mcp.types as mt
import pytest

from auth.mcp_caller_context import AuthenticatedMcpCaller
from core.types import LogWorkspace
from database.models import AgentCall, AgentSession
from database.schemas import AgentSessionOut
from database.types import AgentSessionStatus
from services.agent_sessions import AgentSessionService
from tests.factories import AgentSessionFactory, McpCallerFactory
from tools.sessions import close_agent_session


async def _create_agent_session(
    *,
    session_id: str,
    client_id: str = "codex-agent",
    client_type: str = "codex",
    status: AgentSessionStatus = AgentSessionStatus.ACTIVE,
) -> AuthenticatedMcpCaller:
    caller_kwargs = (
        {}
        if client_id == "codex-agent"
        else {
            "client_id": client_id,
            "client_type": client_type,
            "workspace": LogWorkspace.SESSION,
            "allowed_projects": ["landingpage"],
        }
    )
    caller = await McpCallerFactory.save_to_db(**caller_kwargs)
    await AgentSessionFactory.save_to_db(
        name=session_id,
        caller=caller,
        status=status,
    )
    return _caller(client_id=caller.client_id, client_type=caller.client_type, caller_id=caller.id)


def _caller(
    *,
    client_id: str = "codex-agent",
    client_type: str = "codex",
    caller_id: int = 1,
) -> AuthenticatedMcpCaller:
    return AuthenticatedMcpCaller(
        client_id=client_id,
        client_type=client_type,
        workspace=LogWorkspace.SESSION,
        allowed_projects=frozenset({"landingpage"}),
        caller_id=caller_id,
    )


def test_agent_session_service_does_not_accept_caller_context() -> None:
    """Keep caller authorization decisions in the MCP tool entrypoint."""

    assert "caller" not in signature(AgentSessionService.close_session).parameters
    assert "caller" not in signature(AgentSessionService.load_session).parameters


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_agent_session_service_returns_session_state_not_agent_call_objects() -> None:
    """Keep ORM AgentCall objects hidden behind session facts."""

    session_id = "humble-river-finds-f1e2"
    caller = await _create_agent_session(session_id=session_id)

    state = await AgentSessionService().load_session(name=session_id)

    assert isinstance(state, AgentSessionOut)
    assert state.model_dump() == {
        "id": state.id,
        "name": session_id,
        "caller_id": caller.caller_id,
        "status": AgentSessionStatus.ACTIVE,
        "closed_at": None,
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_close_agent_session_marks_existing_session_closed(
    mocker,
) -> None:
    session_id = "gentle-river-finds-a8f2"
    caller = await _create_agent_session(session_id=session_id)
    mocker.patch("tools.sessions.get_request_mcp_caller", return_value=caller)

    result = await close_agent_session(session_id)

    assert result.structured_content == {
        "action": "close_agent_session",
        "session_id": session_id,
        "status": "closed",
        "message": "Agent session was closed.",
    }
    rows = await AgentCall.objects.filter(session__name=session_id).order_by("created_at")
    assert len(rows) == 1
    assert rows[-1].tool_name == "close_agent_session"
    agent_session = await AgentSession.objects.get(name=session_id)
    assert agent_session.status == AgentSessionStatus.CLOSED


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_close_agent_session_returns_stable_response_when_already_closed(
    mocker,
) -> None:
    session_id = "quiet-field-opens-b1c2"
    caller = await _create_agent_session(session_id=session_id, status=AgentSessionStatus.CLOSED)
    mocker.patch("tools.sessions.get_request_mcp_caller", return_value=caller)

    result = await close_agent_session(session_id)

    assert result.structured_content == {
        "action": "close_agent_session",
        "session_id": session_id,
        "status": "already_closed",
        "message": "Agent session was already closed.",
    }
    rows = await AgentCall.objects.filter(session__name=session_id)
    assert len(rows) == 0


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_close_agent_session_rejects_unknown_session(
    mocker,
) -> None:
    session_id = "silent-window-waits-c3d4"
    caller = _caller()
    mocker.patch("tools.sessions.get_request_mcp_caller", return_value=caller)

    result = await close_agent_session(session_id)
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "session_not_found"
    assert mcp_result.structuredContent["details"] == {"session_id": session_id}


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_close_agent_session_rejects_other_client_session(
    mocker,
) -> None:
    session_id = "bright-river-opens-d4e5"
    await _create_agent_session(session_id=session_id, client_id="other-agent")
    caller = _caller(caller_id=999)
    mocker.patch("tools.sessions.get_request_mcp_caller", return_value=caller)

    result = await close_agent_session(session_id)
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "session_not_found"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_close_agent_session_rejects_workflow_agent(
    mocker,
) -> None:
    session_id = "calm-sky-finds-e5f6"
    caller = await _create_agent_session(
        session_id=session_id,
        client_id="workflow-agent",
        client_type="workflow_agent",
    )
    mocker.patch(
        "tools.sessions.get_request_mcp_caller",
        return_value=caller,
    )

    result = await close_agent_session(session_id)
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "workflow_agent_session_close_forbidden"
