"""Upload project manifest files into the database."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import typer

from database.config import TORTOISE_ORM
from database.lifecycle import close_database, initialize_database
from database.models import ProjectManifest
from database.schemas import ProjectManifestCreate, ProjectManifestUpdate
from database.services.project_manifests import ProjectManifestService
from decorators import async_
from manifests.loader import list_project_manifests, load_project_manifest
from manifests.models import Manifest
from scripts.docker_commands import DockerCommandService

# Values are injected by docker-compose.yml and docker-compose.prod.yml on the app service.
# They must match the Compose project name and the `services.app` key.
COMMANDS_COMPOSE_PROJECT_NAME = os.environ.get(
    "COMMANDS_COMPOSE_PROJECT_NAME",
    "mcp-log-server",
)
COMMANDS_APP_SERVICE = os.environ.get(
    "COMMANDS_APP_SERVICE",
    "app",
)
CONTAINER_MANIFESTS_DIR = "/tmp/mcp-log-server-manifests"

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
            f"To update it, run: uv run commands update-project-manifest "
            f"--project {item.project_key}"
        )
    typer.echo(
        f"Upload summary: created {created_count}, already existing {existing_count}, "
        f"total {len(results)}."
    )


@async_
async def upload_project_manifest_internal(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project key to load from <path>/<project>.json."),
    ] = None,
    all_projects: Annotated[
        bool,
        typer.Option("--all", help="Upload every manifest JSON file from --path."),
    ] = False,
    path: Annotated[
        Path,
        typer.Option("--path", help="Directory with project manifest JSON files."),
    ] = Path("."),
) -> None:
    """Upload one or all configured project manifests into the database."""

    if all_projects and project_name is not None:
        raise typer.BadParameter("Use either PROJECT_NAME or --all, not both.")

    manifests = _load_manifests(
        manifests_dir=path,
        project_name=project_name,
        all_projects=all_projects,
    )
    results = await upload_manifests(
        manifests=manifests,
        service=ProjectManifestService(),
    )
    _echo_upload_results(results)


@async_
async def update_project_manifest_internal(
    project_name: Annotated[
        str,
        typer.Option("--project", help="Project key to update from <path>/<project>.json."),
    ],
    path: Annotated[
        Path,
        typer.Option("--path", help="Directory with project manifest JSON files."),
    ] = Path("."),
) -> None:
    """Update one configured project manifest in the database."""

    manifest = _load_manifests(
        manifests_dir=path,
        project_name=project_name,
        all_projects=False,
    )[0]
    result = await update_manifest(
        manifest=manifest,
        service=ProjectManifestService(),
    )
    if result.status == "missing":
        typer.echo(
            f"Project manifest {result.project_key} does not exist. "
            f"To create it, run: uv run commands upload-project-manifest {result.project_key}"
        )
        return
    typer.echo(
        f"Updated project manifest {result.project_key} "
        f"(sources: {result.source_count}, row_id: {result.row_id})"
    )


def _run_internal_manifest_command(
    command: list[str],
    *,
    manifest_files: list[Path] | None = None,
) -> None:
    """Run one hidden manifest command inside the Docker Compose app service."""

    docker_service = DockerCommandService()
    try:
        if manifest_files is not None:
            docker_service.copy_files_to_compose_service(
                project_name=COMMANDS_COMPOSE_PROJECT_NAME,
                service_name=COMMANDS_APP_SERVICE,
                files=manifest_files,
                target_dir=CONTAINER_MANIFESTS_DIR,
            )
        result = docker_service.run_compose_service_command(
            project_name=COMMANDS_COMPOSE_PROJECT_NAME,
            service_name=COMMANDS_APP_SERVICE,
            command=command,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    if result.output:
        typer.echo(result.output.rstrip())
    if result.exit_code != 0:
        raise typer.Exit(result.exit_code)


def _manifest_files_for_container_copy(
    *,
    manifests_dir: Path,
    project_name: str | None,
    all_projects: bool,
) -> list[Path]:
    """Validate selected manifests and return matching JSON files for container copy."""

    manifests = _load_manifests(
        manifests_dir=manifests_dir,
        project_name=project_name,
        all_projects=all_projects,
    )
    manifest_root = manifests_dir.expanduser()
    return [manifest_root / f"{manifest.project_key}.json" for manifest in manifests]


def upload_project_manifest(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project key to load from <path>/<project>.json."),
    ] = None,
    all_projects: Annotated[
        bool,
        typer.Option("--all", help="Upload every manifest JSON file from --path."),
    ] = False,
    path: Annotated[
        Path,
        typer.Option("--path", help="Directory with project manifest JSON files."),
    ] = Path("."),
) -> None:
    """Run the manifest upload command inside the Docker Compose app service."""

    if all_projects and project_name is not None:
        raise typer.BadParameter("Use either PROJECT_NAME or --all, not both.")
    if project_name is None and not all_projects:
        raise typer.BadParameter("Provide PROJECT_NAME or use --all.")

    manifest_files = _manifest_files_for_container_copy(
        manifests_dir=path,
        project_name=project_name,
        all_projects=all_projects,
    )
    command = ["uv", "run", "python", "-m", "scripts.main", "upload-project-manifest-internal"]
    command.extend(["--path", CONTAINER_MANIFESTS_DIR])
    if all_projects:
        command.append("--all")
    elif project_name is not None:
        command.append(project_name)
    _run_internal_manifest_command(command, manifest_files=manifest_files)


def update_project_manifest(
    project_name: Annotated[
        str,
        typer.Option("--project", help="Project key to update from <path>/<project>.json."),
    ],
    path: Annotated[
        Path,
        typer.Option("--path", help="Directory with project manifest JSON files."),
    ] = Path("."),
) -> None:
    """Run the manifest update command inside the Docker Compose app service."""

    manifest_files = _manifest_files_for_container_copy(
        manifests_dir=path,
        project_name=project_name,
        all_projects=False,
    )
    _run_internal_manifest_command(
        [
            "uv",
            "run",
            "python",
            "-m",
            "scripts.main",
            "update-project-manifest-internal",
            "--path",
            CONTAINER_MANIFESTS_DIR,
            "--project",
            project_name,
        ],
        manifest_files=manifest_files,
    )
