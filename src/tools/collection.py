"""Deterministic MCP log-collection tools."""

from __future__ import annotations

import logging
import re
import shutil
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from docker.errors import APIError, DockerException
from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.auth import AccessToken
from fastmcp.tools.base import ToolResult
from pydantic import BaseModel, ConfigDict
from requests import exceptions as requests_exceptions

import docker
from auth.scopes import LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE
from dependencies import get_settings_dependency
from logging_config import get_logger
from manifests.loader import list_project_manifests, load_project_manifest
from manifests.models import SourceDefinition, SourceManifest
from settings import Settings
from tools.registry import workflow_discoverable_tool
from utils.mcp_errors import build_agent_tool_error_result

logger: logging.Logger = get_logger("tools.collection")

MAX_TAIL_LINES = 1000
MAX_UNBOUNDED_FILE_BYTES = 1_000_000
DOCKER_LOG_TIMEOUT_SECONDS = 15
DOCKER_DURATION_PATTERN = re.compile(r"^(?P<value>\d+)(?P<unit>[smhd])$")


class CollectedSourcePayload(BaseModel):
    """One deterministic collection result for a requested manifest source."""

    model_config = ConfigDict(extra="forbid")

    source_key: str
    source_type: Literal["docker", "file"]
    target: str
    description: str
    stream: Literal["stdout", "stderr"] | None
    status: Literal["collected", "unavailable"]
    line_count: int
    content: str
    output_file: str | None
    error: str | None
    retry_tips: list[str]

    def __getitem__(self, key: str) -> object:
        """Allow legacy dict-style reads while keeping a typed model contract."""

        return getattr(self, key)


class CollectLogsPayload(BaseModel):
    """Structured response returned by `collect_logs`.

    The payload makes the agent-visible request context explicit:

    - which project the caller requested
    - which project the caller is authorized for
    - which sources the caller requested
    - which sources were actually resolved from the manifest
    - deterministic per-source collection results
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["collect_logs"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    requested_source_keys: list[str]
    save_to_files: bool
    requested_tail_lines: int | None
    effective_tail_lines: int | None
    requested_timestamps: bool
    requested_since: str | None
    requested_until: str | None
    tail_lines_limited: bool
    warnings: list[str]
    retry_tips: list[str]
    unknown_requested_source_keys: list[str]
    resolved_source_keys: list[str]
    logs_by_source: dict[str, str]
    project_output_dir: str | None
    latest_output_dir: str | None
    archive_dir: str | None
    collected_at: str
    collected_at_file: str | None
    sources: list[CollectedSourcePayload]

    def __getitem__(self, key: str) -> object:
        """Allow legacy dict-style reads while keeping a typed model contract."""

        return getattr(self, key)


class ProjectListEntry(BaseModel):
    """Describe one project currently available through bundled manifests."""

    model_config = ConfigDict(extra="forbid")

    project_name: str
    project_summary: str
    manifest_file: str
    source_keys: list[str]
    source_types: list[str]
    file_sources_available: bool
    docker_sources_available: bool

    def __getitem__(self, key: str) -> object:
        """Allow legacy dict-style reads while keeping a typed model contract."""

        return getattr(self, key)


def limit_tail_lines(tail_lines: int) -> int:
    """Keep collection size bounded for deterministic tool responses."""

    return max(1, min(tail_lines, MAX_TAIL_LINES))


def _read_full_file(path: Path) -> str:
    """Read the full contents of a file-backed source."""

    return path.read_text(encoding="utf-8", errors="replace")


def _read_file_tail(path: Path, tail_lines: int) -> str:
    """Read the last `tail_lines` lines from a file-backed source."""

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return "\n".join(deque((line.rstrip("\n") for line in handle), maxlen=tail_lines))


def _normalize_docker_time_filter(value: str | None) -> datetime | int | None:
    """Convert agent-facing docker time filters into Docker SDK supported values."""

    if value is None:
        return None

    stripped_value = value.strip()
    if not stripped_value:
        return None

    if stripped_value.isdigit():
        return int(stripped_value)

    duration_match = DOCKER_DURATION_PATTERN.fullmatch(stripped_value)
    if duration_match is not None:
        duration_value = int(duration_match.group("value"))
        duration_unit = duration_match.group("unit")
        duration_kwargs = {
            "s": {"seconds": duration_value},
            "m": {"minutes": duration_value},
            "h": {"hours": duration_value},
            "d": {"days": duration_value},
        }
        return datetime.now(UTC) - timedelta(**duration_kwargs[duration_unit])

    normalized_iso_value = stripped_value.replace("Z", "+00:00")
    try:
        parsed_datetime = datetime.fromisoformat(normalized_iso_value)
    except ValueError as error:
        raise ValueError(
            f"Invalid docker time filter {value!r}. Use an ISO-8601 timestamp, "
            "unix seconds, or a duration like 30m, 1h, or 1d."
        ) from error

    if parsed_datetime.tzinfo is None:
        return parsed_datetime.replace(tzinfo=UTC)
    return parsed_datetime


def _collect_file_source(
    definition: SourceDefinition,
    tail_lines: int | None,
) -> CollectedSourcePayload:
    """Collect one file-backed source from the path declared in the manifest."""

    path = Path(definition.target)
    if not path.exists():
        return CollectedSourcePayload(
            source_key=definition.source_key,
            source_type=definition.source_type,
            target=definition.target,
            description=definition.description,
            stream=definition.stream,
            status="unavailable",
            line_count=0,
            content="",
            output_file=None,
            error=f"File source not found: {definition.target}",
            retry_tips=["Verify the file path in the manifest or retry with a different source."],
        )

    if tail_lines is None:
        if path.stat().st_size > MAX_UNBOUNDED_FILE_BYTES:
            return CollectedSourcePayload(
                source_key=definition.source_key,
                source_type=definition.source_type,
                target=definition.target,
                description=definition.description,
                stream=definition.stream,
                status="unavailable",
                line_count=0,
                content="",
                output_file=None,
                error=(
                    f"File source is too large for unbounded collection: {definition.target}. "
                    "Retry with tail_lines to limit the returned output."
                ),
                retry_tips=[
                    f"Retry with tail_lines <= {MAX_TAIL_LINES} to keep file output bounded."
                ],
            )
        content = _read_full_file(path)
    else:
        content = _read_file_tail(path, tail_lines)
    line_count = 0 if not content else len(content.splitlines())
    return CollectedSourcePayload(
        source_key=definition.source_key,
        source_type=definition.source_type,
        target=definition.target,
        description=definition.description,
        stream=definition.stream,
        status="collected",
        line_count=line_count,
        content=content,
        output_file=None,
        error=None,
        retry_tips=[],
    )


def _collect_docker_source(
    definition: SourceDefinition,
    tail_lines: int | None,
    *,
    timestamps: bool,
    since: str | None,
    until: str | None,
) -> CollectedSourcePayload:
    """Collect one docker-backed source through the Docker Engine API."""

    logs_kwargs: dict[str, bool | int | str | datetime] = {
        "timestamps": timestamps,
        "stdout": True,
        "stderr": True,
    }
    if tail_lines is not None:
        logs_kwargs["tail"] = tail_lines
    normalized_since = _normalize_docker_time_filter(since)
    normalized_until = _normalize_docker_time_filter(until)
    if normalized_since is not None:
        logs_kwargs["since"] = normalized_since
    if normalized_until is not None:
        logs_kwargs["until"] = normalized_until

    try:
        client = cast(Any, docker).from_env(timeout=DOCKER_LOG_TIMEOUT_SECONDS)
        content_bytes = client.containers.get(definition.target).logs(**logs_kwargs)
    except APIError as error:
        error_output = str(error).strip() or "Unknown docker error."
        return CollectedSourcePayload(
            source_key=definition.source_key,
            source_type=definition.source_type,
            target=definition.target,
            description=definition.description,
            stream=definition.stream,
            status="unavailable",
            line_count=0,
            content="",
            output_file=None,
            error=error_output,
            retry_tips=[
                "Verify the container name in the manifest or retry with a different source."
            ],
        )
    except requests_exceptions.Timeout:
        retry_tips = []
        error_message = f"Timed out collecting docker logs for {definition.target}."
        if tail_lines is None:
            error_message += " Retry with tail_lines to limit the requested log output."
            retry_tips.append(
                f"Retry with tail_lines <= {MAX_TAIL_LINES} to keep docker log output bounded."
            )
        return CollectedSourcePayload(
            source_key=definition.source_key,
            source_type=definition.source_type,
            target=definition.target,
            description=definition.description,
            stream=definition.stream,
            status="unavailable",
            line_count=0,
            content="",
            output_file=None,
            error=error_message,
            retry_tips=retry_tips,
        )
    except DockerException:
        return CollectedSourcePayload(
            source_key=definition.source_key,
            source_type=definition.source_type,
            target=definition.target,
            description=definition.description,
            stream=definition.stream,
            status="unavailable",
            line_count=0,
            content="",
            output_file=None,
            error="Docker Engine API is not available in the current runtime.",
            retry_tips=["Retry in a runtime where the Docker socket is mounted and reachable."],
        )

    content = content_bytes.decode("utf-8", errors="replace").strip()
    return CollectedSourcePayload(
        source_key=definition.source_key,
        source_type=definition.source_type,
        target=definition.target,
        description=definition.description,
        stream=definition.stream,
        status="collected",
        line_count=0 if not content else len(content.splitlines()),
        content=content,
        output_file=None,
        error=None,
        retry_tips=[],
    )


def collect_source(
    definition: SourceDefinition,
    tail_lines: int | None,
    *,
    timestamps: bool,
    since: str | None,
    until: str | None,
) -> CollectedSourcePayload:
    """Collect one manifest source through the supported deterministic adapters."""

    if definition.source_type == "file":
        return _collect_file_source(definition, tail_lines)
    return _collect_docker_source(
        definition,
        tail_lines,
        timestamps=timestamps,
        since=since,
        until=until,
    )


def prepare_project_snapshot_dirs(logs_dir: Path, project_key: str) -> tuple[Path, Path, Path]:
    """Prepare `<logs_root>/<project>/latest` and archive the previous latest snapshot."""

    project_output_dir = logs_dir / project_key
    latest_output_dir = project_output_dir / "latest"
    archive_dir = project_output_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    existing_snapshot_paths = (
        list(latest_output_dir.glob("*")) if latest_output_dir.exists() else []
    )
    if existing_snapshot_paths:
        archive_snapshot_dir = archive_dir / datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
        archive_snapshot_dir.mkdir(parents=True, exist_ok=True)
        for snapshot_path in existing_snapshot_paths:
            shutil.move(str(snapshot_path), archive_snapshot_dir / snapshot_path.name)

    latest_output_dir.mkdir(parents=True, exist_ok=True)
    return project_output_dir, latest_output_dir, archive_dir


def write_collection_outputs(
    latest_output_dir: Path,
    collected_sources: list[CollectedSourcePayload],
) -> tuple[str, str]:
    """Persist collected sources into the project's latest snapshot directory."""

    collected_at = datetime.now(UTC).isoformat()

    for source in collected_sources:
        output_file = latest_output_dir / f"{source.source_key}.log"
        output_file.write_text(source.content, encoding="utf-8")
        source.output_file = str(output_file)

    collected_at_file = latest_output_dir / "collected_at.txt"
    collected_at_file.write_text(collected_at, encoding="utf-8")
    return collected_at, str(collected_at_file)


def resolve_manifest_sources(
    manifest: SourceManifest,
    requested_source_keys: list[str] | None,
) -> tuple[list[SourceDefinition], list[str], list[str]]:
    """Resolve requested manifest source keys into concrete source definitions."""

    if requested_source_keys is None:
        return list(manifest.sources), [], [source.source_key for source in manifest.sources]

    requested_lookup = set(requested_source_keys)
    resolved_sources = [
        source for source in manifest.sources if source.source_key in requested_lookup
    ]
    resolved_source_keys = [source.source_key for source in resolved_sources]
    unknown_source_keys = [key for key in requested_source_keys if key not in resolved_source_keys]
    return resolved_sources, unknown_source_keys, resolved_source_keys


def build_collect_logs_payload(
    settings: Settings,
    access_token: AccessToken,
    *,
    requested_project_name: str | None,
    requested_source_keys: list[str] | None,
    save_to_files: bool,
    tail_lines: int | None,
    timestamps: bool,
    since: str | None,
    until: str | None,
) -> CollectLogsPayload:
    """Build the structured collection payload for the current caller and manifest."""

    authorized_project_name = str(access_token.claims.get("project_key") or "").strip()
    if not authorized_project_name:
        raise ValueError("Authenticated access token must include a project_key claim.")

    effective_project_name = requested_project_name or authorized_project_name
    if effective_project_name != authorized_project_name:
        raise ValueError(
            "Requested project key does not match the project_key authorized by the access token."
        )

    manifests_dir = settings.manifest_path.parent
    try:
        manifest = load_project_manifest(manifests_dir, effective_project_name)
    except FileNotFoundError as error:
        raise ValueError(
            f"Unknown project {effective_project_name!r}. No manifest file was "
            "found for that project."
        ) from error
    if manifest.project_key != effective_project_name:
        raise ValueError("Requested project key does not match the loaded manifest project_key.")

    bounded_tail_lines = None if tail_lines is None else limit_tail_lines(tail_lines)
    resolved_sources, unknown_source_keys, resolved_source_keys = resolve_manifest_sources(
        manifest,
        requested_source_keys,
    )
    warnings: list[str] = []
    retry_tips: list[str] = []

    tail_lines_limited = bounded_tail_lines != tail_lines
    if tail_lines_limited:
        warnings.append(
            f"Requested tail_lines={tail_lines} exceeded the server limit of {MAX_TAIL_LINES}. "
            f"Using {bounded_tail_lines} instead."
        )
        retry_tips.append(
            f"Retry with tail_lines <= {MAX_TAIL_LINES} to avoid server-side limiting."
        )
    if tail_lines is None:
        warnings.append(
            "No tail_lines value was provided. Full source output will be "
            "requested where supported."
        )
        retry_tips.append(
            "Retry with tail_lines to keep docker and file collection bounded "
            "if a source is slow or large."
        )

    if unknown_source_keys:
        warnings.append(
            "Some requested source_keys were not found in the configured manifest: "
            + ", ".join(unknown_source_keys)
            + "."
        )
        retry_tips.append(
            "Retry with only source_keys returned by the manifest-backed project configuration."
        )

    collected_sources = [
        collect_source(
            source,
            bounded_tail_lines,
            timestamps=timestamps,
            since=since,
            until=until,
        )
        for source in resolved_sources
        if source.source_type == "docker" or source.source_type == "file"
    ]

    project_output_dir: str | None = None
    latest_output_dir: str | None = None
    archive_dir: str | None = None
    collected_at = datetime.now(UTC).isoformat()
    collected_at_file: str | None = None
    if save_to_files:
        project_output_path, latest_output_path, archive_path = prepare_project_snapshot_dirs(
            settings.logs_dir,
            effective_project_name,
        )
        collected_at, collected_at_file = write_collection_outputs(
            latest_output_path,
            collected_sources,
        )
        project_output_dir = str(project_output_path)
        latest_output_dir = str(latest_output_path)
        archive_dir = str(archive_path)

    logs_by_source = {
        source.source_key: source.content for source in collected_sources if source.content
    }

    return CollectLogsPayload(
        action="collect_logs",
        requested_project_name=requested_project_name,
        authorized_project_name=authorized_project_name,
        effective_project_name=effective_project_name,
        requested_source_keys=requested_source_keys or [],
        save_to_files=save_to_files,
        requested_tail_lines=tail_lines,
        effective_tail_lines=bounded_tail_lines,
        requested_timestamps=timestamps,
        requested_since=since,
        requested_until=until,
        tail_lines_limited=tail_lines_limited,
        warnings=warnings,
        retry_tips=retry_tips,
        unknown_requested_source_keys=unknown_source_keys,
        resolved_source_keys=resolved_source_keys,
        logs_by_source=logs_by_source,
        project_output_dir=project_output_dir,
        latest_output_dir=latest_output_dir,
        archive_dir=archive_dir,
        collected_at=collected_at,
        collected_at_file=collected_at_file,
        sources=collected_sources,
    )


@workflow_discoverable_tool(PROJECTS_READ_SCOPE)
def list_projects(
    settings: Settings = Depends(get_settings_dependency),
) -> list[ProjectListEntry]:
    """List projects currently available through bundled manifest files."""

    logger.info("tool call: list_projects")
    manifests = list_project_manifests(settings.manifest_path.parent)
    return [
        ProjectListEntry(
            project_name=manifest.project_key,
            project_summary=manifest.project_summary,
            manifest_file=f"{manifest.project_key}.json",
            source_keys=[source.source_key for source in manifest.sources],
            source_types=sorted({source.source_type for source in manifest.sources}),
            file_sources_available=any(source.source_type == "file" for source in manifest.sources),
            docker_sources_available=any(
                source.source_type == "docker" for source in manifest.sources
            ),
        )
        for manifest in manifests
    ]


@workflow_discoverable_tool(LOGS_COLLECT_SCOPE)
def collect_logs(
    project_name: str | None = None,
    source_keys: list[str] | None = None,
    save_to_files: bool = False,
    tail_lines: int | None = None,
    timestamps: bool = False,
    since: str | None = None,
    until: str | None = None,
    settings: Settings = Depends(get_settings_dependency),
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Collect deterministic logs for the caller's authorized project sources.

    The response explicitly preserves what the caller asked for:

    - requested project name
    - requested source keys
    - resolved source keys from the configured manifest

    This gives agents enough context to understand which resources they asked
    for, which project they are allowed to access, and which deterministic
    collection results were actually produced.

    Whitelisted docker-log options are exposed directly as tool parameters:

    - optional `tail_lines`
    - `timestamps`
    - `since`
    - `until`

    `save_to_files=True` persists the collected snapshot under the configured
    logs root. `save_to_files=False` keeps the collection in memory only.

    If `tail_lines` is omitted, the server requests full source output where
    supported. Agents should prefer setting `tail_lines` when they do not need
    the full log history.
    """

    if access_token is None:
        return build_agent_tool_error_result(
            error_code="missing_access_token",
            message="Authenticated access token is required to collect logs.",
            retry_tips=[
                "Retry with a bearer JWT that includes the logs.collect scope.",
                "Call tools/list first for the current token if you are unsure "
                "which tools are available.",
            ],
        )

    logger.info(
        "tool call: collect_logs project_name=%s source_keys=%s save_to_files=%s",
        project_name,
        source_keys,
        save_to_files,
    )
    try:
        payload = build_collect_logs_payload(
            settings,
            access_token,
            requested_project_name=project_name,
            requested_source_keys=source_keys,
            save_to_files=save_to_files,
            tail_lines=tail_lines,
            timestamps=timestamps,
            since=since,
            until=until,
        )
    except ValueError as error:
        message = str(error)
        if "project_key claim" in message:
            return build_agent_tool_error_result(
                error_code="missing_project_key_claim",
                message=message,
                retry_tips=[
                    "Retry with a JWT that includes the project_key claim for "
                    "the monitored project.",
                    "Use get_mcp_service_status to inspect the current caller context if needed.",
                ],
            )
        if "authorized by the access token" in message:
            return build_agent_tool_error_result(
                error_code="project_access_mismatch",
                message=message,
                retry_tips=[
                    "Retry with project_name equal to the project_key "
                    "authorized by the current JWT.",
                    "Use get_mcp_service_status to confirm the current "
                    "project_key before retrying.",
                ],
                details={
                    "requested_project_name": project_name,
                    "authorized_project_name": str(access_token.claims.get("project_key") or ""),
                },
            )
        if "No manifest file was found" in message:
            return build_agent_tool_error_result(
                error_code="unknown_project",
                message=message,
                retry_tips=[
                    "Call list_projects to discover the project_name values currently available.",
                    "Retry with one of the listed project names.",
                ],
                details={
                    "requested_project_name": project_name,
                    "manifests_dir": str(settings.manifest_path.parent),
                },
            )
        if "loaded manifest project_key" in message:
            return build_agent_tool_error_result(
                error_code="manifest_project_mismatch",
                message=message,
                retry_tips=[
                    "Verify that the manifest filename and its project_key "
                    "describe the same project.",
                    "Retry only after fixing the inconsistent manifest configuration.",
                ],
                details={
                    "requested_project_name": project_name,
                    "manifests_dir": str(settings.manifest_path.parent),
                },
            )
        if "Invalid docker time filter" in message:
            return build_agent_tool_error_result(
                error_code="invalid_docker_time_filter",
                message=message,
                retry_tips=[
                    "Retry with since/until as ISO-8601, unix seconds, or a "
                    "duration like 30m, 1h, or 1d.",
                    "Omit since/until if you want the current default collection range.",
                ],
            )
        return build_agent_tool_error_result(
            error_code="collect_logs_validation_error",
            message=message,
            retry_tips=[
                "Review the collect_logs arguments and retry with a valid "
                "project_name and source_keys.",
            ],
        )

    return ToolResult(
        content=[],
        structured_content=payload.model_dump(mode="json"),
    )
