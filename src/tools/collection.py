"""Project discovery and log collection MCP tools."""

from __future__ import annotations

import logging
from typing import cast

from fastmcp.server.dependencies import get_http_request
from fastmcp.tools.base import ToolResult

from auth.mcp_authorized_manifests import AuthorizedProjectManifests
from auth.scopes import LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE
from conf import settings
from core.types import LogWorkspace
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from manifests.models import Manifest
from services.log_collection import BuildLogsError, LogCollectionService
from services.project_manifest import ProjectManifestError, ProjectManifestService
from tools.agent_hints import (
    COLLECT_LOGS_NEXT_STEP_TIPS,
    COLLECT_LOGS_TOOL_DESCRIPTION,
    READ_PROJECT_MANIFEST_TOOL_DESCRIPTION,
)
from tools.errors import build_collect_logs_error_details, build_collect_logs_error_result
from tools.models import (
    CollectLogsPayload,
    ProjectManifestSourcePayload,
    ProjectManifestSummary,
    ReadProjectManifestPayload,
    SnapshotWorkspace,
)
from utils.mcp_errors import build_agent_tool_error_result
from utils.types import JSONObject

logger: logging.Logger = get_logger("tools.collection")

collection_service = LogCollectionService()
manifest_service = ProjectManifestService()


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
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    LOGS_COLLECT_SCOPE,
    argument_default_overrides={"since": settings.DEFAULT_LOG_WINDOW},
    mcp_description=COLLECT_LOGS_TOOL_DESCRIPTION,
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
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
