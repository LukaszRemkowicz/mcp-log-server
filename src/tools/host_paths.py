"""Read-only MCP tools for manifest-whitelisted host path inspection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from fastmcp.server.dependencies import get_http_request
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent, ToolAnnotations

from auth.mcp_authorized_manifests import AuthorizedProjectManifests
from auth.scopes import CONTAINER_FILES_READ_SCOPE
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from manifests.models import Manifest, SourceDefinition
from services.host_path_service import (
    MAX_PROJECT_FILE_BYTES,
    HostPathMetadata,
    HostPathService,
    HostPathServiceError,
)
from services.project_manifest import ProjectManifestError
from tools.agent_hints import (
    LIST_PROJECT_DIRECTORY_TOOL_DESCRIPTION,
    READ_PROJECT_FILE_TOOL_DESCRIPTION,
    STAT_PROJECT_PATH_TOOL_DESCRIPTION,
)
from tools.models import (
    ListProjectDirectoryPayload,
    ProjectPathMetadataPayload,
    ReadProjectFilePayload,
    StatProjectPathPayload,
)
from utils.mcp_errors import AgentToolErrorResult, build_agent_error_payload
from utils.types import JSONObject

logger: logging.Logger = get_logger("tools.host_paths")
host_path_service = HostPathService()
READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
)


@dataclass(frozen=True, slots=True)
class HostPathContext:
    """Resolved project/source context for host path inspection tools."""

    project_name: str
    definition: SourceDefinition


def _get_authorized_manifest(project_name: str) -> Manifest | None:
    """Return one request-state manifest prepared by AuthorizedManifestsMiddleware."""

    request = get_http_request()
    authorized_manifests = cast(
        AuthorizedProjectManifests,
        request.state.authorized_manifests,
    )
    return authorized_manifests.manifests.get(project_name)


def _host_path_error_result(
    *,
    action: str,
    message: str,
    requested_project_name: str | None,
    source_key: str | None,
    path: str | None,
) -> ToolResult:
    """Return a stable host path inspection error payload."""

    error_code = "project_file_inspection_error"
    retry_tips = ["Review the tool arguments and retry with a valid file source_key and path."]
    details: JSONObject | None = None
    if "No persisted manifest" in message:
        error_code = "unknown_project"
        retry_tips = [
            "Call list_projects to discover the project_name values currently available.",
            "Retry with one of the listed project names.",
        ]
        details = {"requested_project_name": requested_project_name}
    elif "source_key was not found" in message:
        error_code = "unknown_project_file_source_key"
        retry_tips = ["Retry with one of the file source_keys returned by list_projects."]
        details = {"source_key": source_key}
    elif "only available for file sources" in message:
        error_code = "project_source_type_mismatch"
        retry_tips = ["Retry with a file-backed source_key."]
        details = {"source_key": source_key}
    elif "must be an absolute path" in message:
        error_code = "project_path_not_absolute"
        retry_tips = ["Retry with an absolute host path inside the selected source allowlist."]
        details = {"path": path}
    elif "parent directory traversal" in message:
        error_code = "project_path_parent_traversal"
        retry_tips = ["Retry with a normalized path inside the selected source allowlist."]
        details = {"path": path}
    elif "outside the manifest whitelist" in message:
        error_code = "project_path_not_allowed"
        retry_tips = [
            "Retry with the source target, its parent directory, or an approved inspect prefix.",
        ]
        details = {"source_key": source_key, "path": path}
    elif "symlink resolves outside" in message:
        error_code = "project_path_symlink_escape"
        retry_tips = ["Retry with a path that does not resolve outside the approved source root."]
        details = {"source_key": source_key, "path": path}
    elif "was not found" in message:
        error_code = "project_path_not_found"
        retry_tips = ["Retry with a different path under the approved source root."]
        details = {"source_key": source_key, "path": path}
    elif "not a directory" in message:
        error_code = "project_path_not_directory"
        retry_tips = ["Retry with a directory path under the approved source root."]
        details = {"source_key": source_key, "path": path}
    elif "not a file" in message:
        error_code = "project_path_not_file"
        retry_tips = ["Retry with a file path under the approved source root."]
        details = {"source_key": source_key, "path": path}

    payload = {
        "action": action,
        **build_agent_error_payload(
            error_code=error_code,
            message=message,
            retry_tips=retry_tips,
            details=details,
        ),
    }
    return AgentToolErrorResult(
        content=[TextContent(type="text", text=message)],
        structured_content=payload,
    )


def _build_unknown_project_manifest_error(project_name: str) -> ProjectManifestError:
    """Return the standard missing-manifest error for this tool module."""

    return ProjectManifestError(
        message=(
            f"Unknown project {project_name!r}. No persisted manifest was found for that project."
        )
    )


async def _prepare_host_path_context(
    *,
    action: str,
    project_name: str,
    source_key: str,
    path: str | None,
) -> HostPathContext | ToolResult:
    """Resolve authorized manifest and file source for one host path tool."""

    manifest = _get_authorized_manifest(project_name)
    if manifest is None:
        manifest_error = _build_unknown_project_manifest_error(project_name)
        return _host_path_error_result(
            action=action,
            message=manifest_error.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
        )

    definition = next(
        (source for source in manifest.sources if source.source_key == source_key),
        None,
    )
    if definition is None:
        return _host_path_error_result(
            action=action,
            message="Requested source_key was not found in the configured manifest.",
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
        )
    if definition.source_type != "file":
        return _host_path_error_result(
            action=action,
            message="Project host path inspection is only available for file sources.",
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
        )

    return HostPathContext(project_name=manifest.project_key, definition=definition)


def _project_path_payload(metadata: HostPathMetadata) -> ProjectPathMetadataPayload:
    """Convert host path service metadata into an MCP response item."""

    return ProjectPathMetadataPayload(
        path=metadata.path,
        name=metadata.name,
        exists=metadata.exists,
        is_file=metadata.is_file,
        is_dir=metadata.is_dir,
        is_symlink=metadata.is_symlink,
        size=metadata.size,
        mode=metadata.mode,
        modified_at=metadata.modified_at,
        uid=metadata.uid,
        gid=metadata.gid,
        readable=metadata.readable,
        symlink_target=metadata.symlink_target,
    )


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=STAT_PROJECT_PATH_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def stat_project_path(
    source_key: str,
    path: str | None = None,
    project_name: str | None = None,
) -> ToolResult:
    """Return metadata for one approved host file or directory path."""

    assert project_name is not None
    context = await _prepare_host_path_context(
        action="stat_project_path",
        project_name=project_name,
        source_key=source_key,
        path=path,
    )
    if isinstance(context, ToolResult):
        return context

    stat_result = host_path_service.stat_project_path(context.definition, path)
    if isinstance(stat_result, HostPathServiceError):
        return _host_path_error_result(
            action="stat_project_path",
            message=stat_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
        )

    payload = StatProjectPathPayload(
        action="stat_project_path",
        requested_project_name=project_name,
        project_name=context.project_name,
        source_key=context.definition.source_key,
        path=stat_result.path,
        file=_project_path_payload(stat_result),
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "stat_project_path",
            "project_name": payload.project_name,
            "source_key": payload.source_key,
            "path": payload.path,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=READ_PROJECT_FILE_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def read_project_file(
    source_key: str,
    path: str | None = None,
    project_name: str | None = None,
    max_bytes: int = MAX_PROJECT_FILE_BYTES,
) -> ToolResult:
    """Read one approved host file with a bounded byte limit."""

    assert project_name is not None
    context = await _prepare_host_path_context(
        action="read_project_file",
        project_name=project_name,
        source_key=source_key,
        path=path,
    )
    if isinstance(context, ToolResult):
        return context

    stat_result = host_path_service.stat_project_path(context.definition, path)
    if isinstance(stat_result, HostPathServiceError):
        return _host_path_error_result(
            action="read_project_file",
            message=stat_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
        )

    read_result = host_path_service.read_project_file(
        context.definition,
        path,
        max_bytes=max_bytes,
    )
    if isinstance(read_result, HostPathServiceError):
        return _host_path_error_result(
            action="read_project_file",
            message=read_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
        )
    content, truncated = read_result
    payload = ReadProjectFilePayload(
        action="read_project_file",
        requested_project_name=project_name,
        project_name=context.project_name,
        source_key=context.definition.source_key,
        path=stat_result.path,
        max_bytes=max_bytes,
        truncated=truncated,
        content=content,
        file=_project_path_payload(stat_result),
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "read_project_file",
            "project_name": payload.project_name,
            "source_key": payload.source_key,
            "path": payload.path,
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=LIST_PROJECT_DIRECTORY_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def list_project_directory(
    source_key: str,
    path: str | None = None,
    project_name: str | None = None,
) -> ToolResult:
    """List one approved host directory without recursion."""

    assert project_name is not None
    context = await _prepare_host_path_context(
        action="list_project_directory",
        project_name=project_name,
        source_key=source_key,
        path=path,
    )
    if isinstance(context, ToolResult):
        return context

    list_result = host_path_service.list_project_directory(context.definition, path)
    if isinstance(list_result, HostPathServiceError):
        return _host_path_error_result(
            action="list_project_directory",
            message=list_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
        )
    entries, truncated = list_result
    payload = ListProjectDirectoryPayload(
        action="list_project_directory",
        requested_project_name=project_name,
        project_name=context.project_name,
        source_key=context.definition.source_key,
        path=Path(path).as_posix() if path else Path(context.definition.target).parent.as_posix(),
        truncated=truncated,
        entries=[_project_path_payload(entry) for entry in entries],
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "list_project_directory",
            "project_name": payload.project_name,
            "source_key": payload.source_key,
            "path": payload.path,
            "entry_count": len(payload.entries),
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
