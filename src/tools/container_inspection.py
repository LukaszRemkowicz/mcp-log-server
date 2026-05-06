"""Read-only MCP tools for manifest-whitelisted container file inspection.

These tools are the specialist container-inspection surface for agents that
need to verify deployed project files inside approved containers.

Important boundary:

- agents do not get arbitrary container exec
- agents do not get broad filesystem browsing
- agents choose a high-level inspection operation
- server code maps that operation to a fixed internal command set
- requested paths must stay inside manifest-approved prefixes

So this module is intentionally narrower than "run a command in a container".
It exposes only deterministic, read-only file inspection primitives:

- `read_container_file`
- `list_container_directory`
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.tools.base import ToolResult

from app import mcp
from auth.scopes import CONTAINER_FILES_READ_SCOPE
from conf import settings
from decorators import project_authorized_tool
from logging_config import get_logger
from manifests.models import SourceDefinition
from services.docker_service import (
    MAX_CONTAINER_FILE_BYTES,
    ContainerPathStat,
    DockerService,
    DockerServiceError,
)
from services.project_manifest import ProjectManifestError, ProjectManifestService
from tools.agent_hints import (
    LIST_CONTAINER_DIRECTORY_TOOL_DESCRIPTION,
    READ_CONTAINER_FILE_TOOL_DESCRIPTION,
)
from tools.errors import build_container_inspection_error_result
from tools.models import (
    ContainerPathMetadataPayload,
    ListContainerDirectoryPayload,
    ReadContainerFilePayload,
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


def _prepare_container_inspection_context(
    *,
    action: str,
    project_name: str,
    source_key: str,
    path: str,
    shape_defaults: dict[str, object],
    log_extra: dict[str, object],
) -> ContainerInspectionContext | ToolResult:
    """Resolve manifest, container source, and allowed path for one inspection tool."""

    manifest_result = manifest_service.get_or_error(project_name)
    if isinstance(manifest_result, ProjectManifestError):
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": action,
                "error_message": manifest_result.message,
                **log_extra,
            },
        )
        return build_container_inspection_error_result(
            action=action,
            message=manifest_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
            shape_defaults=shape_defaults,
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
            shape_defaults=shape_defaults,
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
            shape_defaults=shape_defaults,
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
            shape_defaults=shape_defaults,
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


@mcp.tool(
    auth=require_scopes(CONTAINER_FILES_READ_SCOPE),
    description=READ_CONTAINER_FILE_TOOL_DESCRIPTION,
)
@project_authorized_tool
def read_container_file(
    source_key: str,
    path: str,
    project_name: str | None = None,
    max_bytes: int = MAX_CONTAINER_FILE_BYTES,
    access_token: AccessToken | None = CurrentAccessToken(),
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

    assert access_token is not None
    assert project_name is not None
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "read_container_file",
            "source_key": source_key,
            "path": path,
            "project_name": project_name,
            "max_bytes": max_bytes,
        },
    )
    shape_defaults: dict[str, object] = {
        "requested_project_name": project_name,
        "source_key": source_key,
        "path": path,
        "max_bytes": max_bytes,
        "truncated": False,
        "content": "",
        "file": None,
    }
    context = _prepare_container_inspection_context(
        action="read_container_file",
        project_name=project_name,
        source_key=source_key,
        path=path,
        shape_defaults=shape_defaults,
        log_extra={
            "source_key": source_key,
            "path": path,
            "project_name": project_name,
            "max_bytes": max_bytes,
        },
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
            shape_defaults=shape_defaults,
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
            shape_defaults=shape_defaults,
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
def list_container_directory(
    source_key: str,
    path: str | None = None,
    project_name: str | None = None,
    access_token: AccessToken | None = CurrentAccessToken(),
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

    assert access_token is not None
    assert project_name is not None
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "list_container_directory",
            "source_key": source_key,
            "path": path,
            "project_name": project_name,
        },
    )
    shape_defaults: dict[str, object] = {
        "requested_project_name": project_name,
        "source_key": source_key,
        "path": path,
        "truncated": False,
        "entries": [],
    }
    manifest_result = manifest_service.get_or_error(project_name)
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
            shape_defaults=shape_defaults,
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
            shape_defaults=shape_defaults,
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
            shape_defaults=shape_defaults,
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
            shape_defaults=shape_defaults,
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
            shape_defaults=shape_defaults,
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
