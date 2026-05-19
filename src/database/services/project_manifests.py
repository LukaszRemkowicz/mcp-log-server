"""Database service for persisted project manifest metadata.

AI agent note: keep ORM access in this module routed through the model
``objects`` manager, for example ``ProjectManifest.objects.get(...)``. Do not
call Tortoise class methods such as ``ProjectManifest.get(...)`` directly.
"""

from __future__ import annotations

from typing import ClassVar

from database.models import ProjectManifest
from database.schemas import ProjectManifestCreate, ProjectManifestUpdate


class ProjectManifestService:
    """Wrap ORM access for project manifest metadata rows."""

    model: ClassVar[type[ProjectManifest]] = ProjectManifest

    async def exists(self, project_key: str) -> bool:
        """Return whether one project manifest already exists."""

        return (await self.model.objects.filter(project_key=project_key).limit(1).count()) > 0

    async def create(self, payload: ProjectManifestCreate) -> ProjectManifest:
        """Create one project manifest row."""

        context = payload.model_dump(exclude={"pk"}, exclude_none=True)
        return await self.model.objects.create(id=payload.pk, **context)

    async def get(self, project_key: str) -> ProjectManifest:
        """Return one persisted project manifest by project key."""

        return await self.model.objects.get(project_key=project_key)

    async def all(self) -> list[ProjectManifest]:
        """Return all persisted project manifests ordered by project key."""

        return await self.model.objects.all().order_by("project_key")

    async def update(self, payload: ProjectManifestUpdate) -> ProjectManifest:
        """Update one project manifest row with the provided metadata fields."""

        obj = await self.model.objects.get(id=payload.pk)
        context = payload.model_dump(exclude={"pk"}, exclude_none=True)
        for field_name, field_value in context.items():
            setattr(obj, field_name, field_value)
        await obj.save(update_fields=[*context, "updated_at"])
        return obj
