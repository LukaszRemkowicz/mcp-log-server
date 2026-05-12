"""Database services for persisted collect_logs metadata.

AI agent note: keep ORM access in this module routed through the model
``objects`` manager, for example ``CollectLogs.objects.get(...)``. Do not call
Tortoise class methods such as ``CollectLogs.get(...)`` directly.
"""

from __future__ import annotations

from typing import ClassVar

from database.models import CollectLogs, CollectLogsSource
from database.services.models import CollectLogsCreate, CollectLogsSourceCreate


class CollectLogsService:
    """Wrap ORM access for collect_logs artifact metadata rows."""

    model: ClassVar[type[CollectLogs]] = CollectLogs

    async def create(self, payload: CollectLogsCreate) -> CollectLogs:
        """Create one collect_logs artifact row."""

        return await self.model.objects.create(**payload.model_dump())

    async def get(self, collect_logs_id: int) -> CollectLogs:
        """Return one collect_logs artifact row by id."""

        return await self.model.objects.get(id=collect_logs_id)

    async def get_latest(self, project_name: str) -> CollectLogs | None:
        """Return current latest workflow row for one project."""

        return await self.model.objects.get_latest(project_name)

    @staticmethod
    async def archive(
        obj: CollectLogs,
        *,
        archive_name: str,
        snapshot_dir: str,
    ) -> None:
        """Mark one collect_logs obj as archived using caller-provided metadata."""

        obj.is_latest = False
        obj.archive_name = archive_name
        obj.snapshot_dir = snapshot_dir
        await obj.save()


class CollectLogsSourceService:
    """Wrap ORM access for collected log source metadata rows."""

    model: ClassVar[type[CollectLogsSource]] = CollectLogsSource

    async def create(
        self,
        collect_logs: CollectLogs,
        payload: CollectLogsSourceCreate,
    ) -> CollectLogsSource:
        """Create one collected source metadata row."""

        return await self.model.objects.create(
            collect_logs=collect_logs,
            **payload.model_dump(),
        )

    async def create_many(
        self,
        collect_logs: CollectLogs,
        payloads: list[CollectLogsSourceCreate],
    ) -> list[CollectLogsSource]:
        """Create multiple collected source metadata rows."""

        rows: list[CollectLogsSource] = []
        for payload in payloads:
            rows.append(await self.create(collect_logs, payload))
        return rows
