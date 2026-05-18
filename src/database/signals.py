"""Database model signal registrations."""

from __future__ import annotations

from typing import Any

from tortoise.signals import post_delete, post_save

from database.models import ProjectManifest


@post_save(ProjectManifest)
async def clear_project_manifest_cache_on_save(
    sender: type[ProjectManifest],
    instance: ProjectManifest,  # noqa: ARG001
    created: bool,  # noqa: ARG001
    using_db: Any,  # noqa: ARG001
    update_fields: Any,  # noqa: ARG001
) -> None:
    """Clear ProjectManifest caches when manifest rows are saved."""

    await sender.clear_cache()


@post_delete(ProjectManifest)
async def clear_project_manifest_cache_on_delete(
    sender: type[ProjectManifest],
    instance: ProjectManifest,  # noqa: ARG001
    using_db: Any,  # noqa: ARG001
) -> None:
    """Clear ProjectManifest caches when manifest rows are deleted."""

    await sender.clear_cache()


def register_database_signals() -> None:
    """Import this module to register Tortoise signal listeners."""
