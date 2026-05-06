"""Snapshot analysis and follow-up window MCP tools."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.tools.base import ToolResult

from app import mcp
from auth.scopes import LOGS_COLLECT_SCOPE
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from services.log_analysis import LogAnalysisService
from services.log_filtering import CreateFilteredViewError, LogFilteringService, SourceNoiseContext
from services.log_snapshots import LogSnapshotService, SnapshotContext, SnapshotLookupError
from services.project_manifest import ProjectManifestError, ProjectManifestService
from tools.agent_hints import (
    BUILD_INCIDENT_BUNDLE_TOOL_DESCRIPTION,
    CREATE_FILTERED_VIEW_TOOL_DESCRIPTION,
    FILTERED_VIEW_NEXT_STEP_TIPS,
    FOLLOWUP_WINDOW_NEXT_STEP_TIPS,
    GROUP_ERRORS_NEXT_STEP_TIPS,
    GROUP_ERRORS_TOOL_DESCRIPTION,
    INCIDENT_BUNDLE_NEXT_STEP_TIPS,
    LOG_ANALYSIS_CAUTIONS,
    SUGGEST_FOLLOWUP_WINDOW_TOOL_DESCRIPTION,
)
from tools.models import GroupedErrorPayload, GroupErrorsPayload, SuggestFollowupWindowPayload
from utils.log_snapshots import (
    build_snapshot_tool_error_result,
    format_followup_timestamp,
    parse_followup_timestamp,
)
from utils.types import JSONValue

logger: logging.Logger = get_logger("tools.analysis")
analysis_service = LogAnalysisService()
filtering_service = LogFilteringService()
snapshot_service = LogSnapshotService()
manifest_service = ProjectManifestService()

DEFAULT_MAX_ERROR_GROUPS = 50
DEFAULT_FILTERED_VIEW_MAX_LINES = 200
GROUP_ERRORS_SUMMARY_LIMIT = 5


def _build_group_errors_summary(
    *,
    matching_line_count: int,
    total_group_count: int,
    groups: list[GroupedErrorPayload],
) -> str:
    """Build a compact agent-facing explanation of grouped error results."""

    if matching_line_count == 0:
        return "No error-like lines were found in the selected snapshot sources."

    group_word = "group" if total_group_count == 1 else "groups"
    line_word = "line" if matching_line_count == 1 else "lines"
    details = [
        (
            f"{group.count}x {group.severity} {group.category} in "
            f"{', '.join(group.source_keys)}: {group.message_summary}"
        )
        for group in groups[:GROUP_ERRORS_SUMMARY_LIMIT]
    ]
    if not details:
        return (
            f"Found {matching_line_count} error-like {line_word} in "
            f"{total_group_count} {group_word}."
        )
    return (
        f"Found {matching_line_count} error-like {line_word} in "
        f"{total_group_count} {group_word}. Top results: " + "; ".join(details)
    )


def _build_invalid_group_window_result(max_groups: int) -> ToolResult | None:
    """Return a tool error when `max_groups` is outside the supported window."""

    if max_groups < 1 or max_groups > 200:
        return build_snapshot_tool_error_result(
            error_code="invalid_group_window",
            message="max_groups must be between 1 and 200.",
            retry_tips=["Retry with max_groups set to a value between 1 and 200."],
        )
    return None


def _load_snapshot_for_analysis_tool(
    *,
    tool_name: str,
    project_name: str,
    session_id: str | None,
    archive_name: str | None,
    source_keys: list[str] | None,
    source_keys_detail: list[JSONValue],
    max_groups: int,
) -> SnapshotContext | ToolResult:
    """Load the snapshot context or build the same MCP error shape used by analysis tools."""

    context: SnapshotContext | SnapshotLookupError = snapshot_service.load_snapshot(
        project_name=project_name,
        workspace="session" if session_id is not None else "workflow",
        session_id=session_id,
        archive_name=archive_name,
    )
    if isinstance(context, SnapshotLookupError):
        message = context.message
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": tool_name,
                "error_message": message,
                "session_id": session_id,
                "archive_name": archive_name,
                "project_name": project_name,
                "source_keys": source_keys,
                "max_groups": max_groups,
            },
        )
        return build_snapshot_tool_error_result(
            error_code=context.error_code,
            message=message,
            retry_tips=context.retry_tips,
            details={
                "project_name": project_name,
                "session_id": session_id,
                "archive_name": archive_name,
                "source_keys": source_keys_detail,
                "max_groups": max_groups,
            },
        )
    return context


def _build_analysis_source_key_error_result(
    *,
    tool_name: str,
    error: ValueError,
    project_name: str,
    session_id: str | None,
    archive_name: str | None,
    source_keys: list[str] | None,
    source_keys_detail: list[JSONValue],
    max_groups: int,
) -> ToolResult:
    """Build the shared source-key validation error used by analysis tools."""

    message = str(error)
    logger.info(
        "tool error",
        extra={
            "event": "tool_error",
            "tool_name": tool_name,
            "error_message": message,
            "session_id": session_id,
            "archive_name": archive_name,
            "project_name": project_name,
            "source_keys": source_keys,
            "max_groups": max_groups,
        },
    )
    return build_snapshot_tool_error_result(
        error_code="snapshot_source_key_not_found",
        message=message,
        retry_tips=[
            (
                "Retry with valid source_keys for this snapshot. Use session_id "
                "for session snapshots, or archive_name for archived workflow snapshots."
            ),
        ],
        details={
            "project_name": project_name,
            "session_id": session_id,
            "archive_name": archive_name,
            "source_keys": source_keys_detail,
            "max_groups": max_groups,
        },
    )


def _build_invalid_filtered_view_limit_result(
    *,
    max_lines: int,
) -> ToolResult | None:
    """Return a tool error when filtered-view limits are outside supported ranges."""

    if max_lines < 1 or max_lines > 1000:
        return build_snapshot_tool_error_result(
            error_code="invalid_filtered_view_limit",
            message="max_lines must be between 1 and 1000.",
            retry_tips=["Retry with max_lines set to a value between 1 and 1000."],
        )
    return None


def _load_snapshot_for_filtered_view_tool(
    *,
    project_name: str,
    session_id: str | None,
    archive_name: str | None,
    source_keys: list[str] | None,
    source_keys_detail: list[JSONValue],
    max_lines: int,
) -> SnapshotContext | ToolResult:
    """Load a snapshot or build the filtered-view snapshot lookup error response."""

    context: SnapshotContext | SnapshotLookupError = snapshot_service.load_snapshot(
        project_name=project_name,
        workspace="session" if session_id is not None else "workflow",
        session_id=session_id,
        archive_name=archive_name,
    )
    if isinstance(context, SnapshotLookupError):
        message = context.message
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "create_filtered_view",
                "error_message": message,
                "session_id": session_id,
                "archive_name": archive_name,
                "project_name": project_name,
                "source_keys": source_keys,
                "max_lines": max_lines,
            },
        )
        return build_snapshot_tool_error_result(
            error_code=context.error_code,
            message=message,
            retry_tips=context.retry_tips,
            details={
                "project_name": project_name,
                "session_id": session_id,
                "archive_name": archive_name,
                "source_keys": source_keys_detail,
                "max_lines": max_lines,
            },
        )
    return context


def _build_filtered_view_source_key_error_result(
    *,
    error: CreateFilteredViewError,
    project_name: str,
    session_id: str | None,
    archive_name: str | None,
    source_keys: list[str] | None,
    source_keys_detail: list[JSONValue],
    max_lines: int,
) -> ToolResult:
    """Build the filtered-view source-key or manifest lookup error response."""

    logger.info(
        "tool error",
        extra={
            "event": "tool_error",
            "tool_name": "create_filtered_view",
            "error_message": error.message,
            "session_id": session_id,
            "archive_name": archive_name,
            "project_name": project_name,
            "source_keys": source_keys,
        },
    )
    return build_snapshot_tool_error_result(
        error_code=error.error_code,
        message=error.message,
        retry_tips=error.retry_tips,
        details={
            "project_name": project_name,
            "session_id": session_id,
            "archive_name": archive_name,
            "source_keys": source_keys_detail,
            "max_lines": max_lines,
        },
    )


@mcp.tool(
    auth=require_scopes(LOGS_COLLECT_SCOPE),
    description=GROUP_ERRORS_TOOL_DESCRIPTION,
)
@project_authorized_tool
def group_errors(
    project_name: str,
    session_id: str | None = None,
    archive_name: str | None = None,
    source_keys: list[str] | None = None,
    max_groups: int = DEFAULT_MAX_ERROR_GROUPS,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Group repeated error-like lines for triage, then confirm with raw snapshot context.

    This converts repeated raw error-like lines into deterministic grouped
    findings from one persisted artifact so agents can quickly see whether
    there are recurring issues before recollecting a narrower window with
    `collect_logs` and `since` / `until`.

    Use this summary with caution. Grouped findings are intentionally
    compressed and do not replace full timeline/context review by themselves.
    After identifying a suspicious group, agents should confirm the real
    incident shape by recollecting a tighter time window with `collect_logs`
    and by inspecting targeted `grep_log_snapshot(...)` and
    `read_log_snapshot_file(...)` follow-up calls.
    """

    assert access_token is not None
    invalid_group_window_result = _build_invalid_group_window_result(max_groups)
    if invalid_group_window_result is not None:
        return invalid_group_window_result

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "group_errors",
            "session_id": session_id,
            "archive_name": archive_name,
            "project_name": project_name,
            "source_keys": source_keys,
            "max_groups": max_groups,
        },
    )
    source_keys_detail: list[JSONValue] = list(source_keys or [])
    context: SnapshotContext | ToolResult = _load_snapshot_for_analysis_tool(
        tool_name="group_errors",
        project_name=project_name,
        session_id=session_id,
        archive_name=archive_name,
        source_keys=source_keys,
        source_keys_detail=source_keys_detail,
        max_groups=max_groups,
    )
    if isinstance(context, ToolResult):
        return context

    try:
        analysis = analysis_service.group_snapshot_errors(
            context.metadata,
            source_keys=source_keys,
            max_groups=max_groups,
        )
    except ValueError as error:
        return _build_analysis_source_key_error_result(
            tool_name="group_errors",
            error=error,
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            source_keys=source_keys,
            source_keys_detail=source_keys_detail,
            max_groups=max_groups,
        )

    payload = GroupErrorsPayload(
        action="group_errors",
        requested_project_name=project_name,
        project_name=context.project_name,
        workspace=context.metadata.workspace,
        session_id=context.metadata.session_id,
        snapshot_dir=(
            Path(context.metadata.files[0].output_file).parent.as_posix()
            if context.metadata.files
            else ""
        ),
        searched_source_keys=analysis.searched_source_keys,
        analysis_cautions=LOG_ANALYSIS_CAUTIONS,
        next_step_tips=GROUP_ERRORS_NEXT_STEP_TIPS,
        grouped_error_count=analysis.total_group_count,
        matching_line_count=analysis.matching_line_count,
        max_groups=max_groups,
        truncated=analysis.total_group_count > max_groups,
        summary=_build_group_errors_summary(
            matching_line_count=analysis.matching_line_count,
            total_group_count=analysis.total_group_count,
            groups=analysis.groups,
        ),
        groups=analysis.groups,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "group_errors",
            "session_id": payload.session_id,
            "archive_name": archive_name,
            "workspace": payload.workspace,
            "grouped_error_count": payload.grouped_error_count,
            "matching_line_count": payload.matching_line_count,
            "returned_group_count": len(payload.groups),
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@mcp.tool(
    auth=require_scopes(LOGS_COLLECT_SCOPE),
    description=BUILD_INCIDENT_BUNDLE_TOOL_DESCRIPTION,
)
@project_authorized_tool
def build_incident_bundle(
    project_name: str,
    session_id: str | None = None,
    archive_name: str | None = None,
    source_keys: list[str] | None = None,
    max_groups: int = DEFAULT_MAX_ERROR_GROUPS,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Build one compact incident summary, then confirm conclusions with raw snapshot context.

    This MCP tool loads one authorized workflow or session snapshot, runs the
    deterministic grouped-error analyzer, and returns one agent-facing bundle
    containing:

    - top grouped error-like findings
    - total matching-line and severity counts
    - per-source summaries for the sources that contributed findings
    - deterministic next-step tips for drilling into the raw snapshot files

    The bundle is intentionally a triage entry point. It helps an agent decide
    what to inspect next, but it is not final incident proof by itself. Agents
    should confirm timing, clustering, and severity by reading the referenced
    raw lines, grepping nearby context, or recollecting a narrower time window.
    """

    assert access_token is not None
    invalid_group_window_result = _build_invalid_group_window_result(max_groups)
    if invalid_group_window_result is not None:
        return invalid_group_window_result

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "build_incident_bundle",
            "session_id": session_id,
            "archive_name": archive_name,
            "project_name": project_name,
            "source_keys": source_keys,
            "max_groups": max_groups,
        },
    )
    source_keys_detail: list[JSONValue] = list(source_keys or [])
    context: SnapshotContext | ToolResult = _load_snapshot_for_analysis_tool(
        tool_name="build_incident_bundle",
        project_name=project_name,
        session_id=session_id,
        archive_name=archive_name,
        source_keys=source_keys,
        source_keys_detail=source_keys_detail,
        max_groups=max_groups,
    )
    if isinstance(context, ToolResult):
        return context

    try:
        payload = analysis_service.build_incident_bundle(
            context.metadata,
            source_keys=source_keys,
            max_groups=max_groups,
            requested_project_name=project_name,
            project_name=context.project_name,
            analysis_cautions=LOG_ANALYSIS_CAUTIONS,
            next_step_tips=INCIDENT_BUNDLE_NEXT_STEP_TIPS,
        )
    except ValueError as error:
        return _build_analysis_source_key_error_result(
            tool_name="build_incident_bundle",
            error=error,
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            source_keys=source_keys,
            source_keys_detail=source_keys_detail,
            max_groups=max_groups,
        )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "build_incident_bundle",
            "session_id": payload.session_id,
            "archive_name": archive_name,
            "workspace": payload.workspace,
            "grouped_error_count": payload.grouped_error_count,
            "matching_line_count": payload.matching_line_count,
            "high_severity_group_count": payload.high_severity_group_count,
            "medium_severity_group_count": payload.medium_severity_group_count,
            "low_severity_group_count": payload.low_severity_group_count,
            "top_group_count": len(payload.top_groups),
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@mcp.tool(
    auth=require_scopes(LOGS_COLLECT_SCOPE),
    description=CREATE_FILTERED_VIEW_TOOL_DESCRIPTION,
)
@project_authorized_tool
def create_filtered_view(
    project_name: str,
    session_id: str | None = None,
    archive_name: str | None = None,
    source_keys: list[str] | None = None,
    max_lines: int = DEFAULT_FILTERED_VIEW_MAX_LINES,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Create a cleaned deterministic view from one persisted raw snapshot.

    This is the manifest-profile-based noise-cleaning step. It keeps the raw
    persisted collection untouched, applies deterministic noise filters per
    source, and returns a smaller cleaned view that agents can inspect before
    opening the raw files directly.
    """

    assert access_token is not None
    invalid_limit_result = _build_invalid_filtered_view_limit_result(
        max_lines=max_lines,
    )
    if invalid_limit_result is not None:
        return invalid_limit_result

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "create_filtered_view",
            "session_id": session_id,
            "archive_name": archive_name,
            "project_name": project_name,
            "source_keys": source_keys,
            "max_lines": max_lines,
        },
    )
    source_keys_detail: list[JSONValue] = list(source_keys or [])
    context: SnapshotContext | ToolResult = _load_snapshot_for_filtered_view_tool(
        project_name=project_name,
        session_id=session_id,
        archive_name=archive_name,
        source_keys=source_keys,
        source_keys_detail=source_keys_detail,
        max_lines=max_lines,
    )
    if isinstance(context, ToolResult):
        return context

    manifest_result = manifest_service.get_or_error(project_name)
    if isinstance(manifest_result, ProjectManifestError):
        return _build_filtered_view_source_key_error_result(
            error=CreateFilteredViewError(
                message=manifest_result.message,
                error_code="snapshot_source_key_not_found",
                retry_tips=[
                    "Retry with a valid archive_name and source_keys for the authorized project.",
                ],
            ),
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            source_keys=source_keys,
            source_keys_detail=source_keys_detail,
            max_lines=max_lines,
        )

    manifest = manifest_result.manifest
    source_contexts = {
        source.source_key: SourceNoiseContext(
            source_key=source.source_key,
            parser_type=source.parser_type,
            normalization_profile=source.normalization_profile,
            default_noise_profile=source.default_noise_profile,
            static_asset_paths=tuple(manifest.static_asset_paths),
            static_asset_extensions=tuple(manifest.static_asset_extensions),
        )
        for source in manifest.sources
    }
    payload = filtering_service.create_filtered_view(
        context.metadata,
        source_contexts=source_contexts,
        source_keys=source_keys,
        max_lines=max_lines,
        requested_project_name=project_name,
        project_name=context.project_name,
        next_step_tips=FILTERED_VIEW_NEXT_STEP_TIPS,
    )
    if isinstance(payload, CreateFilteredViewError):
        return _build_filtered_view_source_key_error_result(
            error=payload,
            project_name=project_name,
            session_id=session_id,
            archive_name=archive_name,
            source_keys=source_keys,
            source_keys_detail=source_keys_detail,
            max_lines=max_lines,
        )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "create_filtered_view",
            "session_id": payload.session_id,
            "archive_name": archive_name,
            "workspace": payload.workspace,
            "searched_source_count": len(payload.searched_source_keys),
            "kept_line_count": payload.kept_line_count,
            "excluded_line_count": payload.excluded_line_count,
            "returned_line_count": payload.returned_line_count,
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    LOGS_COLLECT_SCOPE,
    mcp_description=SUGGEST_FOLLOWUP_WINDOW_TOOL_DESCRIPTION,
)
def suggest_followup_window(
    first_timestamp: str,
    last_timestamp: str,
    padding_minutes: int = 5,
) -> ToolResult:
    """Suggest a narrower `collect_logs` window from grouped-analysis timestamps.

    This helper turns a suspicious time span from `group_errors` or
    `build_incident_bundle` into ready-to-use `since` and `until` values for a
    new `collect_logs` call.

    Public arguments:

    - `first_timestamp`
      the beginning of the suspicious time span, typically taken from one
      grouped finding or source summary
    - `last_timestamp`
      the end of the suspicious time span
    - `padding_minutes`
      extra time to add before and after the detected span so the recollected
      snapshot includes nearby context, not only the exact matching interval

    This tool does not recollect logs by itself. It only prepares a narrower
    window so the caller can run `collect_logs` again with more focused
    `since` / `until` bounds.
    """

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "suggest_followup_window",
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "padding_minutes": padding_minutes,
        },
    )
    if padding_minutes < 0 or padding_minutes > 1440:
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "suggest_followup_window",
                "error_message": "invalid padding_minutes",
                "first_timestamp": first_timestamp,
                "last_timestamp": last_timestamp,
                "padding_minutes": padding_minutes,
            },
        )
        return build_snapshot_tool_error_result(
            error_code="invalid_followup_window_padding",
            message="padding_minutes must be between 0 and 1440.",
            retry_tips=["Retry with padding_minutes set between 0 and 1440."],
        )

    try:
        parsed_first = parse_followup_timestamp(first_timestamp)
        parsed_last = parse_followup_timestamp(last_timestamp)
    except ValueError:
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "suggest_followup_window",
                "error_message": "invalid follow-up timestamps",
                "first_timestamp": first_timestamp,
                "last_timestamp": last_timestamp,
                "padding_minutes": padding_minutes,
            },
        )
        return build_snapshot_tool_error_result(
            error_code="invalid_followup_window_timestamp",
            message=(
                "first_timestamp and last_timestamp must be valid ISO-8601 values or "
                "the timestamp formats returned by grouped analysis."
            ),
            retry_tips=[
                "Pass timestamps directly from group_errors or build_incident_bundle.",
            ],
        )

    window_start = min(parsed_first, parsed_last) - timedelta(minutes=padding_minutes)
    window_end = max(parsed_first, parsed_last) + timedelta(minutes=padding_minutes)
    payload = SuggestFollowupWindowPayload(
        action="suggest_followup_window",
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        padding_minutes=padding_minutes,
        suggested_since=format_followup_timestamp(window_start),
        suggested_until=format_followup_timestamp(window_end),
        suggested_duration_minutes=max(
            1,
            int((window_end - window_start).total_seconds() // 60),
        ),
        ready_for_collect_logs=True,
        next_step_tips=FOLLOWUP_WINDOW_NEXT_STEP_TIPS,
        explanation=(
            "Use the returned since/until values in a new collect_logs call to "
            "recollect a narrower snapshot around the suspicious time span."
        ),
        example_collect_logs_arguments={
            "since": format_followup_timestamp(window_start),
            "until": format_followup_timestamp(window_end),
        },
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "suggest_followup_window",
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "padding_minutes": padding_minutes,
            "suggested_since": payload.suggested_since,
            "suggested_until": payload.suggested_until,
            "suggested_duration_minutes": payload.suggested_duration_minutes,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
