"""Snapshot analysis and follow-up window MCP tools."""

from __future__ import annotations

import logging
from datetime import timedelta

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.tools.base import ToolResult

from app import mcp
from auth.scopes import LOGS_COLLECT_SCOPE
from logging_config import get_logger
from services.log_analysis import LogAnalysisService
from tools.agent_hints import (
    BUILD_INCIDENT_BUNDLE_TOOL_DESCRIPTION,
    FOLLOWUP_WINDOW_NEXT_STEP_TIPS,
    GROUP_ERRORS_NEXT_STEP_TIPS,
    INCIDENT_BUNDLE_NEXT_STEP_TIPS,
    LOG_ANALYSIS_CAUTIONS,
)
from tools.models import GroupedErrorPayload, GroupErrorsPayload, SuggestFollowupWindowPayload
from utils.log_snapshots import (
    build_snapshot_tool_error_result,
    format_followup_timestamp,
    parse_followup_timestamp,
    resolve_snapshot_context_or_error,
)
from utils.types import JSONValue

logger: logging.Logger = get_logger("tools.analysis")
analysis_service = LogAnalysisService()

DEFAULT_MAX_ERROR_GROUPS = 50


@mcp.tool(auth=require_scopes(LOGS_COLLECT_SCOPE))
def group_errors(
    snapshot_id: str,
    project_name: str | None = None,
    source_keys: list[str] | None = None,
    max_groups: int = DEFAULT_MAX_ERROR_GROUPS,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Group repeated error-like lines for triage, then confirm with raw snapshot context.

    This converts repeated raw error-like lines into deterministic grouped
    findings from one persisted snapshot so agents can quickly see whether
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
    if max_groups < 1 or max_groups > 200:
        return build_snapshot_tool_error_result(
            error_code="invalid_group_window",
            message="max_groups must be between 1 and 200.",
            retry_tips=["Retry with max_groups set to a value between 1 and 200."],
        )

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "group_errors",
            "snapshot_id": snapshot_id,
            "project_name": project_name,
            "source_keys": source_keys,
            "max_groups": max_groups,
        },
    )
    source_keys_detail: list[JSONValue] = list(source_keys or [])
    context, error_result = resolve_snapshot_context_or_error(
        access_token=access_token,
        project_name=project_name,
        snapshot_id=snapshot_id,
        default_error_code="log_snapshot_analysis_error",
        invalid_retry_tips=[
            "Retry with a valid snapshot_id and source_keys for the authorized project.",
        ],
        details={
            "project_name": project_name,
            "snapshot_id": snapshot_id,
            "source_keys": source_keys_detail,
            "max_groups": max_groups,
        },
        logger=logger,
        tool_name="group_errors",
        log_context={"source_keys": source_keys, "max_groups": max_groups},
    )
    if error_result is not None:
        return error_result
    assert context is not None

    try:
        grouping_result: tuple[list[GroupedErrorPayload], int, list[str], int]
        grouping_result = analysis_service.group_snapshot_errors(
            context.snapshot_dir,
            context.metadata,
            source_keys=source_keys,
            max_groups=max_groups,
        )
        groups, matching_line_count, searched_source_keys, total_group_count = grouping_result
    except ValueError as error:
        message = str(error)
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "group_errors",
                "error_message": message,
                "snapshot_id": snapshot_id,
                "project_name": project_name,
                "source_keys": source_keys,
                "max_groups": max_groups,
            },
        )
        return build_snapshot_tool_error_result(
            error_code="snapshot_source_key_not_found",
            message=message,
            retry_tips=[
                "Retry with a valid snapshot_id and source_keys for the authorized project.",
            ],
            details={
                "project_name": project_name,
                "snapshot_id": snapshot_id,
                "source_keys": source_keys_detail,
                "max_groups": max_groups,
            },
        )

    payload = GroupErrorsPayload(
        action="group_errors",
        requested_project_name=project_name,
        authorized_project_name=context.authorized_project_name,
        effective_project_name=context.effective_project_name,
        workspace=context.metadata.workspace,
        snapshot_id=context.metadata.snapshot_id,
        snapshot_dir=str(context.snapshot_dir),
        searched_source_keys=searched_source_keys,
        analysis_cautions=LOG_ANALYSIS_CAUTIONS,
        next_step_tips=GROUP_ERRORS_NEXT_STEP_TIPS,
        grouped_error_count=total_group_count,
        matching_line_count=matching_line_count,
        max_groups=max_groups,
        truncated=total_group_count > max_groups,
        groups=groups,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "group_errors",
            "snapshot_id": payload.snapshot_id,
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
def build_incident_bundle(
    snapshot_id: str,
    project_name: str | None = None,
    source_keys: list[str] | None = None,
    max_groups: int = DEFAULT_MAX_ERROR_GROUPS,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Build one compact incident summary, then confirm conclusions with raw snapshot context.

    This packages grouped findings and source summaries into one structured
    artifact from one persisted snapshot that an LLM can use as high-signal
    analysis context before deciding whether it should recollect a narrower
    window or reopen the raw snapshot files.

    Use this bundle as an entry point, not as a full substitute for raw log
    context. It summarizes the strongest deterministic signals, but agents
    should still recollect a tighter `since` / `until` window or reopen the
    relevant snapshot files and grep windows before drawing final conclusions
    about timing, clustering, or incident severity.
    """

    assert access_token is not None
    if max_groups < 1 or max_groups > 200:
        return build_snapshot_tool_error_result(
            error_code="invalid_group_window",
            message="max_groups must be between 1 and 200.",
            retry_tips=["Retry with max_groups set to a value between 1 and 200."],
        )

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "build_incident_bundle",
            "snapshot_id": snapshot_id,
            "project_name": project_name,
            "source_keys": source_keys,
            "max_groups": max_groups,
        },
    )
    source_keys_detail: list[JSONValue] = list(source_keys or [])
    context, error_result = resolve_snapshot_context_or_error(
        access_token=access_token,
        project_name=project_name,
        snapshot_id=snapshot_id,
        default_error_code="log_snapshot_analysis_error",
        invalid_retry_tips=[
            "Retry with a valid snapshot_id and source_keys for the authorized project.",
        ],
        details={
            "project_name": project_name,
            "snapshot_id": snapshot_id,
            "source_keys": source_keys_detail,
            "max_groups": max_groups,
        },
        logger=logger,
        tool_name="build_incident_bundle",
        log_context={"source_keys": source_keys, "max_groups": max_groups},
    )
    if error_result is not None:
        return error_result
    assert context is not None

    try:
        payload = analysis_service.build_incident_bundle(
            context.snapshot_dir,
            context.metadata,
            source_keys=source_keys,
            max_groups=max_groups,
            requested_project_name=project_name,
            authorized_project_name=context.authorized_project_name,
            effective_project_name=context.effective_project_name,
            analysis_cautions=LOG_ANALYSIS_CAUTIONS,
            next_step_tips=INCIDENT_BUNDLE_NEXT_STEP_TIPS,
        )
    except ValueError as error:
        message = str(error)
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "build_incident_bundle",
                "error_message": message,
                "snapshot_id": snapshot_id,
                "project_name": project_name,
                "source_keys": source_keys,
                "max_groups": max_groups,
            },
        )
        return build_snapshot_tool_error_result(
            error_code="snapshot_source_key_not_found",
            message=message,
            retry_tips=[
                "Retry with a valid snapshot_id and source_keys for the authorized project.",
            ],
            details={
                "project_name": project_name,
                "snapshot_id": snapshot_id,
                "source_keys": source_keys_detail,
                "max_groups": max_groups,
            },
        )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "build_incident_bundle",
            "snapshot_id": payload.snapshot_id,
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


@mcp.tool(auth=require_scopes(LOGS_COLLECT_SCOPE))
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
