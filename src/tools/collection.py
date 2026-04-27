"""Deterministic MCP log-collection tools."""

from __future__ import annotations

import logging

from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.auth import AccessToken
from fastmcp.tools.base import ToolResult

from auth.scopes import LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE
from conf import settings as conf_settings
from dependencies import get_settings_dependency
from logging_config import get_logger
from manifests.loader import list_project_manifests
from manifests.models import SourceDefinition
from services import log_source_collection as log_source_collection_module
from services.log_collection import LogCollectionService
from services.log_snapshots import DEFAULT_GREP_MATCH_LIMIT, LogSnapshotService
from services.log_source_collection import LogSourceCollectionService
from settings import Settings
from tools.errors import build_collect_logs_error_result
from tools.models import (
    CollectedSourcePayload,
    CollectLogsPayload,
    GrepLogSnapshotPayload,
    ListLogSnapshotFilesPayload,
    ProjectListEntry,
    ReadLogSnapshotFilePayload,
    SnapshotWorkspace,
)
from tools.registry import workflow_discoverable_tool
from utils.log_preview import truncate_collected_sources_for_response, truncate_log_preview
from utils.log_snapshots import find_snapshot_file, resolve_snapshot_file_path
from utils.mcp_errors import build_agent_tool_error_result
from utils.types import JSONObject, JSONValue

logger: logging.Logger = get_logger("tools.collection")
docker = log_source_collection_module.docker

MAX_TAIL_LINES = 1000
MAX_INLINE_LOG_BYTES = 200_000
MAX_GREP_MATCHES = 500


def limit_tail_lines(tail_lines: int) -> int:
    """Keep collection size bounded for deterministic tool responses."""

    return LogSourceCollectionService(max_tail_lines=MAX_TAIL_LINES).limit_tail_lines(tail_lines)


def collect_source(
    definition: SourceDefinition,
    tail_lines: int | None,
    *,
    timestamps: bool,
    since: str | None,
    until: str | None,
) -> CollectedSourcePayload:
    """Collect one manifest source through the supported deterministic adapters."""

    return LogSourceCollectionService(max_tail_lines=MAX_TAIL_LINES).collect_source(
        definition,
        tail_lines,
        timestamps=timestamps,
        since=since,
        until=until,
    )


def build_collect_logs_payload(
    settings: Settings,
    access_token: AccessToken,
    *,
    requested_project_name: str | None,
    requested_source_keys: list[str] | None,
    workspace: SnapshotWorkspace,
    session_id: str | None = None,
    tail_lines: int | None,
    timestamps: bool,
    since: str | None,
    until: str | None,
) -> CollectLogsPayload:
    """Build the structured persisted collection payload for the current caller."""

    service = LogCollectionService(
        settings,
        access_token,
        snapshot_service=LogSnapshotService(settings, access_token),
        source_collector=collect_source,
        tail_line_limiter=limit_tail_lines,
        response_truncator=truncate_collected_sources_for_response,
    )
    return service.build_payload(
        requested_project_name=requested_project_name,
        requested_source_keys=requested_source_keys,
        workspace=workspace,
        session_id=session_id,
        tail_lines=tail_lines,
        timestamps=timestamps,
        since=since,
        until=until,
    )


def _build_snapshot_tool_error_result(
    *,
    error_code: str,
    message: str,
    retry_tips: list[str],
    details: JSONObject | None = None,
) -> ToolResult:
    """Return one agent-facing error result for snapshot read/search tools."""

    return build_agent_tool_error_result(
        error_code=error_code,
        message=message,
        retry_tips=retry_tips,
        details=details,
    )


@workflow_discoverable_tool(PROJECTS_READ_SCOPE)
def list_projects(
    settings: Settings = Depends(get_settings_dependency),
) -> list[ProjectListEntry]:
    """List projects currently available through bundled manifest files.

    This is the lightweight discovery entrypoint for project-scoped log tools.
    Callers use it to learn:

    - which `project_name` values currently exist
    - which source keys belong to each project
    - whether a project exposes docker-backed, file-backed, or mixed sources

    This tool intentionally returns only summary metadata, not the full raw
    manifest contents. Its main use cases are:

    - picking a valid `project_name` before calling `collect_logs`
    - discovering valid `source_keys` for later collection calls
    - understanding whether a project can be inspected through file sources,
      docker sources, or both
    """

    logger.info("tool call: list_projects")
    manifests = list_project_manifests(settings.MANIFEST_PATH.parent)
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


@workflow_discoverable_tool(
    LOGS_COLLECT_SCOPE,
    argument_default_overrides={"since": conf_settings.DEFAULT_LOG_WINDOW},
)
def collect_logs(
    project_name: str | None = None,
    source_keys: list[str] | None = None,
    workspace: SnapshotWorkspace = "workflow",
    session_id: str | None = None,
    tail_lines: int | None = None,
    timestamps: bool = False,
    since: str | None = conf_settings.DEFAULT_LOG_WINDOW,
    until: str | None = None,
    settings: Settings = Depends(get_settings_dependency),
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Collect deterministic logs for the caller's authorized project sources.

    This is the first step in the snapshot-based log workflow. It performs
    deterministic collection, persists a workspace snapshot, and returns both
    snapshot metadata and a small inline preview for immediate agent use.

    The response explicitly preserves what the caller asked for:

    - requested project name
    - requested source keys
    - resolved source keys from the configured manifest

    That makes project scoping and source resolution visible to the agent
    instead of forcing it to infer what happened from partial results.

    Whitelisted docker-log options are exposed directly as tool parameters:

    - optional `tail_lines`
    - `timestamps`
    - `since`
    - `until`

    Collections are now always persisted to a workspace-specific snapshot:

    - `workspace="workflow"` uses the stable daily workflow workspace and
      does not require `session_id`
    - `workspace="session"` requires an agent-chosen `session_id`
      and rewrites that session folder on each collection call

    Session id rule:

    - the agent must choose `session_id` itself when starting a new session
    - reuse the same `session_id` for later `collect_logs`,
      `list_log_snapshot_files`, `read_log_snapshot_file`, and
      `grep_log_snapshot` calls that should keep working on that same session
    - pick a different `session_id` only when starting a different analysis
      session

    Typical follow-up flow:

    1. call `collect_logs`
    2. inspect `logs_by_source` for the immediate preview
    3. call `list_log_snapshot_files` if you need the persisted file inventory
    4. call `read_log_snapshot_file` or `grep_log_snapshot` for deeper analysis

    If `since` is omitted, the tool uses `settings.DEFAULT_LOG_WINDOW`
    (normally `24h`) so workflow agents do not need to specify the daily time
    window explicitly unless they want a different range.

    If `tail_lines` is omitted, the server requests the full source output
    inside the selected time window where supported. Agents should prefer
    setting `tail_lines` when they do not need the full bounded history.

    This tool does not search log content. Follow-up content search should
    happen through `grep_log_snapshot`, which operates on the persisted files
    created by this collection step.
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
        ("tool call: collect_logs project_name=%s source_keys=%s workspace=%s session_id=%s"),
        project_name,
        source_keys,
        workspace,
        session_id,
    )
    effective_since = since if since is not None else settings.DEFAULT_LOG_WINDOW
    try:
        payload = build_collect_logs_payload(
            settings,
            access_token,
            requested_project_name=project_name,
            requested_source_keys=source_keys,
            workspace=workspace,
            session_id=session_id,
            tail_lines=tail_lines,
            timestamps=timestamps,
            since=effective_since,
            until=until,
        )
    except ValueError as error:
        return build_collect_logs_error_result(
            str(error),
            settings=settings,
            access_token=access_token,
            project_name=project_name,
            workspace=workspace,
            session_id=session_id,
        )

    return ToolResult(
        content=[],
        structured_content=payload.model_dump(mode="json"),
    )


@workflow_discoverable_tool(LOGS_COLLECT_SCOPE)
def list_log_snapshot_files(
    snapshot_id: str,
    workspace: SnapshotWorkspace = "workflow",
    project_name: str | None = None,
    settings: Settings = Depends(get_settings_dependency),
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """List saved log files for one persisted workflow or session snapshot.

    This is the snapshot inventory step after `collect_logs`. It does not read
    file bodies or search content. It only describes which persisted files
    exist inside one snapshot and how large they are.

    Use `snapshot_id="latest"` when the caller wants the newest workflow
    snapshot and does not need to pin a historical run.

    Use an explicit `snapshot_id` returned by `collect_logs` when the caller
    wants:

    - a stable archived workflow snapshot
    - one specific caller-owned session snapshot
    - repeatable multi-step analysis over the same persisted artifact

    Typical next steps after this tool:

    - `read_log_snapshot_file` for one concrete source file
    - `grep_log_snapshot` to search selected files in the same snapshot
    """

    if access_token is None:
        return build_agent_tool_error_result(
            error_code="missing_access_token",
            message="Authenticated access token is required to read log snapshots.",
            retry_tips=["Retry with a bearer JWT that includes the logs.collect scope."],
        )

    try:
        snapshot_service = LogSnapshotService(settings, access_token)
        context = snapshot_service.load_authorized_snapshot_context(
            project_name,
            workspace,
            snapshot_id,
        )
    except ValueError as error:
        return _build_snapshot_tool_error_result(
            error_code="log_snapshot_not_found",
            message=str(error),
            retry_tips=[
                "Retry with a snapshot_id returned by collect_logs for the authorized project.",
            ],
            details={
                "project_name": project_name,
                "workspace": workspace,
                "snapshot_id": snapshot_id,
            },
        )

    payload = ListLogSnapshotFilesPayload(
        action="list_log_snapshot_files",
        requested_project_name=project_name,
        authorized_project_name=context.authorized_project_name,
        effective_project_name=context.effective_project_name,
        workspace=workspace,
        snapshot_id=context.metadata.snapshot_id,
        snapshot_dir=str(context.snapshot_dir),
        metadata_file=str(context.snapshot_dir / "snapshot_metadata.json"),
        collected_at=context.metadata.collected_at,
        files=context.metadata.files,
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(LOGS_COLLECT_SCOPE)
def read_log_snapshot_file(
    snapshot_id: str,
    source_key: str,
    workspace: SnapshotWorkspace = "workflow",
    project_name: str | None = None,
    start_line: int | None = None,
    line_count: int | None = None,
    max_bytes: int = MAX_INLINE_LOG_BYTES,
    settings: Settings = Depends(get_settings_dependency),
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Read one saved log file from a persisted workflow or session snapshot.

    This is the direct file-content follow-up tool for one persisted snapshot
    entry. It is best used when the caller already knows which `source_key`
    file it wants to inspect, either from `collect_logs` or from
    `list_log_snapshot_files`.

    Use `snapshot_id="latest"` for the newest workflow snapshot when the agent
    does not need to pin a previous run.

    Use an explicit `snapshot_id` when the agent must keep reading the same
    archived workflow snapshot or the same caller-owned session snapshot across
    multiple steps.

    `start_line` and `line_count` can be used to read a smaller line-range
    chunk instead of the whole file. This is the main way to inspect very
    large logs incrementally after a grep result or earlier preview.

    `max_bytes` still limits the inline body returned to the caller. The
    persisted file itself remains unchanged on disk, and the response tells
    the caller whether the returned body was truncated.
    """

    if access_token is None:
        return build_agent_tool_error_result(
            error_code="missing_access_token",
            message="Authenticated access token is required to read log snapshots.",
            retry_tips=["Retry with a bearer JWT that includes the logs.collect scope."],
        )
    if max_bytes < 1:
        return _build_snapshot_tool_error_result(
            error_code="invalid_snapshot_read_limit",
            message="max_bytes must be a positive integer.",
            retry_tips=["Retry with max_bytes >= 1."],
        )
    if start_line is not None and start_line < 1:
        return _build_snapshot_tool_error_result(
            error_code="invalid_snapshot_read_range",
            message="start_line must be a positive integer.",
            retry_tips=["Retry with start_line >= 1."],
        )
    if line_count is not None and line_count < 1:
        return _build_snapshot_tool_error_result(
            error_code="invalid_snapshot_read_range",
            message="line_count must be a positive integer.",
            retry_tips=["Retry with line_count >= 1."],
        )

    try:
        snapshot_service = LogSnapshotService(settings, access_token)
        context = snapshot_service.load_authorized_snapshot_context(
            project_name,
            workspace,
            snapshot_id,
        )
        file_payload = find_snapshot_file(
            context.metadata,
            source_key=source_key,
        )
        file_path = resolve_snapshot_file_path(context.snapshot_dir, file_payload)
        full_content = file_path.read_text(encoding="utf-8", errors="replace")
        selected_content, effective_start_line, effective_line_count = _select_snapshot_read_chunk(
            full_content,
            start_line=start_line,
            line_count=line_count,
        )
        preview_content = truncate_log_preview(selected_content, max_bytes)
        truncated = preview_content != selected_content
    except ValueError as error:
        message = str(error)
        error_code = (
            "snapshot_source_key_not_found" if "source_key" in message else "log_snapshot_not_found"
        )
        return _build_snapshot_tool_error_result(
            error_code=error_code,
            message=message,
            retry_tips=[
                (
                    "Retry with a valid snapshot_id and source_key returned by "
                    "collect_logs or list_log_snapshot_files."
                ),
            ],
            details={
                "project_name": project_name,
                "workspace": workspace,
                "snapshot_id": snapshot_id,
                "source_key": source_key,
                "start_line": start_line,
                "line_count": line_count,
            },
        )

    payload = ReadLogSnapshotFilePayload(
        action="read_log_snapshot_file",
        requested_project_name=project_name,
        authorized_project_name=context.authorized_project_name,
        effective_project_name=context.effective_project_name,
        workspace=workspace,
        snapshot_id=context.metadata.snapshot_id,
        snapshot_dir=str(context.snapshot_dir),
        source_key=source_key,
        start_line=effective_start_line,
        line_count=effective_line_count,
        max_bytes=max_bytes,
        truncated=truncated,
        content=preview_content,
        file=file_payload,
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(LOGS_COLLECT_SCOPE)
def grep_log_snapshot(
    snapshot_id: str,
    grep: str,
    workspace: SnapshotWorkspace = "workflow",
    project_name: str | None = None,
    source_keys: list[str] | None = None,
    match_offset: int = 0,
    match_limit: int = DEFAULT_GREP_MATCH_LIMIT,
    settings: Settings = Depends(get_settings_dependency),
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Search one persisted workflow or session snapshot with controlled grep semantics.

    This is the snapshot search step for persisted logs. The caller provides a
    bounded text pattern through `grep`, and the server searches only the files
    that belong to the authorized snapshot.

    Use `snapshot_id="latest"` for ad-hoc searches over the newest workflow
    snapshot.

    Use an explicit `snapshot_id` when the agent must keep searching the same
    archived workflow snapshot or caller-owned session snapshot across
    multiple steps.

    If `source_keys` is omitted, the search covers every file in the snapshot.
    If `source_keys` is provided, the search is limited to that subset only.

    Use `match_offset` and `match_limit` to page through larger result sets in
    smaller windows. By default, the first configured grep page is returned.

    This tool is intended for:

    - finding matching lines across one persisted workflow snapshot
    - narrowing analysis before opening a full file with `read_log_snapshot_file`
    - reusing a stable snapshot id across repeated searches
    """

    if access_token is None:
        return build_agent_tool_error_result(
            error_code="missing_access_token",
            message="Authenticated access token is required to search log snapshots.",
            retry_tips=["Retry with a bearer JWT that includes the logs.collect scope."],
        )
    if not grep.strip():
        return _build_snapshot_tool_error_result(
            error_code="empty_grep_pattern",
            message="grep must be a non-empty string.",
            retry_tips=["Retry with grep set to the text pattern you want to search for."],
        )
    if match_offset < 0:
        return _build_snapshot_tool_error_result(
            error_code="invalid_match_window",
            message="match_offset must be greater than or equal to 0.",
            retry_tips=["Retry with match_offset set to 0 or a positive integer."],
        )
    if match_limit < 1 or match_limit > MAX_GREP_MATCHES:
        return _build_snapshot_tool_error_result(
            error_code="invalid_match_window",
            message=f"match_limit must be between 1 and {MAX_GREP_MATCHES}.",
            retry_tips=[
                f"Retry with match_limit set between 1 and {MAX_GREP_MATCHES}.",
            ],
        )

    try:
        snapshot_service = LogSnapshotService(settings, access_token)
        context = snapshot_service.load_authorized_snapshot_context(
            project_name,
            workspace,
            snapshot_id,
        )
        matches, total_match_count = snapshot_service.grep_snapshot(
            context.snapshot_dir,
            context.metadata,
            grep=grep,
            source_keys=source_keys,
            match_offset=match_offset,
            match_limit=match_limit,
        )
    except ValueError as error:
        message = str(error)
        error_code = (
            "snapshot_source_key_not_found"
            if "source_key" in message or "source_keys" in message
            else "log_snapshot_search_error"
        )
        source_keys_detail: list[JSONValue] = list(source_keys or [])
        details: JSONObject = {
            "project_name": project_name,
            "workspace": workspace,
            "snapshot_id": snapshot_id,
            "grep": grep,
            "source_keys": source_keys_detail,
        }
        return _build_snapshot_tool_error_result(
            error_code=error_code,
            message=message,
            retry_tips=[
                "Retry with a valid snapshot_id and grep pattern for the authorized project.",
            ],
            details=details,
        )

    matched_source_keys = sorted({match.source_key for match in matches})
    searched_source_keys = source_keys or [item.source_key for item in context.metadata.files]
    payload = GrepLogSnapshotPayload(
        action="grep_log_snapshot",
        requested_project_name=project_name,
        authorized_project_name=context.authorized_project_name,
        effective_project_name=context.effective_project_name,
        workspace=workspace,
        snapshot_id=context.metadata.snapshot_id,
        snapshot_dir=str(context.snapshot_dir),
        grep=grep,
        searched_source_keys=searched_source_keys,
        matched_source_keys=matched_source_keys,
        match_offset=match_offset,
        match_limit=match_limit,
        match_count=total_match_count,
        returned_match_count=len(matches),
        truncated=match_offset + len(matches) < total_match_count,
        matches=matches,
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


def _select_snapshot_read_chunk(
    full_content: str,
    *,
    start_line: int | None,
    line_count: int | None,
) -> tuple[str, int | None, int | None]:
    """Return one requested line-range chunk from a persisted snapshot file.

    This keeps large-log inspection incremental for agents: grep can narrow the
    candidate file or line range, and chunked reads can then fetch smaller,
    more targeted slices of the saved file instead of repeatedly returning the
    entire body.
    """

    lines = full_content.splitlines(keepends=True)
    if start_line is None and line_count is None:
        return full_content, 1 if lines else None, len(lines) if lines else 0

    effective_start_line = 1 if start_line is None else start_line
    if effective_start_line > len(lines) and lines:
        raise ValueError("Requested snapshot read range starts beyond the end of the file.")

    start_index = effective_start_line - 1
    end_index = None if line_count is None else start_index + line_count
    selected_lines = lines[start_index:end_index]
    return "".join(selected_lines), effective_start_line, len(selected_lines)
