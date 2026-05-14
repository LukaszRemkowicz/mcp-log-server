from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import mcp.types as mt
import pytest

from auth.scopes import SESSION_CLOSE_SCOPE
from database.models import AgentCall
from database.schemas import AgentCallCreate
from database.services.agent_calls import AgentCallService
from tests.conftest import CustomAccessToken
from tools.sessions import close_agent_session


async def _create_session_call(
    *,
    session_id: UUID,
    client_id: str = "codex-agent",
    session_ended: bool = False,
) -> None:
    await AgentCallService().create(
        AgentCallCreate(
            session_id=session_id,
            workspace="session",
            event="mcp_call_tool",
            client_id=client_id,
            client_type="codex",
            tool_name="collect_logs",
            session_ended=session_ended,
            arguments={"session_id": str(session_id)},
        )
    )


@pytest.mark.anyio
async def test_close_agent_session_marks_existing_session_closed(
    custom_access_token: CustomAccessToken,
) -> None:
    session_id = uuid4()
    await _create_session_call(session_id=session_id)
    token = custom_access_token(
        "codex-agent",
        [SESSION_CLOSE_SCOPE],
        "codex-agent",
        {"client_type": "codex"},
    )

    result = await close_agent_session(str(session_id), access_token=token)

    assert result.structured_content == {
        "action": "close_agent_session",
        "session_id": str(session_id),
        "status": "closed",
        "message": "Agent session was closed.",
    }
    rows = await AgentCall.objects.filter(session_id=session_id).order_by("created_at")
    assert len(rows) == 2
    assert rows[-1].session_ended is True
    assert rows[-1].tool_name == "close_agent_session"


@pytest.mark.anyio
async def test_close_agent_session_returns_stable_response_when_already_closed(
    custom_access_token: CustomAccessToken,
) -> None:
    session_id = uuid4()
    await _create_session_call(session_id=session_id, session_ended=True)
    token = custom_access_token(
        "codex-agent",
        [SESSION_CLOSE_SCOPE],
        "codex-agent",
        {"client_type": "codex"},
    )

    result = await close_agent_session(str(session_id), access_token=token)

    assert result.structured_content == {
        "action": "close_agent_session",
        "session_id": str(session_id),
        "status": "already_closed",
        "message": "Agent session was already closed.",
    }
    rows = await AgentCall.objects.filter(session_id=session_id)
    assert len(rows) == 1


@pytest.mark.anyio
async def test_close_agent_session_rejects_unknown_session(
    custom_access_token: CustomAccessToken,
) -> None:
    session_id = uuid4()
    token = custom_access_token(
        "codex-agent",
        [SESSION_CLOSE_SCOPE],
        "codex-agent",
        {"client_type": "codex"},
    )

    result = await close_agent_session(str(session_id), access_token=token)
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "session_not_found"
    assert mcp_result.structuredContent["details"] == {"session_id": str(session_id)}


@pytest.mark.anyio
async def test_close_agent_session_rejects_other_client_session(
    custom_access_token: CustomAccessToken,
) -> None:
    session_id = uuid4()
    await _create_session_call(session_id=session_id, client_id="other-agent")
    token = custom_access_token(
        "codex-agent",
        [SESSION_CLOSE_SCOPE],
        "codex-agent",
        {"client_type": "codex"},
    )

    result = await close_agent_session(str(session_id), access_token=token)
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "session_not_found"


@pytest.mark.anyio
async def test_close_agent_session_rejects_workflow_agent(
    custom_access_token: CustomAccessToken,
) -> None:
    session_id = uuid4()
    await _create_session_call(session_id=session_id, client_id="workflow-agent")
    token = custom_access_token(
        "workflow-agent",
        [SESSION_CLOSE_SCOPE],
        "workflow-agent",
        {"client_type": "workflow_agent"},
    )

    result = await close_agent_session(str(session_id), access_token=token)
    mcp_result = cast(mt.CallToolResult, result.to_mcp_result())

    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "workflow_agent_session_close_forbidden"
