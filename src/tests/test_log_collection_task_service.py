from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.types import LogWorkspace
from database.types import TaskStatus, TaskType
from services.log_collection_tasks import LogCollectionTaskService
from tests.factories import McpCallerFactory, TaskFactory


@pytest.mark.anyio
async def test_log_collection_task_service_returns_status_for_owned_session(
    db: None,  # noqa: ARG001
) -> None:
    caller = await McpCallerFactory.save_to_db(
        client_id="status-owner",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["all"],
    )
    other_caller = await McpCallerFactory.save_to_db(
        client_id="status-other",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["all"],
    )
    await TaskFactory.save_to_db(
        caller=caller,
        task_type=TaskType.LOG_COLLECTION,
        status=TaskStatus.COMPLETED,
        workspace=LogWorkspace.SESSION,
        session_id="public-session",
        project_name="mcp",
        result={
            "project_name": "mcp",
            "error": None,
            "sources": [{"source_key": "app", "error": None, "line_count": 3}],
        },
        started_at=datetime(2026, 7, 6, 8, 0, tzinfo=UTC),
        completed_at=datetime(2026, 7, 6, 8, 1, tzinfo=UTC),
    )
    await TaskFactory.save_to_db(
        caller=other_caller,
        task_type=TaskType.LOG_COLLECTION,
        status=TaskStatus.FAILED,
        workspace=LogWorkspace.SESSION,
        session_id="public-session",
        project_name="landingpage",
        error_code="RuntimeError",
        error_message="not visible",
    )

    payload = await LogCollectionTaskService().get_status(
        caller_id=caller.id,
        session_id="public-session",
    )

    assert payload is not None
    assert payload["action"] == "get_log_collection_status"
    assert payload["task_type"] == "log_collection"
    assert "status" not in payload.model_dump(mode="json")
    assert payload["workspace"] == "session"
    assert payload["session_id"] == "public-session"
    assert payload["task_count"] == 1
    assert len(payload.tasks) == 1
    assert payload.tasks[0].project_name == "mcp"
    assert payload.tasks[0].status == TaskStatus.COMPLETED
    assert payload.tasks[0].result == {
        "project_name": "mcp",
        "sources": [{"source_key": "app", "line_count": 3}],
    }
    assert payload.tasks[0].error_code is None
    serialized = payload.model_dump(mode="json", exclude_none=True)
    assert "error_code" not in serialized["tasks"][0]
    assert "error_message" not in serialized["tasks"][0]
    assert "error" not in serialized["tasks"][0]["result"]
    assert "error" not in serialized["tasks"][0]["result"]["sources"][0]


@pytest.mark.anyio
async def test_log_collection_task_service_returns_none_for_unknown_session(
    db: None,  # noqa: ARG001
) -> None:
    caller = await McpCallerFactory.save_to_db(
        client_id="status-owner-missing",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["all"],
    )

    payload = await LogCollectionTaskService().get_status(
        caller_id=caller.id,
        session_id="missing-session",
    )

    assert payload is None
