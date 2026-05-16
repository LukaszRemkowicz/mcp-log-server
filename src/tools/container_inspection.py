"""Read-only MCP tools for manifest-whitelisted container inspection.

These tools are the specialist container-inspection surface for agents that
need to verify deployed project files or container runtime status inside
approved containers.

Important boundary:

- agents do not get arbitrary container exec
- agents do not get broad filesystem browsing
- agents choose a high-level inspection operation
- server code maps that operation to a fixed internal command set
- requested paths must stay inside manifest-approved prefixes for file tools

So this module is intentionally narrower than "run a command in a container".
It exposes only deterministic, read-only inspection primitives:

- `read_container_file`
- `list_container_directory`
- `inspect_containers_health`
- `inspect_container_detail`
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath

from fastmcp.server.auth import require_scopes
from fastmcp.tools.base import ToolResult

from app import mcp
from auth.scopes import CONTAINER_FILES_READ_SCOPE
from conf import settings
from decorators import project_authorized_tool
from logging_config import get_logger
from manifests.models import SourceDefinition
from services.docker_service import (
    MAX_CONTAINER_FILE_BYTES,
    ContainerDetail,
    ContainerHealth,
    ContainerPathStat,
    DockerService,
    DockerServiceError,
)
from services.project_manifest import ProjectManifestError, ProjectManifestService
from tools.agent_hints import (
    INSPECT_CONTAINER_DETAIL_TOOL_DESCRIPTION,
    INSPECT_CONTAINERS_HEALTH_TOOL_DESCRIPTION,
    LIST_CONTAINER_DIRECTORY_TOOL_DESCRIPTION,
    READ_CONTAINER_FILE_TOOL_DESCRIPTION,
    STAT_CONTAINER_PATH_TOOL_DESCRIPTION,
)
from tools.errors import build_container_inspection_error_result
from tools.models import (
    ContainerDetailMountPayload,
    ContainerDetailNetworkPayload,
    ContainerDetailPortPayload,
    ContainerHealthPayload,
    ContainerPathMetadataPayload,
    ContainerRestartPolicyPayload,
    InspectContainerDetailPayload,
    InspectContainersHealthPayload,
    ListContainerDirectoryPayload,
    ReadContainerFilePayload,
    StatContainerPathPayload,
)

logger: logging.Logger = get_logger("tools.container_inspection")
manifest_service = ProjectManifestService()
docker_service = DockerService()


@dataclass(frozen=True, slots=True)
class ContainerInspectionContext:
    """Resolved project/source/path context shared by container inspection tools."""

    project_name: str
    definition: SourceDefinition
    normalized_path: str


@dataclass(frozen=True, slots=True)
class ContainerSourceContext:
    """Resolved project/source context for container-level inspection tools."""

    project_name: str
    definition: SourceDefinition


async def _prepare_container_source_context(
    *,
    action: str,
    project_name: str,
    source_key: str,
) -> ContainerSourceContext | ToolResult:
    """Resolve manifest and docker source for one container-level tool."""

    manifest_result = await manifest_service.get_or_error(project_name)
    if isinstance(manifest_result, ProjectManifestError):
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": action,
                "error_message": manifest_result.message,
                "source_key": source_key,
                "project_name": project_name,
            },
        )
        return build_container_inspection_error_result(
            action=action,
            message=manifest_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=None,
            settings=settings,
        )

    definition = manifest_service.get_container_source_or_error(
        manifest_result.manifest,
        source_key,
    )
    if isinstance(definition, ProjectManifestError):
        return build_container_inspection_error_result(
            action=action,
            message=definition.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=None,
            settings=settings,
        )

    return ContainerSourceContext(
        project_name=manifest_result.project_name,
        definition=definition,
    )


async def _prepare_container_inspection_context(
    *,
    action: str,
    project_name: str,
    source_key: str,
    path: str,
) -> ContainerInspectionContext | ToolResult:
    """Resolve manifest, container source, and allowed path for one inspection tool."""

    manifest_result = await manifest_service.get_or_error(project_name)
    if isinstance(manifest_result, ProjectManifestError):
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": action,
                "error_message": manifest_result.message,
                "source_key": source_key,
                "path": path,
                "project_name": project_name,
            },
        )
        return build_container_inspection_error_result(
            action=action,
            message=manifest_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    definition = manifest_service.get_container_source_or_error(
        manifest_result.manifest,
        source_key,
    )
    if isinstance(definition, ProjectManifestError):
        return build_container_inspection_error_result(
            action=action,
            message=definition.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    normalized_path = docker_service.normalize_container_path_or_error(path)
    if isinstance(normalized_path, DockerServiceError):
        return build_container_inspection_error_result(
            action=action,
            message=normalized_path.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    if not docker_service.container_path_is_allowed(definition, normalized_path):
        return build_container_inspection_error_result(
            action=action,
            message=(
                "Requested container path is outside the manifest whitelist "
                "for the selected source."
            ),
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    return ContainerInspectionContext(
        project_name=manifest_result.project_name,
        definition=definition,
        normalized_path=normalized_path,
    )


def create_container_payload(
    stat_payload: ContainerPathStat,
) -> ContainerPathMetadataPayload:
    """Convert one Docker path stat result into the MCP path metadata payload.

    `DockerService` returns infrastructure-focused `ContainerPathStat` objects.
    The MCP tool response uses `ContainerPathMetadataPayload`, so this adapter
    keeps that model conversion explicit at the tool boundary.
    """

    return ContainerPathMetadataPayload(
        path=stat_payload.path,
        name=PurePosixPath(stat_payload.path).name or stat_payload.path,
        is_dir=stat_payload.is_dir,
        size=stat_payload.size,
        mode=stat_payload.mode,
        modified_at=(
            str(stat_payload.modified_at) if stat_payload.modified_at is not None else None
        ),
    )


def create_container_health_item_payload(
    health: ContainerHealth,
    *,
    source_key: str,
) -> ContainerHealthPayload:
    """Convert Docker runtime state into one MCP health item."""

    return ContainerHealthPayload(
        source_key=source_key,
        inspection_status="ok",
        inspection_error=None,
        container_name=health.container_name,
        container_id=health.container_id,
        image=health.image,
        docker_status=health.docker_status,
        health_status=health.health_status,
        running=health.running,
        restarting=health.restarting,
        paused=health.paused,
        dead=health.dead,
        exit_code=health.exit_code,
        error=health.error,
        restart_count=health.restart_count,
        started_at=health.started_at,
        finished_at=health.finished_at,
    )


def create_container_health_error_item_payload(
    error: DockerServiceError,
    *,
    source_key: str,
    container_name: str,
) -> ContainerHealthPayload:
    """Represent one failed container health lookup without failing the whole overview."""

    return ContainerHealthPayload(
        source_key=source_key,
        inspection_status="error",
        inspection_error=error.message,
        container_name=container_name,
        container_id="",
        image=None,
        docker_status=None,
        health_status=None,
        running=False,
        restarting=False,
        paused=False,
        dead=False,
        exit_code=None,
        error=None,
        restart_count=None,
        started_at=None,
        finished_at=None,
    )


def create_container_detail_payload(
    detail: ContainerDetail,
    *,
    project_name: str,
    source_key: str,
) -> InspectContainerDetailPayload:
    """Convert curated Docker inspect metadata into the MCP detail response."""

    return InspectContainerDetailPayload(
        action="inspect_container_detail",
        project_name=project_name,
        source_key=source_key,
        container=create_container_health_item_payload(
            detail.health,
            source_key=source_key,
        ),
        created_at=detail.created_at,
        env_var_names=detail.env_var_names,
        label_keys=detail.label_keys,
        compose_labels=detail.compose_labels,
        restart_policy=ContainerRestartPolicyPayload(
            name=detail.restart_policy.name,
            maximum_retry_count=detail.restart_policy.maximum_retry_count,
        ),
        command=detail.command,
        entrypoint=detail.entrypoint,
        working_dir=detail.working_dir,
        user=detail.user,
        ports=[
            ContainerDetailPortPayload(
                private_port=item.private_port,
                host_ip=item.host_ip,
                host_port=item.host_port,
            )
            for item in detail.ports
        ],
        mounts=[
            ContainerDetailMountPayload(
                type=item.type,
                destination=item.destination,
                mode=item.mode,
                rw=item.rw,
            )
            for item in detail.mounts
        ],
        networks=[
            ContainerDetailNetworkPayload(
                name=item.name,
                ip_address=item.ip_address,
                aliases=item.aliases,
            )
            for item in detail.networks
        ],
        health_log=detail.health_log,
    )


@mcp.tool(
    auth=require_scopes(CONTAINER_FILES_READ_SCOPE),
    description=INSPECT_CONTAINERS_HEALTH_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def inspect_containers_health(
    project_name: str | None = None,
) -> ToolResult:
    """Return Docker runtime status for all manifest-approved source containers."""

    assert project_name is not None
    manifest_result = await manifest_service.get_or_error(project_name)
    if isinstance(manifest_result, ProjectManifestError):
        return build_container_inspection_error_result(
            action="inspect_containers_health",
            message=manifest_result.message,
            requested_project_name=project_name,
            source_key=None,
            path=None,
            settings=settings,
        )

    docker_sources = [
        source for source in manifest_result.manifest.sources if source.source_type == "docker"
    ]
    container_payloads: list[ContainerHealthPayload] = []
    for source in docker_sources:
        health = docker_service.inspect_container_health(source.target)
        if isinstance(health, DockerServiceError):
            container_payloads.append(
                create_container_health_error_item_payload(
                    health,
                    source_key=source.source_key,
                    container_name=source.target,
                )
            )
            continue
        container_payloads.append(
            create_container_health_item_payload(
                health,
                source_key=source.source_key,
            )
        )

    payload = InspectContainersHealthPayload(
        action="inspect_containers_health",
        project_name=manifest_result.project_name,
        resolved_source_keys=[source.source_key for source in docker_sources],
        containers=container_payloads,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_containers_health",
            "project_name": payload.project_name,
            "container_count": len(payload.containers),
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@mcp.tool(
    auth=require_scopes(CONTAINER_FILES_READ_SCOPE),
    description=INSPECT_CONTAINER_DETAIL_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def inspect_container_detail(
    source_key: str,
    project_name: str | None = None,
) -> ToolResult:
    """Return curated Docker inspect metadata for one manifest-approved container."""

    assert project_name is not None
    context = await _prepare_container_source_context(
        action="inspect_container_detail",
        project_name=project_name,
        source_key=source_key,
    )
    if isinstance(context, ToolResult):
        return context

    detail = docker_service.inspect_container_detail(context.definition.target)
    if isinstance(detail, DockerServiceError):
        return build_container_inspection_error_result(
            action="inspect_container_detail",
            message=detail.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=None,
            settings=settings,
        )

    payload = create_container_detail_payload(
        detail,
        project_name=context.project_name,
        source_key=context.definition.source_key,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_container_detail",
            "source_key": payload.source_key,
            "container_name": payload.container.container_name,
            "docker_status": payload.container.docker_status,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@mcp.tool(
    auth=require_scopes(CONTAINER_FILES_READ_SCOPE),
    description=STAT_CONTAINER_PATH_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def stat_container_path(
    source_key: str,
    path: str,
    project_name: str | None = None,
) -> ToolResult:
    """Return metadata for one approved container file or directory path."""

    assert project_name is not None
    context = await _prepare_container_inspection_context(
        action="stat_container_path",
        project_name=project_name,
        source_key=source_key,
        path=path,
    )
    if isinstance(context, ToolResult):
        return context

    stat_payload = docker_service.stat_container_path(
        context.definition.target,
        context.normalized_path,
    )
    if isinstance(stat_payload, DockerServiceError):
        return build_container_inspection_error_result(
            action="stat_container_path",
            message=stat_payload.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    payload = StatContainerPathPayload(
        action="stat_container_path",
        requested_project_name=project_name,
        project_name=context.project_name,
        source_key=context.definition.source_key,
        container_name=context.definition.target,
        path=context.normalized_path,
        file=create_container_payload(stat_payload),
    )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "stat_container_path",
            "source_key": payload.source_key,
            "container_name": payload.container_name,
            "path": payload.path,
            "is_dir": payload.file.is_dir,
            "size": payload.file.size,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@mcp.tool(
    auth=require_scopes(CONTAINER_FILES_READ_SCOPE),
    description=READ_CONTAINER_FILE_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def read_container_file(
    source_key: str,
    path: str,
    project_name: str | None = None,
    max_bytes: int = MAX_CONTAINER_FILE_BYTES,
) -> ToolResult:
    """Read one approved text file from a docker source container.

    Use this when an agent needs the actual file contents, for example to
    verify deployed application code or inspect a runtime config file.

    The tool is intentionally read-only and bounded:

    - it rejects directories
    - it enforces manifest-approved path prefixes
    - it limits returned bytes with `max_bytes`
    - it reports whether the returned content was truncated

    This keeps the public surface deterministic and safer than exposing
    arbitrary container exec to agents.
    """

    assert project_name is not None
    context = await _prepare_container_inspection_context(
        action="read_container_file",
        project_name=project_name,
        source_key=source_key,
        path=path,
    )
    if isinstance(context, ToolResult):
        return context

    stat_payload = docker_service.stat_container_path(
        context.definition.target,
        context.normalized_path,
    )
    if isinstance(stat_payload, DockerServiceError):
        return build_container_inspection_error_result(
            action="read_container_file",
            message=stat_payload.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    read_result = docker_service.read_container_file(
        context.definition.target,
        context.normalized_path,
        max_bytes=max_bytes,
    )
    if isinstance(read_result, DockerServiceError):
        return build_container_inspection_error_result(
            action="read_container_file",
            message=read_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )
    content, truncated = read_result
    payload = ReadContainerFilePayload(
        action="read_container_file",
        requested_project_name=project_name,
        project_name=context.project_name,
        source_key=context.definition.source_key,
        container_name=context.definition.target,
        path=context.normalized_path,
        max_bytes=max_bytes,
        truncated=truncated,
        content=content,
        file=create_container_payload(stat_payload),
    )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "read_container_file",
            "source_key": payload.source_key,
            "container_name": payload.container_name,
            "path": payload.path,
            "truncated": payload.truncated,
            "size": payload.file.size,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@mcp.tool(
    auth=require_scopes(CONTAINER_FILES_READ_SCOPE),
    description=LIST_CONTAINER_DIRECTORY_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def list_container_directory(
    source_key: str,
    path: str | None = None,
    project_name: str | None = None,
) -> ToolResult:
    """List files/directories inside an approved container directory.

    Use this as the navigation starting point for a source container. If
    `path` is omitted or blank, the tool lists the source's first
    manifest-approved inspection prefix, usually the main project folder such
    as `/app/`, similar to starting with `ls -la` in a terminal. After seeing
    that structure, pass an explicit child directory path to keep drilling down.

    The behavior is intentionally narrow:

    - only one directory at a time
    - only immediate children
    - no recursive crawl
    - same manifest/JWT/path safety checks as the other inspection tools
    """

    assert project_name is not None
    manifest_result = await manifest_service.get_or_error(project_name)
    if isinstance(manifest_result, ProjectManifestError):
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "list_container_directory",
                "error_message": manifest_result.message,
                "source_key": source_key,
                "path": path,
                "project_name": project_name,
            },
        )
        return build_container_inspection_error_result(
            action="list_container_directory",
            message=manifest_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )
    manifest = manifest_result.manifest
    project_name_value = manifest_result.project_name
    definition = manifest_service.get_container_source_or_error(manifest, source_key)
    if isinstance(definition, ProjectManifestError):
        return build_container_inspection_error_result(
            action="list_container_directory",
            message=definition.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    normalized_path = docker_service.resolve_container_directory_path_or_error(
        definition,
        path,
    )
    if isinstance(normalized_path, DockerServiceError):
        return build_container_inspection_error_result(
            action="list_container_directory",
            message=normalized_path.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    if not docker_service.container_path_is_allowed(definition, normalized_path):
        return build_container_inspection_error_result(
            action="list_container_directory",
            message=(
                "Requested container path is outside the manifest whitelist "
                "for the selected source."
            ),
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    list_result = docker_service.list_container_directory(
        definition.target,
        normalized_path,
    )
    if isinstance(list_result, DockerServiceError):
        return build_container_inspection_error_result(
            action="list_container_directory",
            message=list_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )
    entries, truncated = list_result
    payload = ListContainerDirectoryPayload(
        action="list_container_directory",
        requested_project_name=project_name,
        project_name=project_name_value,
        source_key=definition.source_key,
        container_name=definition.target,
        path=normalized_path,
        truncated=truncated,
        entries=[create_container_payload(entry) for entry in entries],
    )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "list_container_directory",
            "source_key": payload.source_key,
            "container_name": payload.container_name,
            "path": payload.path,
            "entry_count": len(payload.entries),
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
