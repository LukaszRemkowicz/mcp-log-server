"""Snapshot inventory, read, and grep MCP tools."""

from __future__ import annotations

import logging

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken
from fastmcp.tools.base import ToolResult

from auth.mcp_caller_context import get_request_mcp_caller
from auth.scopes import LOGS_COLLECT_SCOPE
from core.types import LogWorkspace
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from services.log_snapshots import (
    DEFAULT_GREP_MATCH_LIMIT,
    LogSnapshotService,
    SnapshotContext,
    SnapshotGrepError,
    SnapshotLookupError,
    SnapshotReadChunk,
    SnapshotReadError,
)
from tools.agent_hints import (
    GREP_LOG_SNAPSHOT_TOOL_DESCRIPTION,
    GREP_SNAPSHOT_NEXT_STEP_TIPS,
    LIST_SNAPSHOT_NEXT_STEP_TIPS,
    READ_SNAPSHOT_NEXT_STEP_TIPS,
)
from tools.errors import build_invalid_source_key_arguments_result, build_snapshot_tool_error_result
from tools.models import (
    GrepLogSnapshotMatchPayload,
    GrepLogSnapshotPayload,
    ListLogSnapshotFilesPayload,
    LogSnapshotFilePayload,
    ReadLogSnapshotFilePayload,
)
from tools.utils import SourceKeyArgumentError, resolve_source_keys_for_snapshot
from utils.log_preview import truncate_log_preview
from utils.log_snapshots import (
    build_snapshot_not_found_retry_tips,
    is_collection_diagnostics_source_key,
)
from utils.types import JSONObject, JSONValue

logger: logging.Logger = get_logger("tools.snapshots")

MAX_INLINE_LOG_BYTES = 200_000
MAX_GREP_MATCHES = 500
snapshot_service = LogSnapshotService()


async def _load_snapshot_context(
    *,
    project_name: str,
    session_id: str | None,
    archive_name: str | None,
) -> SnapshotContext | SnapshotLookupError:
    """Load one snapshot context or return the domain lookup error model."""

    return await snapshot_service.load_snapshot(
        project_name=project_name,
        workspace=LogWorkspace.SESSION if session_id is not None else LogWorkspace.WORKFLOW,
        session_id=session_id,
        archive_name=archive_name,
    )


def _build_snapshot_lookup_error_result(
    *,
    lookup_error: SnapshotLookupError,
    tool_name: str,
    project_name: str,
    session_id: str | None,
    archive_name: str | None,
    details: JSONObject | None = None,
    log_extra: dict[str, object] | None = None,
) -> ToolResult:
    """Build the shared MCP error response for one snapshot lookup failure."""

    logger.info(
        "tool error",
        extra={
            "event": "tool_error",
            "tool_name": tool_name,
            "error_message": lookup_error.message,
            "session_id": session_id,
            "archive_name": archive_name,
            "project_name": project_name,
            **(log_extra or {}),
        },
    )
    error_details: JSONObject = {
        "project_name": project_name,
        "session_id": session_id,
        "archive_name": archive_name,
    }
    if details is not None:
        error_details.update(details)
    return build_snapshot_tool_error_result(
        error_code=lookup_error.error_code,
        message=lookup_error.message,
        retry_tips=lookup_error.retry_tips,
        details=error_details,
    )


def _build_snapshot_owner_mismatch_result(
    *,
    tool_name: str,
    context: SnapshotContext,
    project_name: str,
    session_id: str | None,
    archive_name: str | None,
    details: JSONObject | None = None,
    log_extra: dict[str, object] | None = None,
) -> ToolResult:
    """Return the same shape as a missing snapshot when caller ownership fails."""

    workspace = context.metadata.workspace
    return _build_snapshot_lookup_error_result(
        lookup_error=SnapshotLookupError(
            message=f"Requested {workspace} log snapshot was not found.",
            error_code="snapshot_not_found",
            retry_tips=build_snapshot_not_found_retry_tips(workspace),
        ),
        tool_name=tool_name,
        project_name=project_name,
        session_id=session_id,
        archive_name=archive_name,
        details=details,
        log_extra=log_extra,
    )


def _build_snapshot_read_error_result(
    *,
    read_error: SnapshotReadError,
    project_name: str,
    session_id: str | None,
    archive_name: str | None,
    source_key: str,
    start_line: int | None = None,
    line_count: int | None = None,
) -> ToolResult:
    """Build the read-file error response for one expected service error."""

    logger.info(
        "tool error",
        extra={
            "event": "tool_error",
            "tool_name": "read_log_snapshot_file",
            "error_message": read_error.message,
            "session_id": session_id,
            "archive_name": archive_name,
            "source_key": source_key,
            "project_name": project_name,
        },
    )
    return build_snapshot_tool_error_result(
        error_code=read_error.error_code,
        message=read_error.message,
        retry_tips=read_error.retry_tips,
        details={
            "project_name": project_name,
            "session_id": session_id,
            "archive_name": archive_name,
            "source_key": source_key,
            "start_line": start_line,
            "line_count": line_count,
        },
    )


@workflow_discoverable_tool(LOGS_COLLECT_SCOPE)
@project_authorized_tool
async def list_log_snapshot_files(
    project_name: str,
    session_id: str | None = None,
    archive_name: str | None = None,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """List saved log files for one persisted snapshot.

    This is the snapshot inventory step after `collect_logs`. It does not read
    file bodies or search content. It only describes which persisted files
    exist inside one snapshot and how large they are.

    Use `session_id` plus `project_name` for session investigations.
    Use `project_name` alone for the newest workflow artifact, or add
    `archive_name` to reopen an archived workflow artifact.

    Typical next steps after this tool:

    - `read_log_snapshot_file` for one concrete source file
    - `grep_log_snapshot` to search selected files in the same snapshot
    """

    assert access_token is not None
    caller = get_request_mcp_caller()
    context: SnapshotContext | SnapshotLookupError = await _load_snapshot_context(
        project_name=project_name,
        session_id=session_id,
        archive_name=archive_name,
    )
    if isinstance(context, SnapshotLookupError):
        return _build_snapshot_lookup_error_result(
            lookup_error=context,
            tool_name="list_log_snapshot_files",
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
        )
    if context.caller_id != caller.caller_id:
        return _build_snapshot_owner_mismatch_result(
            tool_name="list_log_snapshot_files",
            context=context,
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
        )

    payload = ListLogSnapshotFilesPayload(
        action="list_log_snapshot_files",
        requested_project_name=project_name,
        project_name=context.project_name,
        workspace=context.metadata.workspace,
        session_id=context.metadata.session_id,
        snapshot_dir=str(context.snapshot_dir),
        collected_at=context.metadata.collected_at,
        next_step_tips=LIST_SNAPSHOT_NEXT_STEP_TIPS,
        files=context.metadata.files,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "list_log_snapshot_files",
            "session_id": payload.session_id,
            "archive_name": archive_name,
            "workspace": payload.workspace,
            "file_count": len(payload.files),
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(LOGS_COLLECT_SCOPE)
@project_authorized_tool
async def read_log_snapshot_file(
    project_name: str,
    source_key: str,
    session_id: str | None = None,
    archive_name: str | None = None,
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

    Use `session_id` plus `project_name` for session investigations. Omit
    `archive_name` for the newest workflow artifact, or pass an archive
    folder name when the agent must keep reading one older workflow artifact.

    `start_line` and `line_count` can be used to read a smaller line-range
    chunk instead of the whole file. This is the main way to inspect very
    large logs incrementally after a grep result or earlier preview.

    `max_bytes` still limits the inline body returned to the caller. The
    persisted file itself remains unchanged on disk, and the response tells
    the caller whether the returned body was truncated.
    """

    assert access_token is not None
    caller = get_request_mcp_caller()
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

    context: SnapshotContext | SnapshotLookupError = await _load_snapshot_context(
        project_name=project_name,
        session_id=session_id,
        archive_name=archive_name,
    )
    if isinstance(context, SnapshotLookupError):
        return _build_snapshot_lookup_error_result(
            lookup_error=context,
            tool_name="read_log_snapshot_file",
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            details={
                "source_key": source_key,
                "start_line": start_line,
                "line_count": line_count,
            },
            log_extra={"source_key": source_key},
        )
    if context.caller_id != caller.caller_id:
        return _build_snapshot_owner_mismatch_result(
            tool_name="read_log_snapshot_file",
            context=context,
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            details={
                "source_key": source_key,
                "start_line": start_line,
                "line_count": line_count,
            },
            log_extra={"source_key": source_key},
        )

    source = snapshot_service.find_snapshot_source(
        context.sources,
        source_key=source_key,
    )
    if isinstance(source, SnapshotReadError):
        return _build_snapshot_read_error_result(
            read_error=source,
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            source_key=source_key,
            start_line=start_line,
            line_count=line_count,
        )
    full_content = snapshot_service.read_snapshot_source(source)
    if isinstance(full_content, SnapshotReadError):
        return _build_snapshot_read_error_result(
            read_error=full_content,
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            source_key=source_key,
            start_line=start_line,
            line_count=line_count,
        )
    read_chunk: SnapshotReadChunk | SnapshotReadError = snapshot_service.select_snapshot_read_chunk(
        full_content,
        start_line=start_line,
        line_count=line_count,
    )
    if isinstance(read_chunk, SnapshotReadError):
        return _build_snapshot_read_error_result(
            read_error=read_chunk,
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            source_key=source_key,
            start_line=start_line,
            line_count=line_count,
        )
    preview_content: str = truncate_log_preview(read_chunk.content, max_bytes)
    truncated: bool = preview_content != read_chunk.content
    file_payload: LogSnapshotFilePayload = snapshot_service.source_to_file_payload(source)

    payload = ReadLogSnapshotFilePayload(
        action="read_log_snapshot_file",
        requested_project_name=project_name,
        project_name=context.project_name,
        workspace=context.metadata.workspace,
        session_id=context.metadata.session_id,
        snapshot_dir=str(context.snapshot_dir),
        source_key=source_key,
        start_line=read_chunk.start_line,
        line_count=read_chunk.line_count,
        max_bytes=max_bytes,
        next_step_tips=READ_SNAPSHOT_NEXT_STEP_TIPS,
        truncated=truncated,
        content=preview_content,
        output_file=file_payload.output_file,
        returned_bytes=len(preview_content.encode("utf-8")),
        file=file_payload,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "read_log_snapshot_file",
            "session_id": payload.session_id,
            "archive_name": archive_name,
            "workspace": payload.workspace,
            "source_key": payload.source_key,
            "truncated": payload.truncated,
            "start_line": payload.start_line,
            "line_count": payload.line_count,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    LOGS_COLLECT_SCOPE,
    mcp_description=GREP_LOG_SNAPSHOT_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def grep_log_snapshot(
    project_name: str,
    grep: str,
    session_id: str | None = None,
    archive_name: str | None = None,
    source_keys: list[str] | None = None,
    source_key: str | None = None,
    match_offset: int = 0,
    max_matches: int = DEFAULT_GREP_MATCH_LIMIT,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Search one persisted snapshot with controlled grep semantics.

    This is the snapshot search step for persisted logs. The caller provides a
    bounded extended regex pattern through `grep`, and the server searches only
    the files that belong to the authorized snapshot.

    Public arguments:

    - `project_name`
      the project artifact to inspect
    - `session_id`
      use this for session investigations where one session can contain more
      than one project
    - `archive_name`
      optional workflow archive folder name. Omit it to search the newest
      workflow artifact, or pass it to reopen one archived workflow run
    - `grep`
      the extended regex pattern to search for inside the persisted snapshot
      files, for example `Ban|wp-login|502`.
    - `source_keys`
      optional file subset inside the snapshot. Omit it to search every saved
      source in the snapshot, or provide it to limit the search to specific
      sources such as only `backend` or only `nginx`.
    - `source_key`
      optional single-source alias. `source_key="backend"` is equivalent to
      `source_keys=["backend"]`.
    - `match_offset` and `max_matches`
      page through larger match sets in smaller windows.

    This tool is mainly intended for:

    - finding matching lines across one persisted snapshot
    - narrowing analysis before opening a full file with `read_log_snapshot_file`
    - reusing one archived workflow artifact across repeated searches
    """

    assert access_token is not None
    caller = get_request_mcp_caller()
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
    if max_matches < 1 or max_matches > MAX_GREP_MATCHES:
        return build_snapshot_tool_error_result(
            error_code="invalid_match_window",
            message=f"max_matches must be between 1 and {MAX_GREP_MATCHES}.",
            retry_tips=[f"Retry with max_matches set between 1 and {MAX_GREP_MATCHES}."],
        )

    try:
        source_keys = resolve_source_keys_for_snapshot(source_keys, source_key)
    except SourceKeyArgumentError as error:
        return build_invalid_source_key_arguments_result(
            message=str(error),
            source_key=source_key,
            source_keys=source_keys,
        )
    source_keys_detail: list[JSONValue] = list(source_keys or [])
    context: SnapshotContext | SnapshotLookupError = await _load_snapshot_context(
        project_name=project_name,
        session_id=session_id,
        archive_name=archive_name,
    )
    if isinstance(context, SnapshotLookupError):
        return _build_snapshot_lookup_error_result(
            lookup_error=context,
            tool_name="grep_log_snapshot",
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            details={
                "grep": grep,
                "source_keys": source_keys_detail,
            },
            log_extra={
                "source_keys": source_keys,
                "grep_length": len(grep),
            },
        )
    if context.caller_id != caller.caller_id:
        return _build_snapshot_owner_mismatch_result(
            tool_name="grep_log_snapshot",
            context=context,
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            details={
                "grep": grep,
                "source_keys": source_keys_detail,
            },
            log_extra={
                "source_keys": source_keys,
                "grep_length": len(grep),
            },
        )

    grep_result: tuple[list[GrepLogSnapshotMatchPayload], int] | SnapshotGrepError = (
        snapshot_service.grep_snapshot(
            context,
            grep=grep,
            source_keys=source_keys,
            match_offset=match_offset,
            max_matches=max_matches,
        )
    )
    if isinstance(grep_result, SnapshotGrepError):
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "grep_log_snapshot",
                "error_message": grep_result.message,
                "session_id": session_id,
                "archive_name": archive_name,
                "project_name": project_name,
                "source_keys": source_keys,
                "grep_length": len(grep),
            },
        )
        return build_snapshot_tool_error_result(
            error_code=grep_result.error_code,
            message=grep_result.message,
            retry_tips=grep_result.retry_tips,
            details={
                "project_name": project_name,
                "session_id": session_id,
                "archive_name": archive_name,
                "grep": grep,
                "source_keys": source_keys_detail,
            },
        )

    matches: list[GrepLogSnapshotMatchPayload]
    total_match_count: int
    matches, total_match_count = grep_result
    matched_source_keys: list[str] = sorted({match.source_key for match in matches})
    searched_source_keys: list[str] = source_keys or [
        item.source_key
        for item in context.metadata.files
        if not is_collection_diagnostics_source_key(item.source_key)
    ]
    payload = GrepLogSnapshotPayload(
        action="grep_log_snapshot",
        requested_project_name=project_name,
        project_name=context.project_name,
        workspace=context.metadata.workspace,
        session_id=context.metadata.session_id,
        snapshot_dir=str(context.snapshot_dir),
        grep=grep,
        searched_source_keys=searched_source_keys,
        matched_source_keys=matched_source_keys,
        match_offset=match_offset,
        max_matches=max_matches,
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
            "session_id": payload.session_id,
            "archive_name": archive_name,
            "workspace": payload.workspace,
            "searched_source_count": len(payload.searched_source_keys),
            "matched_source_count": len(payload.matched_source_keys),
            "returned_match_count": payload.returned_match_count,
            "total_match_count": payload.match_count,
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
