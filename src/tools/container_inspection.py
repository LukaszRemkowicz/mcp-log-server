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

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent
from pydantic import BaseModel, ConfigDict

from app import mcp
from auth.scopes import CONTAINER_FILES_READ_SCOPE
from dependencies import get_settings_dependency
from manifests.models import SourceDefinition
from settings import Settings
from tools.utils import load_authorized_project_manifest, resolve_container_source_definition
from utils.container_inspection_commands import MAX_CONTAINER_FILE_BYTES, ContainerPathStat
from utils.container_inspection_commands import (
    list_container_directory as run_list_container_directory,
)
from utils.container_inspection_commands import read_container_file as run_read_container_file
from utils.container_inspection_commands import stat_container_path as run_stat_container_path
from utils.mcp_errors import (
    AgentToolErrorResult,
    build_agent_error_payload,
    build_agent_tool_error_result,
)


class ContainerPathMetadataPayload(BaseModel):
    """Describe one inspected file or directory inside an approved container.

    This is the shared path metadata shape used by all container-inspection
    responses. It keeps the public MCP contract consistent whether the caller
    is:

    - reading a file
    - statting a path
    - listing a directory
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    name: str
    is_dir: bool
    size: int
    mode: int
    modified_at: str | None

    def __getitem__(self, key: str) -> object:
        """Allow legacy dict-style reads while keeping a typed model contract."""

        return getattr(self, key)


class ReadContainerFilePayload(BaseModel):
    """Structured success payload returned by `read_container_file`.

    The tool returns both the inspected file metadata and the text content
    that was read from the container, plus a truncation flag when `max_bytes`
    limits the returned body.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["read_container_file"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    source_key: str
    container_name: str
    path: str
    max_bytes: int
    truncated: bool
    content: str
    file: ContainerPathMetadataPayload


class StatContainerPathPayload(BaseModel):
    """Structured success payload returned by `stat_container_path`.

    This is the lightest inspection response. It lets an agent verify whether
    a path exists and inspect its metadata without reading file contents or
    listing directory children.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["stat_container_path"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    source_key: str
    container_name: str
    path: str
    stat: ContainerPathMetadataPayload


class ListContainerDirectoryPayload(BaseModel):
    """Structured success payload returned by `list_container_directory`.

    The tool only returns immediate entries for one approved directory. It is
    intentionally not a recursive filesystem browser.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["list_container_directory"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    source_key: str
    container_name: str
    path: str
    truncated: bool
    entries: list[ContainerPathMetadataPayload]


@dataclass(frozen=True)
class _ContainerInspectionErrorRule:
    """Describe one ordered message-to-error mapping for inspection failures."""

    message_fragment: str
    error_code: str
    retry_tips: list[str]


_CONTAINER_INSPECTION_ERROR_RULES: tuple[_ContainerInspectionErrorRule, ...] = (
    _ContainerInspectionErrorRule(
        message_fragment="project_key claim",
        error_code="missing_project_key_claim",
        retry_tips=[
            "Retry with a JWT that includes the project_key claim for the monitored project.",
        ],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="authorized by the access token",
        error_code="project_access_mismatch",
        retry_tips=[
            "Retry with project_name equal to the project_key authorized by the current JWT.",
        ],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="No manifest file was found",
        error_code="unknown_project",
        retry_tips=[
            "Call list_projects to discover the project_name values currently available.",
            "Retry with one of the listed project names.",
        ],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="loaded manifest project_key",
        error_code="manifest_project_mismatch",
        retry_tips=[
            "Verify that the manifest filename and its project_key describe the same project.",
        ],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="source_key was not found",
        error_code="unknown_container_source_key",
        retry_tips=[
            "Retry with one of the docker source_keys returned by list_projects for this project.",
        ],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="only available for docker sources",
        error_code="container_source_type_mismatch",
        retry_tips=["Retry with a docker-backed source_key."],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="not enabled for the requested source",
        error_code="container_inspection_not_enabled",
        retry_tips=[
            "Retry with a source that exposes inspect_path_prefixes in the project manifest.",
        ],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="must be an absolute path",
        error_code="container_path_not_absolute",
        retry_tips=["Retry with an absolute container path like /app/VERSION."],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="parent directory traversal",
        error_code="container_path_parent_traversal",
        retry_tips=["Retry with a normalized path inside the allowed source prefix."],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="outside the manifest whitelist",
        error_code="container_path_not_allowed",
        retry_tips=[
            "Retry with a path under one of the manifest-approved path prefixes for this source.",
        ],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="Docker Engine API is not available",
        error_code="docker_api_unavailable",
        retry_tips=["Retry in a runtime where the Docker socket is mounted and reachable."],
    ),
    _ContainerInspectionErrorRule(
        message_fragment="was not found",
        error_code="container_path_not_found",
        retry_tips=["Retry with a different path under the allowed source prefixes."],
    ),
)


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


def _build_container_file_error_details(
    *,
    error_code: str,
    requested_project_name: str | None,
    source_key: str | None,
    path: str | None,
    access_token: AccessToken | None,
    settings: Settings,
) -> dict[str, Any] | None:
    """Build structured details for one normalized inspection error code."""

    if error_code == "project_access_mismatch":
        return {
            "requested_project_name": requested_project_name,
            "authorized_project_name": str(
                access_token.claims.get("project_key") if access_token is not None else ""
            ),
        }
    if error_code == "unknown_project":
        return {"requested_project_name": requested_project_name}
    if error_code == "manifest_project_mismatch":
        return {"manifests_dir": str(settings.manifest_path.parent)}
    if error_code in {
        "unknown_container_source_key",
        "container_source_type_mismatch",
        "container_inspection_not_enabled",
    }:
        return {"source_key": source_key}
    if error_code in {"container_path_not_absolute", "container_path_parent_traversal"}:
        return {"path": path}
    if error_code in {"container_path_not_allowed", "container_path_not_found"}:
        return {"source_key": source_key, "path": path}
    return None


def _classify_container_file_error(message: str) -> tuple[str, list[str]]:
    """Classify one inspection error message into a stable code and retry tips.

    The public MCP error contract is intentionally normalized, but the raw
    failures come from several helpers and runtime layers. This classifier
    keeps the mapping in one ordered table instead of a long imperative
    `elif` ladder.
    """

    for rule in _CONTAINER_INSPECTION_ERROR_RULES:
        if rule.message_fragment in message:
            return rule.error_code, rule.retry_tips
    return (
        "container_file_inspection_error",
        ["Review the tool arguments and retry with a valid source_key and path."],
    )


def _build_container_file_error_result(
    *,
    action: str,
    message: str,
    requested_project_name: str | None,
    source_key: str | None,
    path: str | None,
    access_token: AccessToken | None,
    settings: Settings,
    shape_defaults: dict[str, Any] | None = None,
) -> ToolResult:
    """Map one inspection failure into a stable, agent-facing MCP error result.

    The specialist inspection tools should keep a predictable response shape
    even on errors so that:

    - agents can branch on `isError`
    - terminal callers can still extract known fields safely
    - validation and authorization failures remain easy to understand

    This helper translates common validation and runtime failures into:

    - a stable `error_code`
    - retry guidance
    - optional structured details
    - shape defaults for the specific inspection action
    """

    error_code, retry_tips = _classify_container_file_error(message)
    details = _build_container_file_error_details(
        error_code=error_code,
        requested_project_name=requested_project_name,
        source_key=source_key,
        path=path,
        access_token=access_token,
        settings=settings,
    )

    payload = {
        "action": action,
        **(shape_defaults or {}),
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
        return _build_container_file_error_result(
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
        return _build_container_file_error_result(
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
        return _build_container_file_error_result(
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
