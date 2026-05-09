"""Tests for AgentCall application service logic."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastmcp.server.auth import AccessToken
from pytest_mock import MockerFixture
from tortoise.exceptions import OperationalError

from database.services.models import AgentCallCreate, AgentCallUpdate
from services.agent_calls import (
    AGENT_CALL_UNAVAILABLE_RETRY_TIP,
    AgentCallAuditService,
    AgentCallCreateError,
)


def _access_token() -> AccessToken:
    return AccessToken(
        token="test-token",
        client_id="workflow-client",
        scopes=["logs:collect"],
        claims={"sub": "workflow-subject", "client_type": "workflow_agent"},
    )


@pytest.mark.anyio
async def test_agent_call_service_creates_tool_call_payload(mocker: MockerFixture) -> None:
    session_id = uuid4()
    row = mocker.Mock()
    row.id = uuid4()
    db_service = mocker.Mock()
    db_service.create = mocker.AsyncMock(return_value=row)
    service = AgentCallAuditService(db_service=db_service)

    created_id = await service.create_tool_call(
        session_id=session_id,
        workspace="workflow",
        event="mcp_call_tool",
        token=_access_token(),
        tool_name="collect_logs",
        arguments={
            "session_id": str(session_id),
            "project_names": ["landingpage"],
            "source_keys": ["backend"],
        },
    )

    payload = db_service.create.call_args.args[0]
    assert created_id == row.id
    assert isinstance(payload, AgentCallCreate)
    assert payload.session_id == session_id
    assert payload.workspace == "workflow"
    assert payload.event == "mcp_call_tool"
    assert payload.client_id == "workflow-client"
    assert payload.client_type == "workflow_agent"
    assert payload.tool_name == "collect_logs"
    assert payload.project_name == "landingpage"
    assert payload.source_keys == ["backend"]
    assert payload.arguments == {
        "session_id": str(session_id),
        "project_names": ["landingpage"],
        "source_keys": ["backend"],
    }


@pytest.mark.anyio
async def test_agent_call_service_completes_tool_call(mocker: MockerFixture) -> None:
    agent_call_pk = uuid4()
    session_id = uuid4()
    db_service = mocker.Mock()
    db_service.update = mocker.AsyncMock()
    service = AgentCallAuditService(db_service=db_service)

    await service.complete_tool_call(
        agent_call_pk=agent_call_pk,
        session_id=session_id,
        tool_name="collect_logs",
        duration_seconds=12.5,
        success=False,
        error_code="mcp_tool_error",
    )

    payload = db_service.update.call_args.args[0]
    assert isinstance(payload, AgentCallUpdate)
    assert payload.pk == agent_call_pk
    assert payload.duration_seconds == 12.5
    assert payload.success is False
    assert payload.error_code == "mcp_tool_error"


@pytest.mark.anyio
async def test_agent_call_service_returns_error_model_when_create_fails(
    mocker: MockerFixture,
) -> None:
    db_service = mocker.Mock()
    db_service.create = mocker.AsyncMock(side_effect=OperationalError("db unavailable"))
    service = AgentCallAuditService(db_service=db_service)

    result = await service.create_tool_call(
        session_id=uuid4(),
        workspace="session",
        event="mcp_call_tool",
        token=_access_token(),
        tool_name="collect_logs",
        arguments=None,
    )

    assert isinstance(result, AgentCallCreateError)
    assert result.error_code == "agent_call_unavailable"
    assert result.message == "collect_logs is temporarily unavailable."
    assert result.retry_tips == [AGENT_CALL_UNAVAILABLE_RETRY_TIP]
    assert result.details["tool_name"] == "collect_logs"


@pytest.mark.anyio
async def test_agent_call_service_ignores_missing_row_on_complete(
    mocker: MockerFixture,
) -> None:
    db_service = mocker.Mock()
    db_service.update = mocker.AsyncMock()
    warning_spy = mocker.patch("services.agent_calls.logger.warning")
    service = AgentCallAuditService(db_service=db_service)

    await service.complete_tool_call(
        agent_call_pk=None,
        session_id=uuid4(),
        tool_name="collect_logs",
        duration_seconds=12.5,
        success=True,
        error_code=None,
    )

    warning_spy.assert_called_once()
    db_service.update.assert_not_called()
