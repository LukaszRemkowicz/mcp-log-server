"""Project discovery and log collection MCP tools."""

from __future__ import annotations

import logging
from typing import cast

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken
from fastmcp.tools.base import ToolResult

from auth.scopes import LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE
from conf import settings
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from services.log_collection import BuildLogsError, LogCollectionService
from services.project_authorization import (
    ProjectAccessScope,
    ProjectAuthorizationError,
    ProjectAuthorizationService,
)
from services.project_manifest import ProjectManifestError, ProjectManifestService
from tools.agent_hints import COLLECT_LOGS_TOOL_DESCRIPTION
from tools.errors import build_collect_logs_error_result
from tools.models import CollectLogsPayload, ProjectManifestSummary, SnapshotWorkspace
from utils.mcp_errors import build_agent_tool_error_result

logger: logging.Logger = get_logger("tools.collection")

collection_service = LogCollectionService()
manifest_service = ProjectManifestService()
project_authorization_service = ProjectAuthorizationService()


@workflow_discoverable_tool(PROJECTS_READ_SCOPE)
async def list_projects(
    access_token: AccessToken | None = CurrentAccessToken(),
) -> list[ProjectManifestSummary]:
    """List projects currently available through persisted manifest rows.

    This is the lightweight discovery entrypoint for project-scoped log tools.
    Callers use it to learn:

    - which `project_name` values currently exist
    - which source keys belong to each project

    This tool intentionally returns only summary metadata, not the full raw
    manifest contents.
    """

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "list_projects",
        },
    )
    assert access_token is not None
    project_access_scope: ProjectAccessScope | ProjectAuthorizationError = (
        project_authorization_service.get_required_project_access_scope_or_error(access_token)
    )
    if isinstance(project_access_scope, ProjectAuthorizationError):
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "list_projects",
                "error_message": project_access_scope.message,
            },
        )
        return cast(
            list[ProjectManifestSummary],
            build_agent_tool_error_result(
                error_code=project_access_scope.error_code,
                message=project_access_scope.message,
                retry_tips=project_access_scope.retry_tips,
            ),
        )

    if project_access_scope.all_projects:
        all_project_summaries: list[ProjectManifestSummary] = (await manifest_service.all()).root
        logger.info(
            "tool result",
            extra={
                "event": "tool_result",
                "tool_name": "list_projects",
                "project_count": len(all_project_summaries),
            },
        )
        return all_project_summaries

    project_summaries: list[ProjectManifestSummary] = []
    for project_name in sorted(project_access_scope.allowed_projects):
        manifest_context = await manifest_service.get(project_name)
        if manifest_context is not None:
            project_summaries.append(
                ProjectManifestSummary(
                    project_name=manifest_context.project_name,
                    project_summary=manifest_context.manifest.project_summary,
                    source_keys=[source.source_key for source in manifest_context.manifest.sources],
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


@workflow_discoverable_tool(
    LOGS_COLLECT_SCOPE,
    argument_default_overrides={"since": settings.DEFAULT_LOG_WINDOW},
    mcp_description=COLLECT_LOGS_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def collect_logs(
    project_names: list[str] | None = None,
    source_keys: list[str] | None = None,
    workspace: SnapshotWorkspace = "workflow",
    session_id: str | None = None,
    since: str | None = settings.DEFAULT_LOG_WINDOW,
    until: str | None = None,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Collect logs into workflow or session artifacts for one or more projects.

    This is the first step in the snapshot-based log workflow. It performs
    deterministic collection and persists per-project artifacts for later
    snapshot follow-up tools.

    Public request shape:

    - use `project_names`, even for one project
    - `workspace="workflow"` writes shared workflow artifacts
    - `workspace="session"` writes investigation artifacts under one shared
      `session_id`; MCP creates one when the request omits it
    - the fixed workflow agent is not allowed to use `workspace="session"`
    - reuse the returned `session_id` when the same investigation later needs
      logs from another project or a narrower time window

    Important runtime note:

    - for real MCP/API calls, middleware authorizes and normalizes
      `project_names` before this tool runs
    - for real MCP/API `collect_logs` calls, middleware also injects a generated
      `session_id` when `workspace="session"` and the request omitted it
    - when `project_names` is omitted or empty in the HTTP path, middleware may
      inject all projects accessible to the current JWT
    - direct Python calls to `collect_logs(...)` do not go through middleware,
      so missing or empty `project_names` is treated as programming error and
      this function will fail instead of returning a tool error
    """

    assert access_token is not None
    defaults = collection_service.normalize_params(
        source_keys=source_keys,
        since=since,
    )
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "collect_logs",
            "project_names": project_names,
            "source_keys": defaults.source_keys,
            "workspace": workspace,
            "session_id": session_id,
            "since": defaults.since,
            "until": until,
        },
    )
    assert project_names, "collect_logs expects middleware-normalized non-empty project_names"

    project_payloads = []
    for project_name in project_names:
        manifest_result = await manifest_service.get_or_error(project_name)
        if isinstance(manifest_result, ProjectManifestError):
            logger.info(
                "tool error",
                extra={
                    "event": "tool_error",
                    "tool_name": "collect_logs",
                    "error_message": manifest_result.message,
                    "project_names": project_names,
                    "workspace": workspace,
                    "session_id": session_id,
                    "since": defaults.since,
                    "until": until,
                },
            )
            return build_collect_logs_error_result(
                manifest_result.message,
                settings=settings,
                access_token=access_token,
                project_names=project_names,
                workspace=workspace,
                session_id=session_id,
            )

        manifest_sources = manifest_service.get_manifest_source_keys(
            manifest_result.manifest,
            defaults.source_keys,
        )
        project_payload = await collection_service.build_logs(
            manifest=manifest_result.manifest,
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
            return build_collect_logs_error_result(
                project_payload.message,
                settings=settings,
                access_token=access_token,
                project_names=project_names,
                workspace=workspace,
                session_id=session_id,
            )
        project_payloads.append(project_payload)

    payload = CollectLogsPayload(
        action="collect_logs",
        workspace=workspace,
        session_id=session_id if workspace == "session" else None,
        requested_project_names=project_names,
        next_step_tips=[
            "Use session_id and project_name for later session follow-up tools.",
            "Use project_name alone for the newest workflow artifact.",
            "Use archive_name plus project_name only when you need an archived workflow artifact.",
        ],
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
