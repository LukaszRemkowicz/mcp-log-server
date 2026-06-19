"""MCP tool for bounded project scheduler provenance inspection."""

from __future__ import annotations

import logging

from fastmcp.tools.base import ToolResult
from mcp.types import ToolAnnotations

from auth.scopes import CONTAINER_FILES_READ_SCOPE
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from services.scheduled_jobs_service import (
    ScheduledJobInspection,
    ScheduledJobMatch,
    ScheduledJobsService,
    ScheduledJobWarning,
)
from tools.agent_hints import INSPECT_PROJECT_SCHEDULED_JOBS_TOOL_DESCRIPTION
from tools.models import (
    InspectProjectScheduledJobsPayload,
    ScheduledJobMatchPayload,
    ScheduledJobWarningPayload,
)

logger: logging.Logger = get_logger("tools.scheduled_jobs")
scheduled_jobs_service = ScheduledJobsService()
READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
)


def _scheduled_job_match_payload(match: ScheduledJobMatch) -> ScheduledJobMatchPayload:
    """Convert service scheduler evidence into the MCP response contract."""

    return ScheduledJobMatchPayload(
        scheduler_type=match.scheduler_type,
        path=match.path,
        line_number=match.line_number,
        schedule_context=match.schedule_context,
        command_text=match.command_text,
        output_paths=match.output_paths,
        matched_patterns=match.matched_patterns,
        visibility_warnings=match.visibility_warnings,
    )


def _scheduled_job_warning_payload(warning: ScheduledJobWarning) -> ScheduledJobWarningPayload:
    """Convert service scheduler warning into the MCP response contract."""

    return ScheduledJobWarningPayload(
        path=warning.path,
        warning_code=warning.warning_code,
        message=warning.message,
    )


def _build_scheduled_jobs_payload(
    *,
    requested_project_name: str | None,
    inspection: ScheduledJobInspection,
) -> InspectProjectScheduledJobsPayload:
    """Build the agent-facing scheduler provenance payload."""

    return InspectProjectScheduledJobsPayload(
        action="inspect_project_scheduled_jobs",
        requested_project_name=requested_project_name,
        project_name=inspection.project_name,
        patterns=inspection.patterns,
        scheduler_roots=inspection.scheduler_roots,
        truncated=inspection.truncated,
        matches=[_scheduled_job_match_payload(match) for match in inspection.matches],
        warnings=[_scheduled_job_warning_payload(warning) for warning in inspection.warnings],
    )


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=INSPECT_PROJECT_SCHEDULED_JOBS_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def inspect_project_scheduled_jobs(
    patterns: list[str] | None = None,
    project_name: str | None = None,
) -> ToolResult:
    """Inspect bounded cron/systemd scheduler files for one authorized project."""

    assert project_name is not None
    inspection = scheduled_jobs_service.inspect_project_scheduled_jobs(
        project_name=project_name,
        patterns=patterns,
    )
    payload = _build_scheduled_jobs_payload(
        requested_project_name=project_name,
        inspection=inspection,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_project_scheduled_jobs",
            "project_name": payload.project_name,
            "match_count": len(payload.matches),
            "warning_count": len(payload.warnings),
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
