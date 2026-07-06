from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.types import LogWorkspace
from database.services.tasks import TaskService
from database.types import TaskStatus, TaskType
from tests.factories import McpCallerFactory, TaskFactory


@pytest.mark.anyio
async def test_task_service_gets_session_tasks_for_caller_public_session(
    db: None,  # noqa: ARG001
) -> None:
    caller = await McpCallerFactory.save_to_db(
        client_id="task-service-owner",
        client_type="codex",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["all"],
    )
    other_caller = await McpCallerFactory.save_to_db(
        client_id="task-service-other",
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
        result={"ok": True},
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
    )

    task_list = await TaskService().get_session_tasks(
        task_type=TaskType.LOG_COLLECTION,
        caller_id=caller.id,
        session_id="public-session",
    )

    tasks = task_list.tasks
    assert len(tasks) == 1
    assert tasks[0].caller_id == caller.id
    assert tasks[0].session_id == "public-session"
    assert tasks[0].project_name == "mcp"
    assert tasks[0].status == TaskStatus.COMPLETED
    assert tasks[0].result == {"ok": True}
