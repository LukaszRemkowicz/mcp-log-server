"""Project discovery and log collection MCP tools."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import cast

from fastmcp.server.auth import require_scopes
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools.base import ToolResult

from app import mcp
from auth.mcp_authorized_manifests import AuthorizedProjectManifests
from auth.mcp_caller_context import AuthenticatedMcpCaller, get_request_mcp_caller
from auth.scopes import LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE
from conf import settings
from core.types import LogWorkspace
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from manifests.models import Manifest
from services.log_collection import BuildLogsError, LogCollectionService
from services.log_collection_tasks import LogCollectionTaskService
from services.project_manifest import ProjectManifestError, ProjectManifestService
from services.scheduled_jobs_service import ScheduledJobInspection, ScheduledJobsService
from tasks import collect_logs_task
from tools.agent_hints import (
    COLLECT_LOGS_NEXT_STEP_TIPS,
    COLLECT_LOGS_TOOL_DESCRIPTION,
    EXPLAIN_PROJECT_SOURCE_TOOL_DESCRIPTION,
    GET_LOG_COLLECTION_STATUS_TOOL_DESCRIPTION,
    READ_PROJECT_MANIFEST_TOOL_DESCRIPTION,
    START_LOG_COLLECTION_TOOL_DESCRIPTION,
)
from tools.errors import build_collect_logs_error_details, build_collect_logs_error_result
from tools.models import (
    CollectLogsPayload,
    ExplainProjectSourcePayload,
    LogCollectionTaskStatusPayload,
    ProjectManifestSourcePayload,
    ProjectManifestSummary,
    ReadProjectManifestPayload,
    ScheduledJobMatchPayload,
    ScheduledJobWarningPayload,
    SnapshotWorkspace,
    SourceProducerPayload,
    SourceSchedulerHintsPayload,
)
from utils.mcp_errors import build_agent_tool_error_result
from utils.types import JSONObject

logger: logging.Logger = get_logger("tools.collection")

collection_service = LogCollectionService()
log_collection_task_service = LogCollectionTaskService()
manifest_service = ProjectManifestService()
scheduled_jobs_service = ScheduledJobsService()


def _get_authorized_manifests() -> AuthorizedProjectManifests:
    """Return request-state manifests prepared by AuthorizedManifestsMiddleware."""

    request = get_http_request()
    return cast(
        AuthorizedProjectManifests,
        request.state.authorized_manifests,
    )


def _build_unknown_project_manifest_error(project_name: str) -> ProjectManifestError:
    """Return the standard missing-manifest error for this tool module."""

    return ProjectManifestError(
        message=(
            f"Unknown project {project_name!r}. No persisted manifest was found for that project."
        )
    )


@workflow_discoverable_tool(PROJECTS_READ_SCOPE)
async def list_projects() -> list[ProjectManifestSummary]:
    """List projects currently available through persisted manifest rows.

    This is the lightweight discovery entrypoint for project-scoped log tools.
    Callers use it to learn:

    - which `project_name` values currently exist
    - which source keys belong to each project

    This tool intentionally returns only summary metadata, not the full raw
    manifest contents.
    """

    authorized_manifests = _get_authorized_manifests()

    project_summaries: list[ProjectManifestSummary] = []
    for project_name in sorted(authorized_manifests.manifests):
        manifest = authorized_manifests.manifests[project_name]
        project_summaries.append(
            ProjectManifestSummary(
                project_name=manifest.project_key,
                project_summary=manifest.project_summary,
                source_keys=[source.source_key for source in manifest.sources],
            )
        )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "list_projects",
            "project_count": len(project_summaries),
        },
    )
    return project_summaries


def _build_project_manifest_source_payloads(
    manifest: Manifest,
    source_key: str | None,
) -> list[ProjectManifestSourcePayload] | ToolResult:
    """Return source payloads for one manifest, optionally filtered by source key."""

    available_source_keys = [source.source_key for source in manifest.sources]
    sources = manifest.sources
    if source_key is not None:
        sources = [source for source in manifest.sources if source.source_key == source_key]
        if not sources:
            return build_agent_tool_error_result(
                error_code="unknown_source_key",
                message="Requested source_key was not found in the configured manifest.",
                retry_tips=[
                    "Call list_projects to discover valid source_keys for this project.",
                    "Retry with a source_key returned by read_project_manifest or omit source_key.",
                ],
                details=cast(
                    JSONObject,
                    {
                        "project_name": manifest.project_key,
                        "source_key": source_key,
                        "available_source_keys": available_source_keys,
                    },
                ),
            )

    return [
        ProjectManifestSourcePayload.model_validate(source.model_dump(mode="json"))
        for source in sources
    ]


def _source_producer_payload(source: ProjectManifestSourcePayload) -> SourceProducerPayload:
    """Return configured producer metadata for one source."""

    has_metadata = any(
        [
            source.expected_producer_type,
            source.scheduler_patterns,
        ]
    )
    return SourceProducerPayload(
        metadata_status="configured" if has_metadata else "missing",
        expected_producer_type=source.expected_producer_type,
        scheduler_patterns=source.scheduler_patterns,
    )


def _scheduler_hint_patterns(source: ProjectManifestSourcePayload) -> list[str]:
    """Return configured literal patterns for scheduler provenance lookup."""

    if source.scheduler_patterns:
        return source.scheduler_patterns
    return []


def _source_scheduler_hints_payload(
    inspection: ScheduledJobInspection,
) -> SourceSchedulerHintsPayload:
    """Convert scheduler service evidence into a source-explanation payload."""

    return SourceSchedulerHintsPayload(
        inspected_patterns=inspection.patterns,
        matches=[
            ScheduledJobMatchPayload(
                scheduler_type=match.scheduler_type,
                path=match.path,
                line_number=match.line_number,
                schedule_context=match.schedule_context,
                command_text=match.command_text,
                output_paths=match.output_paths,
                matched_patterns=match.matched_patterns,
                visibility_warnings=match.visibility_warnings,
            )
            for match in inspection.matches
        ],
        warnings=[
            ScheduledJobWarningPayload(
                path=warning.path,
                warning_code=warning.warning_code,
                message=warning.message,
            )
            for warning in inspection.warnings
        ],
        truncated=inspection.truncated,
    )


def _source_explanation_next_steps(
    source: ProjectManifestSourcePayload,
    scheduler_hints: SourceSchedulerHintsPayload | None,
) -> list[str]:
    """Return deterministic next-step tips for one source explanation."""

    tips = [
        "Use collect_logs with this source_key to collect the configured source.",
    ]
    if source.source_type == "file":
        tips.append(
            "Use stat_project_path to inspect whether the configured host file "
            "exists and is readable."
        )
        tips.append(
            "Use list_project_directory to inspect rotated files beside the configured source."
        )
    else:
        tips.append(
            "Use inspect_containers_health or inspect_container_detail to inspect "
            "the configured container source."
        )
    if source.scheduler_patterns:
        tips.append(
            "Use inspect_project_scheduled_jobs with the returned scheduler_patterns "
            "for more scheduler detail."
        )
        if scheduler_hints is not None and not scheduler_hints.matches:
            tips.append(
                "No scheduler evidence matched the configured patterns; verify mounts "
                "and producer naming."
            )
    elif source.expected_producer_type in {"cron", "systemd"}:
        tips.append("Scheduler producer type is configured, but scheduler_patterns are missing.")
    else:
        tips.append(
            "Producer metadata is not configured for this source; treat producer details "
            "as unknown."
        )
    return tips


@workflow_discoverable_tool(
    PROJECTS_READ_SCOPE,
    mcp_description=READ_PROJECT_MANIFEST_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def read_project_manifest(
    project_name: str,
    source_key: str | None = None,
) -> ToolResult:
    """Read the persisted manifest contract for one authorized project."""

    authorized_manifests = _get_authorized_manifests()
    manifest = authorized_manifests.manifests.get(project_name)
    if manifest is None:
        return build_agent_tool_error_result(
            error_code="unknown_project_manifest",
            message="No persisted manifest was found for the requested project.",
            retry_tips=[
                "Call list_projects to discover projects visible to this MCP caller.",
                "Retry with a project_name returned by list_projects.",
            ],
            details=cast(JSONObject, {"project_name": project_name}),
        )

    source_payloads = _build_project_manifest_source_payloads(manifest, source_key)
    if isinstance(source_payloads, ToolResult):
        return source_payloads

    payload = ReadProjectManifestPayload(
        action="read_project_manifest",
        project_name=manifest.project_key,
        project_summary=manifest.project_summary,
        requested_source_key=source_key,
        source_keys=[source.source_key for source in source_payloads],
        static_asset_paths=manifest.static_asset_paths,
        static_asset_extensions=manifest.static_asset_extensions,
        sources=source_payloads,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "read_project_manifest",
            "project_name": payload.project_name,
            "requested_source_key": payload.requested_source_key,
            "source_count": len(payload.sources),
        },
    )
    return ToolResult(content=[], structured_content=payload)


@workflow_discoverable_tool(
    LOGS_COLLECT_SCOPE,
    argument_default_overrides={"since": settings.DEFAULT_LOG_WINDOW},
    mcp_description=START_LOG_COLLECTION_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def start_log_collection(
    project_names: list[str] | None = None,
    source_keys: list[str] | None = None,
    workspace: SnapshotWorkspace = LogWorkspace.WORKFLOW,
    session_id: str | None = None,
    since: str | None = settings.DEFAULT_LOG_WINDOW,
    until: str | None = None,
) -> ToolResult:
    """Queue service-level log collection tasks and return the polling session_id."""

    if not project_names:
        raise AssertionError(
            "start_log_collection expects middleware-normalized non-empty project_names"
        )
    assert session_id, "start_log_collection expects middleware-injected session_id"
    defaults = collection_service.normalize_params(
        source_keys=source_keys,
        since=since,
    )
    authorized_manifests = _get_authorized_manifests()

    for project_name in project_names:
        manifest: Manifest | None = authorized_manifests.manifests.get(project_name)
        if manifest is None:
            manifest_error = _build_unknown_project_manifest_error(project_name)
            logger.info(
                "tool error",
                extra={
                    "event": "tool_error",
                    "tool_name": "start_log_collection",
                    "error_message": manifest_error.message,
                    "project_names": project_names,
                    "workspace": workspace,
                    "session_id": session_id,
                    "since": defaults.since,
                    "until": until,
                },
            )
            return build_collect_logs_error_result(
                manifest_error.message,
                settings=settings,
                project_names=project_names,
                workspace=workspace,
                session_id=session_id,
            )

        manifest_sources = manifest_service.get_manifest_source_keys(
            manifest,
            defaults.source_keys,
        )
        if not manifest_sources.sources and manifest_sources.missing_source_keys:
            return build_agent_tool_error_result(
                error_code="unknown_source_keys",
                message="No requested source_keys were found in the configured manifest.",
                retry_tips=[
                    "Call list_projects to discover valid source_keys for this project.",
                    "Retry with source_keys from the project manifest, or use source_keys=['all'].",
                ],
                details=cast(
                    JSONObject,
                    {
                        "project_name": project_name,
                        "requested_source_keys": defaults.source_keys,
                        "unknown_requested_source_keys": manifest_sources.missing_source_keys,
                    },
                ),
            )

        project_start = perf_counter()
        logger.info(
            "start_log_collection project queue start",
            extra={
                "event": "start_log_collection_project_queue_start",
                "tool_name": "start_log_collection",
                "project_names": project_names,
                "project_name": project_name,
                "workspace": workspace,
                "session_id": session_id,
                "requested_source_keys": defaults.source_keys,
                "resolved_source_keys": manifest_sources.source_keys,
                "missing_source_keys": manifest_sources.missing_source_keys,
                "source_count": len(manifest_sources.sources),
                "since": defaults.since,
                "until": until,
            },
        )
        queued_task = await collect_logs_task.apply_async(
            kwargs={
                "manifest": manifest,
                "sources": manifest_sources.sources,
                "missing_source_keys": manifest_sources.missing_source_keys,
                "source_keys": manifest_sources.source_keys,
                "workspace": workspace,
                "session_id": session_id,
                "since": defaults.since,
                "until": until,
            }
        )
        logger.info(
            "start_log_collection project queue done",
            extra={
                "event": "start_log_collection_project_queue_done",
                "tool_name": "start_log_collection",
                "project_names": project_names,
                "project_name": project_name,
                "workspace": workspace,
                "session_id": session_id,
                "requested_source_keys": defaults.source_keys,
                "resolved_source_keys": manifest_sources.source_keys,
                "missing_source_keys": manifest_sources.missing_source_keys,
                "source_count": len(manifest_sources.sources),
                "since": defaults.since,
                "until": until,
                "task_id": str(queued_task.id),
                "duration_seconds": round(perf_counter() - project_start, 3),
            },
        )

    payload = {
        "action": "start_log_collection",
        "status": "started",
        "workspace": workspace,
        "session_id": session_id,
        "next_step_tips": [
            "Call get_log_collection_status with this session_id until status is completed.",
            "When completed, use the returned collect_logs result or session snapshot tools.",
        ],
    }
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "start_log_collection",
            "workspace": workspace,
            "session_id": session_id,
            "project_count": len(project_names),
        },
    )
    return ToolResult(content=[], structured_content=payload)


@mcp.tool(
    auth=require_scopes(LOGS_COLLECT_SCOPE),
    description=GET_LOG_COLLECTION_STATUS_TOOL_DESCRIPTION,
)
async def get_log_collection_status(session_id: str) -> ToolResult:
    """Return background log-collection task status for this caller/session."""

    caller: AuthenticatedMcpCaller = get_request_mcp_caller()
    payload: LogCollectionTaskStatusPayload | None = await log_collection_task_service.get_status(
        caller_id=caller.caller_id,
        session_id=session_id,
    )
    if payload is None:
        return build_agent_tool_error_result(
            error_code="log_collection_task_not_found",
            message="No background log collection was found for this session_id.",
            retry_tips=[
                "Call start_log_collection first and reuse the returned session_id.",
                "Retry with a session_id owned by the current MCP caller.",
            ],
            details=cast(JSONObject, {"session_id": session_id}),
        )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "get_log_collection_status",
            "workspace": payload["workspace"],
            "session_id": session_id,
            "task_count": payload["task_count"],
        },
    )
    return ToolResult(
        content=[],
        structured_content=payload.model_dump(mode="json", exclude_none=True),
    )


@workflow_discoverable_tool(
    PROJECTS_READ_SCOPE,
    mcp_description=EXPLAIN_PROJECT_SOURCE_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def explain_project_source(
    project_name: str,
    source_key: str,
) -> ToolResult:
    """Explain one manifest source contract and configured producer provenance."""

    authorized_manifests = _get_authorized_manifests()
    manifest = authorized_manifests.manifests.get(project_name)
    if manifest is None:
        return build_agent_tool_error_result(
            error_code="unknown_project_manifest",
            message="No persisted manifest was found for the requested project.",
            retry_tips=[
                "Call list_projects to discover projects visible to this MCP caller.",
                "Retry with a project_name returned by list_projects.",
            ],
            details=cast(JSONObject, {"project_name": project_name}),
        )

    source_payloads = _build_project_manifest_source_payloads(manifest, source_key)
    if isinstance(source_payloads, ToolResult):
        return source_payloads
    source = source_payloads[0]
    scheduler_hints: SourceSchedulerHintsPayload | None = None
    scheduler_patterns = _scheduler_hint_patterns(source)
    if scheduler_patterns:
        scheduler_hints = _source_scheduler_hints_payload(
            scheduled_jobs_service.inspect_project_scheduled_jobs(
                project_name=manifest.project_key,
                patterns=scheduler_patterns,
            )
        )

    payload = ExplainProjectSourcePayload(
        action="explain_project_source",
        project_name=manifest.project_key,
        project_summary=manifest.project_summary,
        source_key=source.source_key,
        source=source,
        producer=_source_producer_payload(source),
        scheduler_hints=scheduler_hints,
        next_step_tips=_source_explanation_next_steps(source, scheduler_hints),
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "explain_project_source",
            "project_name": payload.project_name,
            "source_key": payload.source_key,
            "producer_metadata_status": payload.producer.metadata_status,
            "scheduler_match_count": (
                len(payload.scheduler_hints.matches) if payload.scheduler_hints else None
            ),
        },
    )
    return ToolResult(
        content=[],
        structured_content=payload.model_dump(mode="json", exclude_none=True),
    )


@mcp.tool(
    auth=require_scopes(LOGS_COLLECT_SCOPE),
    description=COLLECT_LOGS_TOOL_DESCRIPTION,
    exclude_args=["workspace"],
)
@project_authorized_tool
async def collect_logs(
    project_names: list[str] | None = None,
    source_keys: list[str] | None = None,
    workspace: SnapshotWorkspace = LogWorkspace.WORKFLOW,
    session_id: str | None = None,
    since: str | None = settings.DEFAULT_LOG_WINDOW,
    until: str | None = None,
) -> ToolResult:
    """Collect logs into workflow or session artifacts for one or more projects.

    This is the first step in the snapshot-based log workflow. It performs
    deterministic collection and persists per-project artifacts for later
    snapshot follow-up tools.

    Public request shape:

    - use `project_names`, even for one project
    - middleware injects `workspace` from the authenticated caller row
    - session callers write investigation artifacts under one shared `session_id`
    - reuse the returned `session_id` when the same investigation later needs
      logs from another project or a narrower time window

    Important runtime note:

    - for real MCP/API calls, middleware authorizes and normalizes
      `project_names` before this tool runs
    - for real MCP/API `collect_logs` calls, middleware injects the caller-owned
      `workspace` and an effective `session_id`
    - when `project_names` is omitted or empty in the HTTP path, project
      authorization may use all projects accessible to the request-state caller
    - direct Python calls to `collect_logs(...)` do not go through middleware,
      so missing or empty `project_names` is treated as programming error and
      this function will fail instead of returning a tool error
    """

    defaults = collection_service.normalize_params(
        source_keys=source_keys,
        since=since,
    )
    assert project_names, "collect_logs expects middleware-normalized non-empty project_names"
    authorized_manifests = _get_authorized_manifests()

    project_payloads = []
    for project_name in project_names:
        manifest: Manifest | None = authorized_manifests.manifests.get(project_name)
        if manifest is None:
            manifest_error = _build_unknown_project_manifest_error(project_name)
            logger.info(
                "tool error",
                extra={
                    "event": "tool_error",
                    "tool_name": "collect_logs",
                    "error_message": manifest_error.message,
                    "project_names": project_names,
                    "workspace": workspace,
                    "session_id": session_id,
                    "since": defaults.since,
                    "until": until,
                },
            )
            return build_collect_logs_error_result(
                manifest_error.message,
                settings=settings,
                project_names=project_names,
                workspace=workspace,
                session_id=session_id,
            )

        manifest_sources = manifest_service.get_manifest_source_keys(
            manifest,
            defaults.source_keys,
        )
        if not manifest_sources.sources and manifest_sources.missing_source_keys:
            return build_agent_tool_error_result(
                error_code="unknown_source_keys",
                message="No requested source_keys were found in the configured manifest.",
                retry_tips=[
                    "Call list_projects to discover valid source_keys for this project.",
                    "Retry with source_keys from the project manifest, or use source_keys=['all'].",
                ],
                details=cast(
                    JSONObject,
                    {
                        "project_name": project_name,
                        "requested_source_keys": defaults.source_keys,
                        "unknown_requested_source_keys": manifest_sources.missing_source_keys,
                    },
                ),
            )
        project_start = perf_counter()
        logger.info(
            "collect_logs project start",
            extra={
                "event": "collect_logs_project_start",
                "tool_name": "collect_logs",
                "project_names": project_names,
                "project_name": project_name,
                "workspace": workspace,
                "session_id": session_id,
                "requested_source_keys": defaults.source_keys,
                "resolved_source_keys": manifest_sources.source_keys,
                "missing_source_keys": manifest_sources.missing_source_keys,
                "source_count": len(manifest_sources.sources),
                "since": defaults.since,
                "until": until,
            },
        )
        project_payload = await collection_service.build_logs(
            manifest=manifest,
            sources=manifest_sources.sources,
            missing_source_keys=manifest_sources.missing_source_keys,
            source_keys=manifest_sources.source_keys,
            workspace=workspace,
            session_id=session_id,
            since=defaults.since,
            until=until,
        )
        if isinstance(project_payload, BuildLogsError):
            duration_seconds = round(perf_counter() - project_start, 3)
            logger.info(
                "tool error",
                extra={
                    "event": "tool_error",
                    "tool_name": "collect_logs",
                    "error_message": project_payload.message,
                    "project_names": project_names,
                    "project_name": project_name,
                    "workspace": workspace,
                    "session_id": session_id,
                    "since": defaults.since,
                    "until": until,
                    "duration_seconds": duration_seconds,
                },
            )
            logger.info(
                "collect_logs project error",
                extra={
                    "event": "collect_logs_project_error",
                    "tool_name": "collect_logs",
                    "error_code": project_payload.error_code,
                    "error_message": project_payload.message,
                    "project_names": project_names,
                    "project_name": project_name,
                    "workspace": workspace,
                    "session_id": session_id,
                    "requested_source_keys": defaults.source_keys,
                    "resolved_source_keys": manifest_sources.source_keys,
                    "missing_source_keys": manifest_sources.missing_source_keys,
                    "source_count": len(manifest_sources.sources),
                    "since": defaults.since,
                    "until": until,
                    "duration_seconds": duration_seconds,
                },
            )
            return build_agent_tool_error_result(
                error_code=project_payload.error_code,
                message=project_payload.message,
                retry_tips=project_payload.retry_tips,
                details=build_collect_logs_error_details(
                    project_payload.error_code,
                    settings=settings,
                    project_names=project_names,
                    workspace=workspace,
                    session_id=session_id,
                ),
            )
        logger.info(
            "collect_logs project done",
            extra={
                "event": "collect_logs_project_done",
                "tool_name": "collect_logs",
                "project_names": project_names,
                "project_name": project_name,
                "workspace": workspace,
                "session_id": session_id,
                "requested_source_keys": defaults.source_keys,
                "resolved_source_keys": manifest_sources.source_keys,
                "missing_source_keys": manifest_sources.missing_source_keys,
                "source_count": len(manifest_sources.sources),
                "since": defaults.since,
                "until": until,
                "duration_seconds": round(perf_counter() - project_start, 3),
            },
        )
        project_payloads.append(project_payload)

    payload = CollectLogsPayload(
        action="collect_logs",
        workspace=workspace,
        session_id=session_id,
        requested_project_names=project_names,
        next_step_tips=COLLECT_LOGS_NEXT_STEP_TIPS,
        projects=project_payloads,
    )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "collect_logs",
            "workspace": payload.workspace,
            "session_id": payload.session_id,
            "project_count": len(payload.projects),
        },
    )
    return ToolResult(
        content=[],
        structured_content=payload.model_dump(mode="json", exclude_none=True),
    )
