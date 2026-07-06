from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from core.types import LogWorkspace
from database.models import McpCaller, Task
from database.types import TaskStatus, TaskType
from tests.factories import McpCallerFactory


@pytest.mark.anyio
async def test_task_model_persists_async_collection_status(db: None) -> None:  # noqa: ARG001
    caller = await McpCallerFactory.save_to_db(
        client_id="task-caller",
        client_type="workflow_agent",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["all"],
    )
    task = await Task.objects.create(
        task_type=TaskType.LOG_COLLECTION,
        status=TaskStatus.RUNNING,
        workspace=LogWorkspace.WORKFLOW,
        caller=caller,
        session_id="task-session",
        arguments={
            "project_names": ["agent-monitoring", "mcp"],
            "source_keys": ["all"],
            "since": "2026-07-04T00:00:00+02:00",
            "until": "2026-07-05T00:00:00+02:00",
        },
        started_at=datetime(2026, 7, 5, 0, 0, tzinfo=UTC),
    )

    task.status = TaskStatus.COMPLETED
    task.result = {
        "session_id": "daily-workflow",
        "project_count": 2,
    }
    task.completed_at = datetime(2026, 7, 5, 0, 2, 48, tzinfo=UTC)
    await task.save(update_fields=["status", "result", "completed_at", "updated_at"])

    saved = await Task.objects.get(id=task.id)
    saved_caller = await McpCaller.objects.get(id=caller.id)

    assert saved.task_type == TaskType.LOG_COLLECTION
    assert saved.status == TaskStatus.COMPLETED
    assert saved.workspace == LogWorkspace.WORKFLOW
    assert saved_caller.client_id == "task-caller"
    assert saved.session_id == "task-session"
    assert saved.arguments["project_names"] == ["agent-monitoring", "mcp"]
    assert saved.result == {"session_id": "daily-workflow", "project_count": 2}
    assert cast(Any, saved.error_code) is None
    assert saved.completed_at == datetime(2026, 7, 5, 0, 2, 48, tzinfo=UTC)
