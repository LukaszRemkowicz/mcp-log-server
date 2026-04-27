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

- `stat_container_path`
- `read_container_file`
- `list_container_directory`
"""

from __future__ import annotations

from pathlib import PurePosixPath

from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.tools.base import ToolResult

from app import mcp
from auth.scopes import CONTAINER_FILES_READ_SCOPE
from dependencies import get_settings_dependency
from manifests.models import SourceDefinition
from settings import Settings
from tools.errors import build_container_file_error_result
from tools.models import (
    ContainerPathMetadataPayload,
    ListContainerDirectoryPayload,
    ReadContainerFilePayload,
    StatContainerPathPayload,
)
from tools.utils import load_authorized_project_manifest, resolve_container_source_definition
from utils.container_inspection_commands import MAX_CONTAINER_FILE_BYTES, ContainerPathStat
from utils.container_inspection_commands import (
    list_container_directory as run_list_container_directory,
)
from utils.container_inspection_commands import read_container_file as run_read_container_file
from utils.container_inspection_commands import stat_container_path as run_stat_container_path
from utils.mcp_errors import build_agent_tool_error_result


def _normalize_container_path(path: str) -> str:
    """Normalize one requested container path into a safe absolute POSIX path.

    Container inspection requests should operate on one explicit path, not an
    ambiguous or shell-like string. This helper enforces the minimum path
    contract before whitelist checks run:

    - path must be absolute
    - path may not contain `..`
    - path is normalized as a POSIX path string

    The parent-traversal rejection matters because an agent may otherwise try
    to escape an allowed prefix like `/app/` with a path such as
    `/app/../etc/passwd`.
    """

    stripped_path = path.strip()
    if not stripped_path.startswith("/"):
        raise ValueError("Container inspection path must be an absolute path.")

    raw_path = PurePosixPath(stripped_path)
    if ".." in raw_path.parts:
        raise ValueError("Container inspection path may not include parent directory traversal.")

    normalized_path = str(raw_path)
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    return normalized_path


def _container_path_is_allowed(definition: SourceDefinition, path: str) -> bool:
    """Return whether the requested path stays inside the manifest whitelist.

    Each docker-backed source can expose `inspect_path_prefixes` in the
    manifest. Those prefixes are the real filesystem boundary for specialist
    inspection tools.

    This helper answers the core policy question for the public tools:

    - is the normalized requested path exactly an allowed prefix?
    - or is it a child of one allowed prefix?

    Anything outside those approved roots must be rejected before internal
    container commands are executed.
    """

    normalized_path = _normalize_container_path(path)
    for prefix in definition.inspect_path_prefixes:
        normalized_prefix = _normalize_container_path(prefix)
        if normalized_path == normalized_prefix:
            return True
        if normalized_path.startswith(f"{normalized_prefix.rstrip('/')}/"):
            return True
    return False


def _build_path_metadata(
    *,
    path: str,
    is_dir: bool,
    size: int,
    mode: int,
    modified_at: str | None,
) -> ContainerPathMetadataPayload:
    """Convert one internal stat result into the public MCP metadata model."""

    return ContainerPathMetadataPayload(
        path=path,
        name=PurePosixPath(path).name or path,
        is_dir=is_dir,
        size=size,
        mode=mode,
        modified_at=str(modified_at) if modified_at is not None else None,
    )


def _metadata_from_stat(stat_payload: ContainerPathStat) -> ContainerPathMetadataPayload:
    """Convert one internal command result into public path metadata.

    The command wrapper layer returns strongly typed stat objects. This helper
    adapts those internal results into the Pydantic payload model shared by
    the MCP response surface.
    """

    return _build_path_metadata(
        path=stat_payload.path,
        is_dir=stat_payload.is_dir,
        size=stat_payload.size,
        mode=stat_payload.mode,
        modified_at=stat_payload.modified_at,
    )


@mcp.tool(auth=require_scopes(CONTAINER_FILES_READ_SCOPE))
def stat_container_path(
    source_key: str,
    path: str,
    project_name: str | None = None,
    settings: Settings = Depends(get_settings_dependency),
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Return metadata for one approved path inside a docker source container.

    Use this when an agent needs to verify that a path exists and inspect its
    basic metadata without fetching file contents.

    Public arguments:

    - `source_key`: manifest container alias such as `backend` or `nginx`
    - `path`: absolute filesystem path inside that container
    - `project_name`: optional explicit project override, still constrained by
      the JWT `project_key`

    Safety model:

    - JWT must include `container.files.read`
    - source must be docker-backed
    - source must expose `inspect_path_prefixes`
    - path must be absolute and free of parent traversal
    - path must stay inside a manifest-approved prefix
    - internal execution uses only the approved inspection command wrapper
    """

    if access_token is None:
        return build_agent_tool_error_result(
            error_code="missing_access_token",
            message="Authenticated access token is required to inspect container files.",
            retry_tips=[
                "Retry with a bearer JWT that includes the container.files.read scope.",
            ],
        )

    try:
        manifest, authorized_project_name, effective_project_name = (
            load_authorized_project_manifest(
                settings,
                access_token,
                project_name,
            )
        )
        definition = resolve_container_source_definition(manifest, source_key)
        normalized_path = _normalize_container_path(path)
        if not _container_path_is_allowed(definition, normalized_path):
            raise ValueError(
                "Requested container path is outside the manifest whitelist "
                "for the selected source."
            )
        stat_payload = run_stat_container_path(definition.target, normalized_path)
        payload = StatContainerPathPayload(
            action="stat_container_path",
            requested_project_name=project_name,
            authorized_project_name=authorized_project_name,
            effective_project_name=effective_project_name,
            source_key=definition.source_key,
            container_name=definition.target,
            path=normalized_path,
            stat=_metadata_from_stat(stat_payload),
        )
    except ValueError as error:
        return build_container_file_error_result(
            action="stat_container_path",
            message=str(error),
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            access_token=access_token,
            settings=settings,
            shape_defaults={
                "requested_project_name": project_name,
                "source_key": source_key,
                "path": path,
                "stat": None,
            },
        )

    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@mcp.tool(auth=require_scopes(CONTAINER_FILES_READ_SCOPE))
def read_container_file(
    source_key: str,
    path: str,
    project_name: str | None = None,
    max_bytes: int = MAX_CONTAINER_FILE_BYTES,
    settings: Settings = Depends(get_settings_dependency),
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

    if access_token is None:
        return build_agent_tool_error_result(
            error_code="missing_access_token",
            message="Authenticated access token is required to inspect container files.",
            retry_tips=[
                "Retry with a bearer JWT that includes the container.files.read scope.",
            ],
        )

    try:
        manifest, authorized_project_name, effective_project_name = (
            load_authorized_project_manifest(
                settings,
                access_token,
                project_name,
            )
        )
        definition = resolve_container_source_definition(manifest, source_key)
        normalized_path = _normalize_container_path(path)
        if not _container_path_is_allowed(definition, normalized_path):
            raise ValueError(
                "Requested container path is outside the manifest whitelist "
                "for the selected source."
            )
        if max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer.")
        stat_payload = run_stat_container_path(definition.target, normalized_path)
        file_metadata = _metadata_from_stat(stat_payload)
        if file_metadata.is_dir:
            raise ValueError(
                "Requested container path is a directory, not a readable regular file."
            )
        content, truncated = run_read_container_file(
            definition.target,
            normalized_path,
            max_bytes=max_bytes,
        )
        payload = ReadContainerFilePayload(
            action="read_container_file",
            requested_project_name=project_name,
            authorized_project_name=authorized_project_name,
            effective_project_name=effective_project_name,
            source_key=definition.source_key,
            container_name=definition.target,
            path=normalized_path,
            max_bytes=max_bytes,
            truncated=truncated,
            content=content,
            file=file_metadata,
        )
    except ValueError as error:
        return build_container_file_error_result(
            action="read_container_file",
            message=str(error),
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            access_token=access_token,
            settings=settings,
            shape_defaults={
                "requested_project_name": project_name,
                "source_key": source_key,
                "path": path,
                "max_bytes": max_bytes,
                "truncated": False,
                "content": "",
                "file": None,
            },
        )

    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@mcp.tool(auth=require_scopes(CONTAINER_FILES_READ_SCOPE))
def list_container_directory(
    source_key: str,
    path: str,
    project_name: str | None = None,
    settings: Settings = Depends(get_settings_dependency),
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """List immediate entries under one approved directory in a docker source.

    Use this when an agent needs to discover which files or subdirectories are
    available inside an allowed root before requesting a specific file.

    The behavior is intentionally narrow:

    - only one directory at a time
    - only immediate children
    - no recursive crawl
    - same manifest/JWT/path safety checks as the other inspection tools
    """

    if access_token is None:
        return build_agent_tool_error_result(
            error_code="missing_access_token",
            message="Authenticated access token is required to inspect container files.",
            retry_tips=[
                "Retry with a bearer JWT that includes the container.files.read scope.",
            ],
        )

    try:
        manifest, authorized_project_name, effective_project_name = (
            load_authorized_project_manifest(
                settings,
                access_token,
                project_name,
            )
        )
        definition = resolve_container_source_definition(manifest, source_key)
        normalized_path = _normalize_container_path(path)
        if not _container_path_is_allowed(definition, normalized_path):
            raise ValueError(
                "Requested container path is outside the manifest whitelist "
                "for the selected source."
            )
        directory_metadata = _metadata_from_stat(
            run_stat_container_path(definition.target, normalized_path)
        )
        if not directory_metadata.is_dir:
            raise ValueError("Requested container path is not a directory.")
        entries, truncated = run_list_container_directory(
            definition.target,
            normalized_path,
        )
        payload = ListContainerDirectoryPayload(
            action="list_container_directory",
            requested_project_name=project_name,
            authorized_project_name=authorized_project_name,
            effective_project_name=effective_project_name,
            source_key=definition.source_key,
            container_name=definition.target,
            path=normalized_path,
            truncated=truncated,
            entries=[_metadata_from_stat(entry) for entry in entries],
        )
    except ValueError as error:
        return build_container_file_error_result(
            action="list_container_directory",
            message=str(error),
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            access_token=access_token,
            settings=settings,
            shape_defaults={
                "requested_project_name": project_name,
                "source_key": source_key,
                "path": path,
                "truncated": False,
                "entries": [],
            },
        )

    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
