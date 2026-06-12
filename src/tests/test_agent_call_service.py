"""Tests for AgentCall application service logic."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from tortoise.exceptions import OperationalError

from database.models import McpCaller
from database.schemas import AgentCallCreate, AgentCallUpdate
from services.agent_calls import (
    AGENT_CALL_UNAVAILABLE_RETRY_TIP,
    AgentCallAuditService,
    AgentCallCreateError,
)
from tests.factories import AgentSessionFactory


def test_agent_session_factory_builds_caller_chain() -> None:
    session = AgentSessionFactory.build()
    caller = session.caller

    assert isinstance(caller, McpCaller)
    assert caller.id is not None
    assert caller.client_id.startswith("test-client-")
    assert caller._saved_in_db is True


def test_agent_session_factory_instance_builds_caller_chain() -> None:
    session = AgentSessionFactory()
    caller = session.caller

    assert isinstance(caller, McpCaller)
    assert caller.id is not None
    assert caller.client_id.startswith("test-client-")
    assert caller._saved_in_db is True


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_agent_session_factory_can_save_to_db() -> None:
    session = await AgentSessionFactory.save_to_db()

    assert session.id is not None
    assert session.caller.id is not None
    assert session.caller.client_id.startswith("test-client-")
    assert session._saved_in_db is True
    assert session.caller._saved_in_db is True


@pytest.mark.anyio
async def test_agent_call_service_creates_tool_call_payload(mocker: MockerFixture) -> None:
    session = AgentSessionFactory.build()
    row = mocker.Mock()
    row.id = uuid4()
    db_service = mocker.Mock()
    db_service.create = mocker.AsyncMock(return_value=row)
    service = AgentCallAuditService(db_service=db_service)

    created_id = await service.create_tool_call(
        session=session,
        event="mcp_call_tool",
        tool_name="collect_logs",
        arguments={
            "session_id": session.name,
            "project_names": ["landingpage"],
            "source_keys": ["backend"],
        },
    )

    payload = db_service.create.call_args.args[0]
    assert created_id == row.id
    assert isinstance(payload, AgentCallCreate)
    assert payload.session_id == session.id
    assert payload.event == "mcp_call_tool"
    assert payload.caller == session.caller.id
    assert payload.tool_name == "collect_logs"
    assert payload.project_name == "landingpage"
    assert payload.source_keys == ["backend"]
    assert payload.arguments == {
        "session_id": session.name,
        "project_names": ["landingpage"],
        "source_keys": ["backend"],
    }


@pytest.mark.anyio
async def test_agent_call_service_creates_analysis_tool_call_payload(
    mocker: MockerFixture,
) -> None:
    """Verify audit rows capture single-project analysis tool arguments."""

    session = AgentSessionFactory.build()
    row = mocker.Mock()
    row.id = uuid4()
    db_service = mocker.Mock()
    db_service.create = mocker.AsyncMock(return_value=row)
    service = AgentCallAuditService(db_service=db_service)

    created_id = await service.create_tool_call(
        session=session,
        event="mcp_call_tool",
        tool_name="group_errors",
        arguments={
            "session_id": session.name,
            "project_name": "landingpage",
            "source_keys": ["backend"],
        },
    )

    payload = db_service.create.call_args.args[0]
    assert created_id == row.id
    assert isinstance(payload, AgentCallCreate)
    assert payload.tool_name == "group_errors"
    assert payload.project_name == "landingpage"
    assert payload.source_keys == ["backend"]
    assert payload.arguments == {
        "session_id": session.name,
        "project_name": "landingpage",
        "source_keys": ["backend"],
    }


@pytest.mark.anyio
async def test_agent_call_service_completes_tool_call(mocker: MockerFixture) -> None:
    agent_call_pk = uuid4()
    db_service = mocker.Mock()
    db_service.update = mocker.AsyncMock()
    service = AgentCallAuditService(db_service=db_service)

    await service.complete_tool_call(
        agent_call_pk=agent_call_pk,
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
    session = AgentSessionFactory.build()

    result = await service.create_tool_call(
        session=session,
        event="mcp_call_tool",
        tool_name="collect_logs",
        arguments=None,
    )

    assert isinstance(result, AgentCallCreateError)
    assert result.error_code == "agent_call_unavailable"
    assert result.message == "collect_logs is temporarily unavailable."
    assert result.retry_tips == [AGENT_CALL_UNAVAILABLE_RETRY_TIP]
    assert result.details["tool_name"] == "collect_logs"
    assert result.details["session_id"] == session.name


@pytest.mark.anyio
async def test_agent_call_service_ignores_missing_row_on_complete(
    mocker: MockerFixture,
) -> None:
    db_service = mocker.Mock()
    db_service.update = mocker.AsyncMock()
    debug_spy = mocker.patch("services.agent_calls.logger.debug")
    service = AgentCallAuditService(db_service=db_service)

    await service.complete_tool_call(
        agent_call_pk=None,
        tool_name="collect_logs",
        duration_seconds=12.5,
        success=True,
        error_code=None,
    )

    debug_spy.assert_called_once()
    db_service.update.assert_not_called()
