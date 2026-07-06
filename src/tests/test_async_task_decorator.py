from __future__ import annotations

import asyncio
from inspect import signature
from typing import Any, cast

import pytest

from auth.mcp_caller_context import AuthenticatedMcpCaller
from core.types import LogWorkspace
from database.models import McpCaller, Task
from database.schemas import AsyncTaskResult
from database.types import TaskStatus, TaskType
from tasks import INTERNAL_TASK_CLIENT_ID, INTERNAL_TASK_CLIENT_TYPE, collect_logs_task, task
from tests.factories import McpCallerFactory


@pytest.mark.anyio
async def test_task_decorator_keeps_direct_call_inline(
    db: None,  # noqa: ARG001
) -> None:
    @task(TaskType.LOG_COLLECTION)
    async def collect_like_tool(project_names: list[str]) -> dict[str, Any]:
        return {"requested_project_names": project_names}

    result = await collect_like_tool(project_names=["mcp"])

    assert result == {"requested_project_names": ["mcp"]}
    assert await Task.objects.count() == 0
    assert hasattr(collect_like_tool, "delay")
    assert hasattr(collect_like_tool, "apply_async")


@pytest.mark.anyio
async def test_task_decorator_delay_persists_task_and_runs_background(
    db: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = await McpCallerFactory.save_to_db(
        client_id="async-task-caller",
        client_type="workflow_agent",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["all"],
    )
    started = asyncio.Event()
    release = asyncio.Event()

    monkeypatch.setattr(
        "tasks.get_request_mcp_caller",
        lambda: AuthenticatedMcpCaller(
            client_id=caller.client_id,
            client_type=caller.client_type,
            workspace=caller.workspace,
            allowed_projects=frozenset(caller.allowed_projects),
            caller_id=caller.id,
        ),
    )

    @task(TaskType.LOG_COLLECTION)
    async def collect_like_tool(
        project_names: list[str],
        source_keys: list[str],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        started.set()
        await release.wait()
        return {
            "action": "collect_logs",
            "session_id": session_id,
            "requested_project_names": project_names,
        }

    queued_task = await collect_like_tool.delay(
        project_names=["agent-monitoring", "mcp"],
        source_keys=["all"],
        session_id="async-session",
    )

    assert isinstance(queued_task, AsyncTaskResult)
    assert queued_task.status == TaskStatus.QUEUED
    assert queued_task.task_type == TaskType.LOG_COLLECTION
    assert queued_task.project_name is None
    assert repr(queued_task) == f"<AsyncTaskResult: {queued_task.id}>"
    saved_task = await Task.objects.get(id=queued_task.id)
    saved_caller = await McpCaller.objects.get(id=caller.id)
    assert saved_task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}
    assert saved_caller.client_id == "async-task-caller"
    assert cast(Any, saved_task.project_name) is None
    assert saved_task.arguments == {
        "args": [],
        "kwargs": {
            "project_names": ["agent-monitoring", "mcp"],
            "source_keys": ["all"],
            "session_id": "async-session",
        },
    }
    assert saved_task.result is None

    await asyncio.wait_for(started.wait(), timeout=1)
    running = await Task.objects.get(id=queued_task.id)
    assert running.status == TaskStatus.RUNNING
    assert running.started_at is not None
    release.set()

    saved = await Task.objects.get(id=queued_task.id)
    for _ in range(20):
        if saved.status == TaskStatus.COMPLETED:
            break
        await asyncio.sleep(0.05)
        saved = await Task.objects.get(id=queued_task.id)

    assert saved.status == TaskStatus.COMPLETED
    assert saved.result == {
        "action": "collect_logs",
        "session_id": "async-session",
        "requested_project_names": ["agent-monitoring", "mcp"],
    }
    assert saved.completed_at is not None


@pytest.mark.anyio
async def test_task_decorator_apply_async_uses_celery_style_arguments(
    db: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = await McpCallerFactory.save_to_db(
        client_id="async-task-apply-caller",
        client_type="workflow_agent",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["all"],
    )
    monkeypatch.setattr(
        "tasks.get_request_mcp_caller",
        lambda: AuthenticatedMcpCaller(
            client_id=caller.client_id,
            client_type=caller.client_type,
            workspace=caller.workspace,
            allowed_projects=frozenset(caller.allowed_projects),
            caller_id=caller.id,
        ),
    )

    @task(TaskType.LOG_COLLECTION)
    async def collect_like_tool(
        project_name: str,
        project_names: list[str],
        source_keys: list[str],
    ) -> dict[str, Any]:
        return {"project_names": project_names, "source_keys": source_keys}

    queued_task = await collect_like_tool.apply_async(
        args=("mcp", ["mcp"]),
        kwargs={"source_keys": ["all"]},
    )

    assert isinstance(queued_task, AsyncTaskResult)
    assert queued_task.project_name == "mcp"
    saved = await Task.objects.get(id=queued_task.id)
    for _ in range(20):
        if saved.status == TaskStatus.COMPLETED:
            break
        await asyncio.sleep(0.05)
        saved = await Task.objects.get(id=queued_task.id)

    assert saved.status == TaskStatus.COMPLETED
    assert saved.arguments == {
        "args": ["mcp", ["mcp"]],
        "kwargs": {"source_keys": ["all"]},
    }
    assert saved.project_name == "mcp"
    assert saved.result == {"project_names": ["mcp"], "source_keys": ["all"]}


@pytest.mark.anyio
async def test_task_decorator_uses_internal_owner_outside_mcp_request(
    db: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tasks.get_request_mcp_caller",
        lambda: (_ for _ in ()).throw(RuntimeError("No active HTTP request found.")),
    )

    @task(TaskType.LOG_COLLECTION)
    async def collect_like_tool() -> dict[str, Any]:
        return {"ok": True}

    queued_task = await collect_like_tool.delay()

    assert isinstance(queued_task, AsyncTaskResult)
    saved = await Task.objects.get(id=queued_task.id)
    for _ in range(20):
        if saved.status == TaskStatus.COMPLETED:
            break
        await asyncio.sleep(0.05)
        saved = await Task.objects.get(id=queued_task.id)

    assert saved.status == TaskStatus.COMPLETED
    assert saved.workspace == LogWorkspace.WORKFLOW
    saved_caller = await McpCaller.objects.get(client_id=INTERNAL_TASK_CLIENT_ID)
    assert saved_caller.client_type == INTERNAL_TASK_CLIENT_TYPE
    assert saved.result == {"ok": True}


@pytest.mark.anyio
async def test_task_decorator_is_for_task_functions_not_service_methods(
    db: None,  # noqa: ARG001
) -> None:
    @task(TaskType.LOG_COLLECTION)
    async def collect_logs_task(project_name: str, source_keys: list[str]) -> dict[str, Any]:
        return {"project_name": project_name, "source_keys": source_keys}

    task_signature = signature(collect_logs_task)
    assert list(task_signature.parameters) == ["project_name", "source_keys"]
    assert hasattr(collect_logs_task, "delay")
    assert hasattr(collect_logs_task, "apply_async")


def test_collect_logs_task_signature_uses_collection_inputs() -> None:
    task_signature = signature(collect_logs_task)

    assert "project_name" not in task_signature.parameters
    assert list(task_signature.parameters) == [
        "manifest",
        "sources",
        "missing_source_keys",
        "source_keys",
        "workspace",
        "session_id",
        "since",
        "until",
    ]


@pytest.mark.anyio
async def test_task_decorator_marks_task_failed_when_background_job_raises(
    db: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = await McpCallerFactory.save_to_db(
        client_id="async-task-failing-caller",
        client_type="workflow_agent",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["all"],
    )
    monkeypatch.setattr(
        "tasks.get_request_mcp_caller",
        lambda: AuthenticatedMcpCaller(
            client_id=caller.client_id,
            client_type=caller.client_type,
            workspace=caller.workspace,
            allowed_projects=frozenset(caller.allowed_projects),
            caller_id=caller.id,
        ),
    )

    @task(TaskType.LOG_COLLECTION)
    async def failing_collection() -> dict[str, Any]:
        raise RuntimeError("docker logs timed out")

    queued_task = await failing_collection.delay()

    saved = await Task.objects.get(id=queued_task.id)
    for _ in range(20):
        if saved.status == TaskStatus.FAILED:
            break
        await asyncio.sleep(0.05)
        saved = await Task.objects.get(id=queued_task.id)

    assert saved.status == TaskStatus.FAILED
    assert saved.error_code == "RuntimeError"
    assert saved.error_message == "docker logs timed out"
    assert saved.result == {
        "error_code": "RuntimeError",
        "error_message": "docker logs timed out",
    }
    assert saved.completed_at is not None


@pytest.mark.anyio
async def test_task_decorator_marks_bad_task_arguments_failed(
    db: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = await McpCallerFactory.save_to_db(
        client_id="async-task-bad-args-caller",
        client_type="workflow_agent",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["all"],
    )
    monkeypatch.setattr(
        "tasks.get_request_mcp_caller",
        lambda: AuthenticatedMcpCaller(
            client_id=caller.client_id,
            client_type=caller.client_type,
            workspace=caller.workspace,
            allowed_projects=frozenset(caller.allowed_projects),
            caller_id=caller.id,
        ),
    )

    @task(TaskType.LOG_COLLECTION)
    async def keyword_only_task(*, project_name: str) -> dict[str, Any]:
        return {"project_name": project_name}

    queued_task = await keyword_only_task.delay("QQQDQD", "wdwdw", "sqs")

    assert queued_task.status == TaskStatus.FAILED
    saved = await Task.objects.get(id=queued_task.id)
    for _ in range(20):
        if saved.status == TaskStatus.FAILED:
            break
        await asyncio.sleep(0.05)
        saved = await Task.objects.get(id=queued_task.id)

    assert saved.status == TaskStatus.FAILED
    assert saved.error_code == "TypeError"
    assert saved.error_message == (
        "keyword_only_task accepts keyword arguments only; received 3 positional arguments. "
        "Use keyword arguments: project_name."
    )
    assert saved.result == {
        "error_code": "TypeError",
        "error_message": saved.error_message,
    }
    assert saved.completed_at is not None
