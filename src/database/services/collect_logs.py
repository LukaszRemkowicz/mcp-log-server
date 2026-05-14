"""Database services for persisted collect_logs metadata.

AI agent note: keep ORM access in this module routed through the model
``objects`` manager, for example ``CollectLogs.objects.get(...)``. Do not call
Tortoise class methods such as ``CollectLogs.get(...)`` directly.
"""

from __future__ import annotations

from typing import ClassVar

from core.types import LogWorkspace
from database.fields import FileReference, FileStorage
from database.models import CollectLogs, CollectLogsSource
from database.schemas import (
    CollectLogsCreate,
    CollectLogsOut,
    CollectLogsSourceCreate,
    CollectLogsSourceOut,
    CollectLogsWithSourcesOut,
)
from storage import storage as log_storage


class CollectLogsService:
    """Wrap ORM access for collect_logs artifact metadata rows."""

    model: ClassVar[type[CollectLogs]] = CollectLogs

    async def create(self, payload: CollectLogsCreate) -> CollectLogsOut:
        """Create one collect_logs artifact row."""

        obj = await self.model.objects.create(**payload.model_dump())
        return self._to_out(obj)

    async def get(self, collect_logs_id: int) -> CollectLogsOut:
        """Return one collect_logs artifact row by id."""

        obj = await self.model.objects.get(id=collect_logs_id)
        return self._to_out(obj)

    async def get_with_sources(self, collect_logs_id: int) -> CollectLogsWithSourcesOut:
        """Return one collect_logs artifact row with source rows by id."""

        obj = await self.model.objects.get(id=collect_logs_id)
        return await self._to_out_with_sources(obj)

    async def get_latest(self, project_name: str) -> CollectLogsOut | None:
        """Return current latest workflow row for one project."""

        obj = await self.model.objects.get_latest(project_name)
        if obj is None:
            return None
        return self._to_out(obj)

    async def get_latest_with_sources(
        self,
        project_name: str,
    ) -> CollectLogsWithSourcesOut | None:
        """Return current latest workflow row with source rows for one project."""

        obj = await self.model.objects.get_latest(project_name)
        if obj is None:
            return None
        return await self._to_out_with_sources(obj)

    async def get_session_collect_logs_with_sources(
        self,
        *,
        project_name: str,
        session_id: str,
    ) -> CollectLogsWithSourcesOut | None:
        """Return one session collect_logs row with source rows."""

        obj = await self.model.objects.filter(
            project_name=project_name,
            workspace=LogWorkspace.SESSION,
            session_id=session_id,
        ).first()
        if obj is None:
            return None
        return await self._to_out_with_sources(obj)

    async def get_archive_with_sources(
        self,
        *,
        project_name: str,
        archive_name: str,
    ) -> CollectLogsWithSourcesOut | None:
        """Return one archived workflow row with source rows."""

        obj = await self.model.objects.filter(
            project_name=project_name,
            workspace=LogWorkspace.WORKFLOW,
            archive_name=archive_name,
        ).first()
        if obj is None:
            return None
        return await self._to_out_with_sources(obj)

    @staticmethod
    def _to_out(obj: CollectLogs) -> CollectLogsOut:
        """Return the DB OUT pydantic representation for one collect_logs row."""

        return CollectLogsOut(
            id=obj.id,
            session_id=obj.session_id,
            workspace=obj.workspace,
            project_name=obj.project_name,
            collected_at=obj.collected_at,
            snapshot_dir=obj.snapshot_dir,
            archive_name=obj.archive_name,
            is_latest=obj.is_latest,
            requested_source_keys=obj.requested_source_keys,
            resolved_source_keys=obj.resolved_source_keys,
            unknown_requested_source_keys=obj.unknown_requested_source_keys,
            requested_since=obj.requested_since,
            requested_until=obj.requested_until,
            warnings=obj.warnings,
            retry_tips=obj.retry_tips,
        )

    async def _to_out_with_sources(self, obj: CollectLogs) -> CollectLogsWithSourcesOut:
        """Return the collect_logs DB OUT representation with embedded source rows."""

        sources: list[CollectLogsSource] = await obj.sources.all()
        return CollectLogsWithSourcesOut(
            **self._to_out(obj).model_dump(),
            sources=[
                CollectLogsSourceOut(
                    id=source.id,
                    source_key=source.source_key,
                    source_type=source.source_type.value,
                    target=source.target,
                    description=source.description,
                    stream=source.stream.value if source.stream is not None else None,
                    parser_type=source.parser_type,
                    normalization_profile=source.normalization_profile,
                    default_noise_profile=source.default_noise_profile,
                    status=source.status.value,
                    file=(
                        FileReference(
                            name=source.file.name,
                            storage=FileStorage(location=log_storage.location),
                        )
                        if source.file is not None
                        else None
                    ),
                    line_count=source.line_count,
                    error=source.error,
                    retry_tips=source.retry_tips,
                )
                for source in sources
            ],
        )

    @staticmethod
    async def archive(
        collect_logs_id: int,
        *,
        archive_name: str,
        snapshot_dir: str,
    ) -> None:
        """Mark one collect_logs obj as archived using caller-provided metadata."""

        obj = await CollectLogs.objects.get(id=collect_logs_id)
        obj.is_latest = False
        obj.archive_name = archive_name
        obj.snapshot_dir = snapshot_dir
        await obj.save()


class CollectLogsSourceService:
    """Wrap ORM access for collected log source metadata rows."""

    model: ClassVar[type[CollectLogsSource]] = CollectLogsSource

    async def create(
        self,
        collect_logs: CollectLogsOut,
        payload: CollectLogsSourceCreate,
    ) -> None:
        """Create one collected source metadata row."""

        await self.model.objects.create(
            collect_logs_id=collect_logs.id,
            **payload.model_dump(),
        )

    async def create_many(
        self,
        collect_logs: CollectLogsOut,
        payloads: list[CollectLogsSourceCreate],
    ) -> None:
        """Create multiple collected source metadata rows."""

        for payload in payloads:
            await self.create(collect_logs, payload)

    async def update_file(self, collect_logs_source_id: int, file: str) -> None:
        """Update one collected source file path."""

        obj = await self.model.objects.get(id=collect_logs_source_id)
        obj.file = FileReference(name=file)
        await obj.save(update_fields=["file"])
