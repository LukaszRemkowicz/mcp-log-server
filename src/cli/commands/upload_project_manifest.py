"""Upload project manifest files into the database."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import typer

from conf import settings
from database.config import TORTOISE_ORM
from database.lifecycle import close_database, initialize_database
from database.models import ProjectManifest
from database.schemas import ProjectManifestCreate, ProjectManifestUpdate
from database.services.project_manifests import ProjectManifestService
from decorators import async_
from manifests.loader import list_project_manifests, load_project_manifest
from manifests.models import Manifest

ProjectManifestCommandStatus = Literal["created", "exists", "updated", "missing"]


@dataclass(frozen=True, slots=True)
class ProjectManifestCommandResult:
    """Project manifest command result."""

    project_key: str
    source_count: int
    row_id: str | None = None
    status: ProjectManifestCommandStatus = "created"


@asynccontextmanager
async def database_context() -> AsyncIterator[None]:
    """Initialize database access for the upload command."""

    await initialize_database(TORTOISE_ORM)
    try:
        yield
    finally:
        await close_database()


def _load_manifests(
    *,
    manifests_dir: Path,
    project_name: str | None,
    all_projects: bool,
) -> list[Manifest]:
    """Load manifests selected by command arguments."""

    manifest_root = manifests_dir.expanduser()
    if all_projects:
        manifests = list_project_manifests(manifest_root)
        if not manifests:
            raise typer.BadParameter(f"No project manifest files found in {manifest_root}.")
        return manifests

    if project_name is None:
        raise typer.BadParameter("Provide PROJECT_NAME or use --all.")

    manifest_path = manifest_root / f"{project_name}.json"
    if not manifest_path.exists():
        raise typer.BadParameter(f"Project manifest not found: {manifest_path}")
    return [load_project_manifest(manifest_root, project_name)]


def _create_payload(manifest: Manifest) -> ProjectManifestCreate:
    """Return DB create payload for one loaded manifest."""

    return ProjectManifestCreate(
        project_key=manifest.project_key,
        project_summary=manifest.project_summary,
        static_asset_paths=manifest.static_asset_paths,
        static_asset_extensions=manifest.static_asset_extensions,
        sources=[source.model_dump(mode="json") for source in manifest.sources],
    )


async def _update_payload(
    *,
    manifest: Manifest,
    service: ProjectManifestService,
) -> ProjectManifestUpdate:
    """Return DB update payload for one existing manifest."""

    existing = await service.get(manifest.project_key)
    return ProjectManifestUpdate(
        pk=existing.id,
        project_summary=manifest.project_summary,
        static_asset_paths=manifest.static_asset_paths,
        static_asset_extensions=manifest.static_asset_extensions,
        sources=[source.model_dump(mode="json") for source in manifest.sources],
    )


async def upload_manifests(
    *,
    manifests: list[Manifest],
    service: ProjectManifestService,
) -> list[ProjectManifestCommandResult]:
    """Create missing manifests and leave existing rows untouched."""

    results: list[ProjectManifestCommandResult] = []
    async with database_context():
        for manifest in manifests:
            if await service.exists(manifest.project_key):
                results.append(
                    ProjectManifestCommandResult(
                        project_key=manifest.project_key,
                        source_count=len(manifest.sources),
                        status="exists",
                    )
                )
                continue

            row: ProjectManifest = await service.create(_create_payload(manifest))
            results.append(
                ProjectManifestCommandResult(
                    project_key=manifest.project_key,
                    source_count=len(manifest.sources),
                    row_id=str(row.id),
                )
            )
    return results


async def update_manifest(
    *,
    manifest: Manifest,
    service: ProjectManifestService,
) -> ProjectManifestCommandResult:
    """Update one existing manifest."""

    async with database_context():
        if not await service.exists(manifest.project_key):
            return ProjectManifestCommandResult(
                project_key=manifest.project_key,
                source_count=len(manifest.sources),
                status="missing",
            )
        row = await service.update(await _update_payload(manifest=manifest, service=service))
    return ProjectManifestCommandResult(
        project_key=manifest.project_key,
        source_count=len(manifest.sources),
        row_id=str(row.id),
        status="updated",
    )


async def update_manifests(
    *,
    manifests: list[Manifest],
    service: ProjectManifestService,
) -> list[ProjectManifestCommandResult]:
    """Update existing manifests and report missing rows."""

    results: list[ProjectManifestCommandResult] = []
    async with database_context():
        for manifest in manifests:
            if not await service.exists(manifest.project_key):
                results.append(
                    ProjectManifestCommandResult(
                        project_key=manifest.project_key,
                        source_count=len(manifest.sources),
                        status="missing",
                    )
                )
                continue
            row = await service.update(await _update_payload(manifest=manifest, service=service))
            results.append(
                ProjectManifestCommandResult(
                    project_key=manifest.project_key,
                    source_count=len(manifest.sources),
                    row_id=str(row.id),
                    status="updated",
                )
            )
    return results


def _echo_upload_results(results: list[ProjectManifestCommandResult]) -> None:
    """Print upload command results for created and untouched manifests."""

    created_count = sum(result.status == "created" for result in results)
    existing_count = sum(result.status == "exists" for result in results)
    for item in results:
        if item.status == "created":
            typer.echo(
                f"Created project manifest {item.project_key} "
                f"(sources: {item.source_count}, row_id: {item.row_id})"
            )
            continue
        typer.echo(
            f"Project manifest {item.project_key} already exists and was not changed. "
            f"To update it, run: uv run command update-project-manifest "
            f"--project {item.project_key}"
        )
    typer.echo(
        f"Upload summary: created {created_count}, already existing {existing_count}, "
        f"total {len(results)}."
    )


def _echo_update_results(results: list[ProjectManifestCommandResult]) -> None:
    """Print update command results for updated and missing manifests."""

    updated_count = sum(result.status == "updated" for result in results)
    missing_count = sum(result.status == "missing" for result in results)
    for item in results:
        if item.status == "updated":
            typer.echo(
                f"Updated project manifest {item.project_key} "
                f"(sources: {item.source_count}, row_id: {item.row_id})"
            )
            continue
        typer.echo(
            f"Project manifest {item.project_key} does not exist. "
            f"To create it, run: uv run command upload-project-manifest {item.project_key}"
        )
    typer.echo(
        f"Update summary: updated {updated_count}, missing {missing_count}, total {len(results)}."
    )


@async_
async def upload_project_manifest(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project key to load from <path>/<project>.json."),
    ] = None,
    all_projects: Annotated[
        bool,
        typer.Option("--all", help="Upload every manifest JSON file from --path."),
    ] = False,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help="Directory with project manifest JSON files. Defaults to PROJECT_MANIFESTS_PATH.",
        ),
    ] = None,
) -> None:
    """Upload one or all configured project manifests into the database."""

    if all_projects and project_name is not None:
        raise typer.BadParameter("Use either PROJECT_NAME or --all, not both.")

    manifests = _load_manifests(
        manifests_dir=path or settings.PROJECT_MANIFESTS_PATH,
        project_name=project_name,
        all_projects=all_projects,
    )
    results = await upload_manifests(
        manifests=manifests,
        service=ProjectManifestService(),
    )
    _echo_upload_results(results)


@async_
async def update_project_manifest(
    project_name: Annotated[
        str | None,
        typer.Option(
            "--project",
            help="Project key to update from <path>/<project>.json. Required unless --all is used.",
        ),
    ] = None,
    all_projects: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Update every manifest JSON file from --path instead of using --project.",
        ),
    ] = False,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help="Directory with project manifest JSON files. Defaults to PROJECT_MANIFESTS_PATH.",
        ),
    ] = None,
) -> None:
    """Update one or all configured project manifests in the database."""

    if all_projects and project_name is not None:
        raise typer.BadParameter("Use either --project or --all, not both.")
    if project_name is None and not all_projects:
        raise typer.BadParameter("Provide --project PROJECT_NAME or use --all.")

    manifests = _load_manifests(
        manifests_dir=path or settings.PROJECT_MANIFESTS_PATH,
        project_name=project_name,
        all_projects=all_projects,
    )
    results = await update_manifests(
        manifests=manifests,
        service=ProjectManifestService(),
    )
    _echo_update_results(results)
