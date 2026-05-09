"""Database service for persisted project manifest metadata.

AI agent note: keep ORM access in this module routed through the model
``objects`` manager, for example ``ProjectManifest.objects.get(...)``. Do not
call Tortoise class methods such as ``ProjectManifest.get(...)`` directly.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID, uuid4

from database.models import ProjectManifest
from database.services.models import ProjectManifestUpdate
from manifests.models import Manifest


class ProjectManifestService:
    """Wrap ORM access for project manifest metadata rows."""

    model: ClassVar[type[ProjectManifest]] = ProjectManifest

    async def create_or_update(
        self,
        manifest: Manifest,
        *,
        pk: UUID | None = None,
    ) -> ProjectManifest:
        """Create or update one project manifest row from a validated manifest."""

        payload = manifest.model_dump(mode="json")
        payload["id"] = pk or uuid4()
        payload["sources"] = [source.model_dump(mode="json") for source in manifest.sources]
        existing = await self.model.objects.filter(
            project_key=manifest.project_key,
        ).first()
        if existing is None:
            return await self.model.objects.create(**payload)

        return await self.update(
            ProjectManifestUpdate(
                pk=existing.id,
                project_summary=manifest.project_summary,
                static_asset_paths=manifest.static_asset_paths,
                static_asset_extensions=manifest.static_asset_extensions,
                sources=payload["sources"],
            )
        )

    async def get(self, project_key: str) -> ProjectManifest:
        """Return one persisted project manifest by project key."""

        return await self.model.objects.get(project_key=project_key)

    async def all(self) -> list[ProjectManifest]:
        """Return all persisted project manifests ordered by project key."""

        return await self.model.objects.all().order_by("project_key")

    async def update(self, payload: ProjectManifestUpdate) -> ProjectManifest:
        """Update one project manifest row with the provided metadata fields."""

        row = await self.model.objects.get(id=payload.pk)
        context = payload.model_dump(exclude={"pk"}, exclude_none=True)
        for field_name, field_value in context.items():
            setattr(row, field_name, field_value)
        await row.save(update_fields=[*context, "updated_at"])
        return row
