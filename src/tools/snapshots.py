"""Snapshot inventory, read, and grep MCP tools."""

from __future__ import annotations

import logging

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken
from fastmcp.tools.base import ToolResult

from auth.scopes import LOGS_COLLECT_SCOPE
from conf import settings
from logging_config import get_logger
from services.log_snapshots import DEFAULT_GREP_MATCH_LIMIT, LogSnapshotService
from tools.agent_hints import (
    GREP_SNAPSHOT_NEXT_STEP_TIPS,
    LIST_SNAPSHOT_NEXT_STEP_TIPS,
    READ_SNAPSHOT_NEXT_STEP_TIPS,
)
from tools.models import (
    GrepLogSnapshotPayload,
    ListLogSnapshotFilesPayload,
    ReadLogSnapshotFilePayload,
)
from tools.registry import workflow_discoverable_tool
from utils.log_preview import truncate_log_preview
from utils.log_snapshots import (
    build_snapshot_tool_error_result,
    find_snapshot_file,
    resolve_snapshot_context_or_error,
    resolve_snapshot_file_path,
    select_snapshot_read_chunk,
)
from utils.types import JSONValue

logger: logging.Logger = get_logger("tools.snapshots")

MAX_INLINE_LOG_BYTES = 200_000
MAX_GREP_MATCHES = 500


@workflow_discoverable_tool(LOGS_COLLECT_SCOPE)
def list_log_snapshot_files(
    snapshot_id: str,
    project_name: str | None = None,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """List saved log files for one persisted snapshot.

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

    assert access_token is not None
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "list_log_snapshot_files",
            "snapshot_id": snapshot_id,
            "project_name": project_name,
        },
    )
    context, error_result = resolve_snapshot_context_or_error(
        access_token=access_token,
        project_name=project_name,
        snapshot_id=snapshot_id,
        default_error_code="log_snapshot_not_found",
        invalid_retry_tips=[
            "Retry with a snapshot_id returned by collect_logs for the authorized project.",
        ],
        details={
            "project_name": project_name,
            "snapshot_id": snapshot_id,
        },
        logger=logger,
        tool_name="list_log_snapshot_files",
    )
    if error_result is not None:
        return error_result
    assert context is not None

    payload = ListLogSnapshotFilesPayload(
        action="list_log_snapshot_files",
        requested_project_name=project_name,
        authorized_project_name=context.authorized_project_name,
        effective_project_name=context.effective_project_name,
        workspace=context.metadata.workspace,
        snapshot_id=context.metadata.snapshot_id,
        snapshot_dir=str(context.snapshot_dir),
        metadata_file=str(context.snapshot_dir / "snapshot_metadata.json"),
        collected_at=context.metadata.collected_at,
        next_step_tips=LIST_SNAPSHOT_NEXT_STEP_TIPS,
        files=context.metadata.files,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "list_log_snapshot_files",
            "snapshot_id": payload.snapshot_id,
            "workspace": payload.workspace,
            "file_count": len(payload.files),
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(LOGS_COLLECT_SCOPE)
def read_log_snapshot_file(
    snapshot_id: str,
    source_key: str,
    project_name: str | None = None,
    start_line: int | None = None,
    line_count: int | None = None,
    max_bytes: int = MAX_INLINE_LOG_BYTES,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Read one saved log file from a persisted snapshot.

    This is the direct file-content follow-up tool for one persisted snapshot
    entry. It is best used when the caller already knows which `source_key`
    file it wants to inspect, either from `collect_logs` or from
    `list_log_snapshot_files`.

    Use `snapshot_id="latest"` for the newest workflow snapshot when the agent
    does not need to pin a previous run. Use an explicit `snapshot_id` when
    the agent must keep reading the same persisted snapshot across multiple steps.

    `start_line` and `line_count` can be used to read a smaller line-range
    chunk instead of the whole file. This is the main way to inspect very
    large logs incrementally after a grep result or earlier preview.

    `max_bytes` still limits the inline body returned to the caller. The
    persisted file itself remains unchanged on disk, and the response tells
    the caller whether the returned body was truncated.
    """

    assert access_token is not None
    if max_bytes < 1:
        return build_snapshot_tool_error_result(
            error_code="invalid_snapshot_read_limit",
            message="max_bytes must be a positive integer.",
            retry_tips=["Retry with max_bytes >= 1."],
        )
    if start_line is not None and start_line < 1:
        return build_snapshot_tool_error_result(
            error_code="invalid_snapshot_read_range",
            message="start_line must be a positive integer.",
            retry_tips=["Retry with start_line >= 1."],
        )
    if line_count is not None and line_count < 1:
        return build_snapshot_tool_error_result(
            error_code="invalid_snapshot_read_range",
            message="line_count must be a positive integer.",
            retry_tips=["Retry with line_count >= 1."],
        )

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "read_log_snapshot_file",
            "snapshot_id": snapshot_id,
            "source_key": source_key,
            "project_name": project_name,
            "start_line": start_line,
            "line_count": line_count,
            "max_bytes": max_bytes,
        },
    )
    context, error_result = resolve_snapshot_context_or_error(
        access_token=access_token,
        project_name=project_name,
        snapshot_id=snapshot_id,
        default_error_code="log_snapshot_not_found",
        invalid_retry_tips=[
            (
                "Retry with a valid snapshot_id and source_key returned by "
                "collect_logs or list_log_snapshot_files."
            ),
        ],
        details={
            "project_name": project_name,
            "snapshot_id": snapshot_id,
            "source_key": source_key,
            "start_line": start_line,
            "line_count": line_count,
        },
        logger=logger,
        tool_name="read_log_snapshot_file",
        log_context={"source_key": source_key},
    )
    if error_result is not None:
        return error_result
    assert context is not None

    try:
        file_payload = find_snapshot_file(
            context.metadata,
            source_key=source_key,
        )
        file_path = resolve_snapshot_file_path(context.snapshot_dir, file_payload)
        full_content = file_path.read_text(encoding="utf-8", errors="replace")
        selected_content, effective_start_line, effective_line_count = select_snapshot_read_chunk(
            full_content,
            start_line=start_line,
            line_count=line_count,
        )
        preview_content = truncate_log_preview(selected_content, max_bytes)
        truncated = preview_content != selected_content
    except ValueError as error:
        message = str(error)
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "read_log_snapshot_file",
                "error_message": message,
                "snapshot_id": snapshot_id,
                "source_key": source_key,
                "project_name": project_name,
            },
        )
        return build_snapshot_tool_error_result(
            error_code="snapshot_source_key_not_found",
            message=message,
            retry_tips=[
                (
                    "Retry with a valid snapshot_id and source_key returned by "
                    "collect_logs or list_log_snapshot_files."
                ),
            ],
            details={
                "project_name": project_name,
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
        workspace=context.metadata.workspace,
        snapshot_id=context.metadata.snapshot_id,
        snapshot_dir=str(context.snapshot_dir),
        source_key=source_key,
        start_line=effective_start_line,
        line_count=effective_line_count,
        max_bytes=max_bytes,
        next_step_tips=READ_SNAPSHOT_NEXT_STEP_TIPS,
        truncated=truncated,
        content=preview_content,
        file=file_payload,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "read_log_snapshot_file",
            "snapshot_id": payload.snapshot_id,
            "workspace": payload.workspace,
            "source_key": payload.source_key,
            "truncated": payload.truncated,
            "start_line": payload.start_line,
            "line_count": payload.line_count,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(LOGS_COLLECT_SCOPE)
def grep_log_snapshot(
    snapshot_id: str,
    grep: str,
    project_name: str | None = None,
    source_keys: list[str] | None = None,
    match_offset: int = 0,
    match_limit: int = DEFAULT_GREP_MATCH_LIMIT,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Search one persisted snapshot with controlled grep semantics.

    This is the snapshot search step for persisted logs. The caller provides a
    bounded text pattern through `grep`, and the server searches only the files
    that belong to the authorized snapshot.

    Public arguments:

    - `snapshot_id`
      the persisted snapshot to search. This is the real snapshot identity for
      the tool. Use `snapshot_id="latest"` for ad-hoc searches over the newest
      workflow snapshot, or pass an explicit snapshot id returned by
      `collect_logs` when you want to keep searching the same saved artifact
      across multiple steps.
    - `grep`
      the text pattern to search for inside the persisted snapshot files.
    - `project_name`
      optional project scope check. In the current design, snapshot lookup is
      still project-scoped for authorization and storage resolution, so this
      can be used when the caller wants to make that project choice explicit.
    - `source_keys`
      optional file subset inside the snapshot. Omit it to search every saved
      source in the snapshot, or provide it to limit the search to specific
      sources such as only `backend` or only `nginx`.
    - `match_offset` and `match_limit`
      page through larger match sets in smaller windows.

    This tool is mainly intended for:

    - finding matching lines across one persisted snapshot
    - narrowing analysis before opening a full file with `read_log_snapshot_file`
    - reusing a stable snapshot id across repeated searches
    """

    assert access_token is not None
    if not grep.strip():
        return build_snapshot_tool_error_result(
            error_code="empty_grep_pattern",
            message="grep must be a non-empty string.",
            retry_tips=["Retry with grep set to the text pattern you want to search for."],
        )
    if match_offset < 0:
        return build_snapshot_tool_error_result(
            error_code="invalid_match_window",
            message="match_offset must be greater than or equal to 0.",
            retry_tips=["Retry with match_offset set to 0 or a positive integer."],
        )
    if match_limit < 1 or match_limit > MAX_GREP_MATCHES:
        return build_snapshot_tool_error_result(
            error_code="invalid_match_window",
            message=f"match_limit must be between 1 and {MAX_GREP_MATCHES}.",
            retry_tips=[f"Retry with match_limit set between 1 and {MAX_GREP_MATCHES}."],
        )

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "grep_log_snapshot",
            "snapshot_id": snapshot_id,
            "project_name": project_name,
            "source_keys": source_keys,
            "grep_length": len(grep),
            "match_offset": match_offset,
            "match_limit": match_limit,
        },
    )
    source_keys_detail: list[JSONValue] = list(source_keys or [])
    context, error_result = resolve_snapshot_context_or_error(
        access_token=access_token,
        project_name=project_name,
        snapshot_id=snapshot_id,
        default_error_code="log_snapshot_search_error",
        invalid_retry_tips=[
            "Retry with a valid snapshot_id and grep pattern for the authorized project.",
        ],
        details={
            "project_name": project_name,
            "snapshot_id": snapshot_id,
            "grep": grep,
            "source_keys": source_keys_detail,
        },
        logger=logger,
        tool_name="grep_log_snapshot",
        log_context={
            "source_keys": source_keys,
            "grep_length": len(grep),
        },
    )
    if error_result is not None:
        return error_result
    assert context is not None

    try:
        snapshot_service = LogSnapshotService(settings, access_token)
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
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "grep_log_snapshot",
                "error_message": message,
                "snapshot_id": snapshot_id,
                "project_name": project_name,
                "source_keys": source_keys,
                "grep_length": len(grep),
            },
        )
        return build_snapshot_tool_error_result(
            error_code="snapshot_source_key_not_found",
            message=message,
            retry_tips=[
                "Retry with a valid snapshot_id and grep pattern for the authorized project.",
            ],
            details={
                "project_name": project_name,
                "snapshot_id": snapshot_id,
                "grep": grep,
                "source_keys": source_keys_detail,
            },
        )

    matched_source_keys = sorted({match.source_key for match in matches})
    searched_source_keys = source_keys or [item.source_key for item in context.metadata.files]
    payload = GrepLogSnapshotPayload(
        action="grep_log_snapshot",
        requested_project_name=project_name,
        authorized_project_name=context.authorized_project_name,
        effective_project_name=context.effective_project_name,
        workspace=context.metadata.workspace,
        snapshot_id=context.metadata.snapshot_id,
        snapshot_dir=str(context.snapshot_dir),
        grep=grep,
        searched_source_keys=searched_source_keys,
        matched_source_keys=matched_source_keys,
        match_offset=match_offset,
        match_limit=match_limit,
        match_count=total_match_count,
        returned_match_count=len(matches),
        next_step_tips=GREP_SNAPSHOT_NEXT_STEP_TIPS,
        truncated=match_offset + len(matches) < total_match_count,
        matches=matches,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "grep_log_snapshot",
            "snapshot_id": payload.snapshot_id,
            "workspace": payload.workspace,
            "searched_source_count": len(payload.searched_source_keys),
            "matched_source_count": len(payload.matched_source_keys),
            "returned_match_count": payload.returned_match_count,
            "total_match_count": payload.match_count,
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
