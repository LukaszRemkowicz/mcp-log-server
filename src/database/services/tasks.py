"""Database service for async task rows."""

from __future__ import annotations

from typing import Any, ClassVar, cast

from tortoise.exceptions import DoesNotExist

from database.models import Task
from database.schemas import TaskListOut, TaskOut
from database.types import TaskType


class TaskService:
    """Wrap ORM access for persisted async task rows."""

    model: ClassVar[type[Task]] = Task

    async def get(
        self,
        *,
        task_type: TaskType,
        caller_id: int,
        session_id: str,
    ) -> TaskOut | None:
        """Return one caller-owned task for a public session id, when present."""

        try:
            task_row = await self.model.objects.get(
                task_type=task_type,
                caller_id=caller_id,
                session_id=session_id,
            )
        except DoesNotExist:
            return None
        return self._to_out(task_row)

    async def get_session_tasks(
        self,
        *,
        task_type: TaskType,
        caller_id: int,
        session_id: str,
    ) -> TaskListOut:
        """Return all caller-owned tasks for one public session id."""

        task_rows = await self.model.objects.filter(
            task_type=task_type,
            caller_id=caller_id,
            session_id=session_id,
        ).order_by("created_at")
        return TaskListOut(tasks=[self._to_out(task_row) for task_row in task_rows])

    @staticmethod
    def _to_out(obj: Task) -> TaskOut:
        """Return the DB OUT pydantic representation for one task row."""

        return TaskOut(
            id=obj.id,
            created_at=obj.created_at,
            task_type=obj.task_type,
            status=obj.status,
            workspace=obj.workspace,
            caller_id=cast(int, cast(Any, obj).caller_id),
            session_id=obj.session_id,
            project_name=obj.project_name,
            arguments=obj.arguments,
            result=obj.result,
            error_code=obj.error_code,
            error_message=obj.error_message,
            started_at=obj.started_at,
            completed_at=obj.completed_at,
            expires_at=obj.expires_at,
        )
