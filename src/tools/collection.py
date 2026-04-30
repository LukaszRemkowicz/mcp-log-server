"""Project discovery and log collection MCP tools."""

from __future__ import annotations

import logging

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken
from fastmcp.tools.base import ToolResult

from auth.scopes import LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE
from conf import settings
from logging_config import get_logger
from manifests.loader import list_project_manifests
from services.log_collection import LogCollectionService
from services.log_snapshots import LogSnapshotService
from services.log_source_collection import LogSourceCollectionService
from tools.agent_hints import COLLECT_LOGS_TOOL_DESCRIPTION
from tools.errors import build_collect_logs_error_result
from tools.models import ProjectListEntry, SnapshotWorkspace
from tools.registry import workflow_discoverable_tool

logger: logging.Logger = get_logger("tools.collection")

source_collection_service = LogSourceCollectionService()


@workflow_discoverable_tool(PROJECTS_READ_SCOPE)
def list_projects() -> list[ProjectListEntry]:
    """List projects currently available through bundled manifest files.

    This is the lightweight discovery entrypoint for project-scoped log tools.
    Callers use it to learn:

    - which `project_name` values currently exist
    - which source keys belong to each project
    - whether a project exposes docker-backed, file-backed, or mixed sources

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
    manifests_dir = settings.MANIFEST_PATH.parent
    entries = [
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
        for manifest in list_project_manifests(manifests_dir)
    ]
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "list_projects",
            "project_count": len(entries),
        },
    )
    return entries


@workflow_discoverable_tool(
    LOGS_COLLECT_SCOPE,
    argument_default_overrides={"since": "24h"},
    mcp_description=COLLECT_LOGS_TOOL_DESCRIPTION,
)
def collect_logs(
    project_name: str | None = None,
    source_keys: list[str] | None = None,
    workspace: SnapshotWorkspace = "workflow",
    session_id: str | None = None,
    tail_lines: int | None = None,
    timestamps: bool = False,
    since: str | None = "24h",
    until: str | None = None,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Collect logs into a shared workflow snapshot or a caller-owned session snapshot.

    This is the first step in the snapshot-based log workflow. It performs
    deterministic collection, persists a snapshot, and returns both snapshot
    metadata and a small inline preview for immediate agent use.

    Choose the persistence mode with `workspace`:

    - `workspace="workflow"` writes to the shared workflow snapshot area
    - `workspace="session"` writes to a caller-owned session snapshot and
      requires a unique `session_id`
    """

    assert access_token is not None
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "collect_logs",
            "project_name": project_name,
            "source_keys": source_keys,
            "workspace": workspace,
            "session_id": session_id,
            "tail_lines": tail_lines,
            "timestamps": timestamps,
            "since": since,
            "until": until,
        },
    )
    collection_service = LogCollectionService(
        settings,
        access_token,
        snapshot_service=LogSnapshotService(settings, access_token),
        source_collector=source_collection_service.collect_source,
        tail_line_limiter=source_collection_service.limit_tail_lines,
    )
    try:
        payload = collection_service.build_payload(
            requested_project_name=project_name,
            requested_source_keys=source_keys,
            workspace=workspace,
            session_id=session_id,
            tail_lines=tail_lines,
            timestamps=timestamps,
            since=since,
            until=until,
        )
    except ValueError as error:
        message = str(error)
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "collect_logs",
                "error_message": message,
                "project_name": project_name,
                "workspace": workspace,
                "session_id": session_id,
                "since": since,
                "until": until,
            },
        )
        return build_collect_logs_error_result(
            message,
            settings=settings,
            access_token=access_token,
            project_name=project_name,
            workspace=workspace,
            session_id=session_id,
        )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "collect_logs",
            "project_name": payload.effective_project_name,
            "workspace": payload.workspace,
            "session_id": payload.session_id,
            "snapshot_id": payload.snapshot_id,
            "source_count": len(payload.sources),
            "warning_count": len(payload.warnings),
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
