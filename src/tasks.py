"""Background task entrypoints."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from functools import update_wrapper
from inspect import Parameter, signature
from typing import Any, Generic, ParamSpec, TypeVar, cast

from fastmcp.tools.base import ToolResult

from auth.mcp_caller_context import get_request_mcp_caller
from core.types import LogWorkspace
from database.models import McpCaller, Task
from database.schemas import AsyncTaskResult
from database.types import TaskStatus, TaskType
from logging_config import get_logger
from manifests.models import Manifest, SourceDefinition
from services.log_collection import BuildLogsError, LogCollectionService
from tools.models import ProjectCollectLogsPayload, SnapshotWorkspace
from utils.types import JSONValue

logger = get_logger("tasks")
P = ParamSpec("P")
T = TypeVar("T")
AsyncCallable = Callable[P, Coroutine[Any, Any, T]]
INTERNAL_TASK_CLIENT_ID = "internal-task-runner"
INTERNAL_TASK_CLIENT_TYPE = "internal"
INTERNAL_TASK_WORKSPACE = LogWorkspace.WORKFLOW
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _remember_background_task(background_task: asyncio.Task[None]) -> None:
    """Keep fire-and-forget tasks alive until they finish."""

    _BACKGROUND_TASKS.add(background_task)
    background_task.add_done_callback(_BACKGROUND_TASKS.discard)


class TaskRunner(Generic[P, T]):  # noqa: UP046
    """Celery-like task object for one async function."""

    def __init__(self, *, task_type: TaskType, func: AsyncCallable[P, T]) -> None:
        self.task_type = task_type
        self.func = func
        self.__signature__ = signature(func)
        update_wrapper(self, func)

    @classmethod
    def _json_safe_value(cls, value: Any) -> JSONValue:
        """Return a JSON-safe representation of one task value."""

        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [cls._json_safe_value(item) for item in value]
        if isinstance(value, tuple):
            return [cls._json_safe_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): cls._json_safe_value(item)
                for key, item in value.items()
                if isinstance(key, str | int | float | bool)
            }
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json", exclude_none=True)
            return cls._json_safe_value(dumped)
        return repr(value)

    @classmethod
    def _serialize_call(
        cls,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> dict[str, JSONValue]:
        """Return raw task call arguments for DB persistence."""

        return {
            "args": cls._json_safe_value(args),
            "kwargs": cls._json_safe_value(kwargs),
        }

    @staticmethod
    def _error_result_payload(exc: Exception) -> dict[str, str]:
        return {
            "error_code": exc.__class__.__name__,
            "error_message": str(exc),
        }

    def _task_argument_error(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        exc: TypeError,
    ) -> TypeError:
        positional_parameters = [
            name
            for name, parameter in self.__signature__.parameters.items()
            if parameter.kind
            in {
                Parameter.POSITIONAL_ONLY,
                Parameter.POSITIONAL_OR_KEYWORD,
                Parameter.VAR_POSITIONAL,
            }
        ]
        if args and not positional_parameters:
            keyword_parameters = [
                name
                for name, parameter in self.__signature__.parameters.items()
                if parameter.kind
                in {
                    Parameter.KEYWORD_ONLY,
                    Parameter.POSITIONAL_OR_KEYWORD,
                }
            ]
            accepted_arguments = ", ".join(keyword_parameters)
            message = (
                f"{self.func.__name__} accepts keyword arguments only; received "
                f"{len(args)} positional arguments. Use keyword arguments"
            )
            if accepted_arguments:
                message = f"{message}: {accepted_arguments}."
            else:
                message = f"{message}."
            return TypeError(message)
        if args and kwargs:
            return TypeError(
                f"{self.func.__name__} received invalid task arguments: {exc}. "
                "Use delay(...args) or delay(name=value), matching the task signature."
            )
        return exc

    @classmethod
    def _serialize_result(cls, result: Any) -> dict[str, Any]:
        """Return a JSON object suitable for persisting as task result."""

        if isinstance(result, ToolResult):
            structured_content = result.structured_content
            if isinstance(structured_content, dict):
                return structured_content
            return {"structured_content": cls._json_safe_value(structured_content)}
        safe_result = cls._json_safe_value(result)
        if isinstance(safe_result, dict):
            return safe_result
        return {"result": safe_result}

    @staticmethod
    def _session_id(kwargs: dict[str, Any]) -> str | None:
        session_id = kwargs.get("session_id")
        return session_id if isinstance(session_id, str) else None

    def _project_name(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str | None:
        try:
            bound = self.__signature__.bind_partial(*args, **kwargs)
        except TypeError:
            return None
        project_name = bound.arguments.get("project_name")
        if isinstance(project_name, str):
            return project_name
        manifest = bound.arguments.get("manifest")
        if isinstance(manifest, Manifest):
            return manifest.project_key
        return None

    @staticmethod
    async def _task_owner() -> tuple[LogWorkspace, int]:
        """Return request caller ownership, falling back to the internal task caller."""

        try:
            caller = get_request_mcp_caller()
        except RuntimeError:
            internal_caller = await McpCaller.objects.filter(
                client_id=INTERNAL_TASK_CLIENT_ID,
                client_type=INTERNAL_TASK_CLIENT_TYPE,
                workspace=INTERNAL_TASK_WORKSPACE,
            ).first()
            if internal_caller is None:
                internal_caller = await McpCaller.objects.create(
                    client_id=INTERNAL_TASK_CLIENT_ID,
                    client_type=INTERNAL_TASK_CLIENT_TYPE,
                    workspace=INTERNAL_TASK_WORKSPACE,
                    allowed_projects=["all"],
                )
            return INTERNAL_TASK_WORKSPACE, cast(int, internal_caller.id)
        return caller.workspace, caller.caller_id

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        """Run the task function inline in the current request."""

        return await self.func(*args, **kwargs)

    async def apply_async(
        self,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> AsyncTaskResult:
        """Create the DB task row and return a Celery-like async result handle."""

        task_kwargs = kwargs or {}
        workspace, caller_id = await self._task_owner()
        task_row = await Task.objects.create(
            task_type=self.task_type,
            status=TaskStatus.QUEUED,
            workspace=workspace,
            caller_id=caller_id,
            session_id=self._session_id(task_kwargs),
            project_name=self._project_name(args, task_kwargs),
            arguments=self._serialize_call(args, task_kwargs),
        )
        try:
            self.__signature__.bind(*args, **task_kwargs)
        except TypeError as exc:
            exc = self._task_argument_error(args, task_kwargs, exc)
            task_row.status = TaskStatus.FAILED
            task_row.error_code = exc.__class__.__name__
            task_row.error_message = str(exc)
            task_row.result = self._error_result_payload(exc)
            task_row.completed_at = datetime.now(UTC)
            await task_row.save(
                update_fields=[
                    "status",
                    "result",
                    "error_code",
                    "error_message",
                    "completed_at",
                    "updated_at",
                ]
            )
            return AsyncTaskResult(
                id=task_row.id,
                task_type=task_row.task_type,
                status=task_row.status,
                session_id=task_row.session_id,
                project_name=task_row.project_name,
            )

        _remember_background_task(
            asyncio.create_task(
                self.run(
                    task_id=task_row.id,
                    args=args,
                    kwargs=task_kwargs,
                )
            )
        )
        return AsyncTaskResult(
            id=task_row.id,
            task_type=task_row.task_type,
            status=task_row.status,
            session_id=task_row.session_id,
            project_name=task_row.project_name,
        )

    async def delay(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncTaskResult:
        """Shortcut for apply_async with plain args and kwargs."""

        return await self.apply_async(
            args=args,
            kwargs=kwargs,
        )

    async def run(
        self,
        *,
        task_id: object,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        """Execute this queued task and persist its terminal status."""

        task_row: Task | None = None
        try:
            task_row = await Task.objects.get(id=task_id)
            task_row.status = TaskStatus.RUNNING
            task_row.started_at = datetime.now(UTC)
            await task_row.save(update_fields=["status", "started_at", "updated_at"])
            result = await self.func(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - exercised by status tests
            if task_row is None:
                logger.exception(
                    "async task startup failed",
                    extra={
                        "event": "async_task_startup_failed",
                        "task_id": str(task_id),
                        "task_type": self.task_type.value,
                    },
                )
                return

            task_row.status = TaskStatus.FAILED
            task_row.error_code = exc.__class__.__name__
            task_row.error_message = str(exc)
            task_row.result = self._error_result_payload(exc)
            task_row.completed_at = datetime.now(UTC)
            await task_row.save(
                update_fields=[
                    "status",
                    "result",
                    "error_code",
                    "error_message",
                    "completed_at",
                    "updated_at",
                ]
            )
            logger.exception(
                "async task failed",
                extra={
                    "event": "async_task_failed",
                    "task_id": str(task_id),
                    "task_type": task_row.task_type.value,
                },
            )
            return

        task_row.status = TaskStatus.COMPLETED
        task_row.result = self._serialize_result(result)
        task_row.completed_at = datetime.now(UTC)
        await task_row.save(update_fields=["status", "result", "completed_at", "updated_at"])


def task(
    task_type: TaskType,
) -> Callable[[AsyncCallable[P, T]], TaskRunner[P, T]]:
    """Wrap an async function in a Celery-like task object."""

    def decorator(func: AsyncCallable[P, T]) -> TaskRunner[P, T]:
        return TaskRunner(task_type=task_type, func=func)

    return decorator


collection_service = LogCollectionService()


@task(TaskType.LOG_COLLECTION)
async def collect_logs_task(
    *,
    manifest: Manifest,
    sources: list[SourceDefinition],
    missing_source_keys: list[str],
    source_keys: list[str],
    workspace: SnapshotWorkspace = LogWorkspace.WORKFLOW,
    session_id: str | None,
    since: str | None,
    until: str | None,
) -> ProjectCollectLogsPayload | BuildLogsError:
    """Collect logs in the background using the log collection service."""

    return await collection_service.build_logs(
        manifest=manifest,
        sources=sources,
        missing_source_keys=missing_source_keys,
        source_keys=source_keys,
        workspace=workspace,
        session_id=session_id,
        since=since,
        until=until,
    )
