"""Database service for persisted project manifest metadata.

AI agent note: keep ORM access in this module routed through the model
``objects`` manager, for example ``ProjectManifest.objects.get(...)``. Do not
call Tortoise class methods such as ``ProjectManifest.get(...)`` directly.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from database.models import ProjectManifest
from database.services.models import ProjectManifestUpdate
from manifests.models import Manifest


class ProjectManifestService:
    """Wrap ORM access for project manifest metadata rows."""

    @staticmethod
    async def create_or_update(
        manifest: Manifest,
        *,
        pk: UUID | None = None,
    ) -> ProjectManifest:
        """Create or update one project manifest row from a validated manifest."""

        payload = manifest.model_dump(mode="json")
        payload["id"] = pk or uuid4()
        payload["sources"] = [source.model_dump(mode="json") for source in manifest.sources]
        existing = await ProjectManifest.objects.filter(project_key=manifest.project_key).first()
        if existing is None:
            return await ProjectManifest.objects.create(**payload)

        return await ProjectManifestService.update(
            ProjectManifestUpdate(
                pk=existing.id,
                project_summary=manifest.project_summary,
                static_asset_paths=manifest.static_asset_paths,
                static_asset_extensions=manifest.static_asset_extensions,
                sources=payload["sources"],
            )
        )

    @staticmethod
    async def get(project_key: str) -> ProjectManifest:
        """Return one persisted project manifest by project key."""

        return await ProjectManifest.objects.get(project_key=project_key)

    @staticmethod
    async def all() -> list[ProjectManifest]:
        """Return all persisted project manifests ordered by project key."""

        return await ProjectManifest.objects.all().order_by("project_key")

    @staticmethod
    async def update(payload: ProjectManifestUpdate) -> ProjectManifest:
        """Update one project manifest row with the provided metadata fields."""

        row = await ProjectManifest.objects.get(id=payload.pk)
        context = payload.model_dump(exclude={"pk"}, exclude_none=True)
        for field_name, field_value in context.items():
            setattr(row, field_name, field_value)
        await row.save(update_fields=[*context, "updated_at"])
        return row
