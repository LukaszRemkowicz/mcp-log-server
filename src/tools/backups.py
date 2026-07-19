"""Codex/session-only MCP tool for bounded project backup inspection."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastmcp.tools.base import ToolResult
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from app import mcp
from auth.scopes import CONTAINER_FILES_READ_SCOPE
from conf import settings
from decorators import project_authorized_tool, run_in_thread
from logging_config import get_logger
from manifests.models import ProjectBackupInspectionMetadata
from services.backup_inspection_service import BackupInspectionService
from services.project_manifest import ProjectManifestError, ProjectManifestService
from services.schemas import INTEGRITY_NOTE, BackupInspection
from utils.mcp_errors import build_agent_tool_error_result

logger: logging.Logger = get_logger("tools.backups")
backup_inspection_service = BackupInspectionService()
project_manifest_service = ProjectManifestService()
INSPECT_PROJECT_BACKUPS_DESCRIPTION = (
    "Inspect bounded filesystem metadata for configured project backup files. "
    "Returns only the newest filename, age, size, scanned-match count, scan completeness, "
    "and status; it never reads backup contents and does not independently verify "
    "integrity."
)
READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
)


@run_in_thread
def _inspect_configured_backups(
    configuration: ProjectBackupInspectionMetadata,
) -> BackupInspection:
    """Inspect configured backup metadata in a worker thread."""

    return backup_inspection_service.inspect(
        configuration=configuration,
        allowed_roots=list(settings.BACKUP_INSPECTION_ROOTS),
    )


class BackupInspectionWarningPayload(BaseModel):
    """One sanitized backup inspection warning."""

    model_config = ConfigDict(extra="forbid")

    warning_code: str
    message: str


class InspectProjectBackupsPayload(BaseModel):
    """Structured response returned by `inspect_project_backups`."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["inspect_project_backups"]
    requested_project_name: str | None
    project_name: str
    status: Literal["current", "stale", "missing", "unavailable", "not_configured"]
    latest_filename: str | None
    latest_age_seconds: int | None
    latest_size_bytes: int | None
    backup_count: int
    scan_complete: bool
    integrity_note: str
    warnings: list[BackupInspectionWarningPayload]


def codex_backup_auth(context: Any) -> bool:
    """Allow backup discovery/calls only to Codex tokens with the inspection scope."""

    token = context.token
    if token is None or CONTAINER_FILES_READ_SCOPE not in token.scopes:
        return False
    return token.client_id != "workflow-agent" and token.claims.get("client_type") == "codex"


@mcp.tool(
    auth=codex_backup_auth,
    description=INSPECT_PROJECT_BACKUPS_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def inspect_project_backups(project_name: str | None = None) -> ToolResult:
    """Inspect backup-file metadata for one authorized project."""

    assert project_name is not None
    manifest_context = await project_manifest_service.get_or_error(project_name)
    if isinstance(manifest_context, ProjectManifestError):
        return build_agent_tool_error_result(
            error_code="project_manifest_not_found",
            message=manifest_context.message,
            retry_tips=[
                "Retry with a project returned by list_projects.",
                "Ask an operator to upload the project manifest before retrying.",
            ],
            details={"project_name": project_name},
        )

    configuration = (
        manifest_context.manifest.deployment.backup_inspection
        if manifest_context.manifest.deployment is not None
        else None
    )
    if configuration is None:
        payload = InspectProjectBackupsPayload(
            action="inspect_project_backups",
            requested_project_name=project_name,
            project_name=manifest_context.project_name,
            status="not_configured",
            latest_filename=None,
            latest_age_seconds=None,
            latest_size_bytes=None,
            backup_count=0,
            scan_complete=True,
            integrity_note=INTEGRITY_NOTE,
            warnings=[],
        )
    else:
        inspection = await _inspect_configured_backups(configuration)
        payload = InspectProjectBackupsPayload(
            action="inspect_project_backups",
            requested_project_name=project_name,
            project_name=manifest_context.project_name,
            status=inspection.status,
            latest_filename=inspection.latest_filename,
            latest_age_seconds=inspection.latest_age_seconds,
            latest_size_bytes=inspection.latest_size_bytes,
            backup_count=inspection.backup_count,
            scan_complete=inspection.scan_complete,
            integrity_note=inspection.integrity_note,
            warnings=[
                BackupInspectionWarningPayload(
                    warning_code=warning.warning_code,
                    message=warning.message,
                )
                for warning in inspection.warnings
            ],
        )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_project_backups",
            "project_name": payload.project_name,
            "status": payload.status,
            "backup_count": payload.backup_count,
            "scan_complete": payload.scan_complete,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
