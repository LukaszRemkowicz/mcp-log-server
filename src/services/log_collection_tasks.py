"""Services for persisted log-collection background tasks."""

from __future__ import annotations

from typing import Any

from database.schemas import TaskListOut
from database.services.tasks import TaskService
from database.types import TaskType
from tools.models import LogCollectionProjectTaskStatusPayload, LogCollectionTaskStatusPayload


class LogCollectionTaskService:
    """Read persisted async log-collection task state."""

    def __init__(self, *, task_service: TaskService | None = None) -> None:
        self.task_service = task_service or TaskService()

    def _strip_none_values(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._strip_none_values(item)
                for key, item in value.items()
                if item is not None
            }
        if isinstance(value, list):
            return [self._strip_none_values(item) for item in value]
        return value

    async def get_status(
        self,
        *,
        caller_id: int,
        session_id: str,
    ) -> LogCollectionTaskStatusPayload | None:
        """Return project task statuses for a caller-owned log-collection session."""

        task_list: TaskListOut = await self.task_service.get_session_tasks(
            task_type=TaskType.LOG_COLLECTION,
            caller_id=caller_id,
            session_id=session_id,
        )
        task_rows = task_list.tasks
        if not task_rows:
            return None

        return LogCollectionTaskStatusPayload(
            action="get_log_collection_status",
            task_type=TaskType.LOG_COLLECTION,
            workspace=task_rows[0].workspace,
            session_id=session_id,
            task_count=len(task_rows),
            created_at=task_rows[0].created_at.isoformat(),
            started_at=(
                min(
                    task_row.started_at for task_row in task_rows if task_row.started_at is not None
                ).isoformat()
                if any(task_row.started_at is not None for task_row in task_rows)
                else None
            ),
            completed_at=(
                max(
                    task_row.completed_at
                    for task_row in task_rows
                    if task_row.completed_at is not None
                ).isoformat()
                if all(task_row.completed_at is not None for task_row in task_rows)
                else None
            ),
            tasks=[
                LogCollectionProjectTaskStatusPayload(
                    project_name=task_row.project_name,
                    status=task_row.status,
                    created_at=task_row.created_at.isoformat(),
                    started_at=(
                        task_row.started_at.isoformat() if task_row.started_at is not None else None
                    ),
                    completed_at=(
                        task_row.completed_at.isoformat()
                        if task_row.completed_at is not None
                        else None
                    ),
                    result=self._strip_none_values(task_row.result),
                    error_code=task_row.error_code,
                    error_message=task_row.error_message,
                )
                for task_row in task_rows
            ],
        )
