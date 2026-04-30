"""Pure helpers for persisted log snapshot metadata and file-system layout.

These helpers intentionally avoid service state. They encode deterministic
rules for:

- converting collected sources into persisted metadata entries
- loading snapshot metadata from disk
- resolving workflow/session snapshot ids into directories
- re-anchoring file metadata back into the authorized snapshot directory

`LogSnapshotService` uses them as building blocks while keeping orchestration,
authorization, persistence, and grep execution in the service layer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp.server.auth import AccessToken
from fastmcp.tools.base import ToolResult

from conf import settings
from tools.models import CollectedSourcePayload, LogSnapshotFilePayload, LogSnapshotMetadata
from tools.utils import (
    SNAPSHOT_ID_FILE_NAME,
    SNAPSHOT_METADATA_FILE_NAME,
    load_snapshot_metadata_from_json,
)
from utils.mcp_errors import build_agent_tool_error_result
from utils.types import JSONObject

if TYPE_CHECKING:
    from services.log_snapshots import AuthorizedSnapshotContext


def build_snapshot_file_payloads(
    collected_sources: list[CollectedSourcePayload],
) -> list[LogSnapshotFilePayload]:
    """Convert collected sources into persisted snapshot file metadata entries."""

    file_payloads: list[LogSnapshotFilePayload] = []
    for source in collected_sources:
        if source.output_file is None:
            continue
        file_payloads.append(
            LogSnapshotFilePayload(
                source_key=source.source_key,
                source_type=source.source_type,
                description=source.description,
                target=source.target,
                stream=source.stream,
                file_name=Path(source.output_file).name,
                output_file=source.output_file,
                line_count=source.line_count,
                byte_count=source.byte_count,
            )
        )
    return file_payloads


def read_snapshot_metadata(snapshot_dir: Path) -> LogSnapshotMetadata:
    """Load the persisted snapshot metadata JSON for one snapshot directory."""

    metadata_file = snapshot_dir / SNAPSHOT_METADATA_FILE_NAME
    if not metadata_file.exists():
        raise ValueError("Requested log snapshot metadata was not found.")
    return load_snapshot_metadata_from_json(metadata_file.read_text(encoding="utf-8"))


def resolve_workflow_snapshot_dir(project_output_dir: Path, snapshot_id: str) -> Path:
    """Resolve one workflow snapshot id into either latest or an archive dir."""

    workflow_root_dir = project_output_dir / "workflow"
    latest_output_dir = workflow_root_dir / "latest"
    archive_dir = workflow_root_dir / "archive"

    if snapshot_id == "latest":
        if not latest_output_dir.exists():
            raise ValueError("Requested workflow log snapshot was not found.")
        return latest_output_dir

    latest_snapshot_id_file = latest_output_dir / SNAPSHOT_ID_FILE_NAME
    if latest_snapshot_id_file.exists():
        latest_snapshot_id = latest_snapshot_id_file.read_text(encoding="utf-8").strip()
        if latest_snapshot_id == snapshot_id:
            return latest_output_dir

    archived_snapshot_dir = archive_dir / snapshot_id
    if archived_snapshot_dir.exists():
        return archived_snapshot_dir

    raise ValueError("Requested workflow log snapshot was not found.")


def resolve_session_snapshot_dir(project_output_dir: Path, snapshot_id: str) -> Path:
    """Resolve one session snapshot id into its persisted session directory."""

    snapshot_dir = project_output_dir / "sessions" / snapshot_id
    if not snapshot_dir.exists():
        raise ValueError("Requested session log snapshot was not found.")
    return snapshot_dir


def resolve_snapshot_dir_by_id(project_output_dir: Path, snapshot_id: str) -> tuple[str, Path]:
    """Resolve one snapshot id without requiring the caller to name a workspace.

    Public analysis tools treat `snapshot_id` as the real lookup key. This
    helper hides the underlying workflow/session storage split and returns:

    - the resolved workspace name
    - the concrete snapshot directory
    """

    if snapshot_id == "latest":
        return "workflow", resolve_workflow_snapshot_dir(project_output_dir, snapshot_id)

    workflow_root_dir = project_output_dir / "workflow"
    latest_output_dir = workflow_root_dir / "latest"
    latest_snapshot_id_file = latest_output_dir / SNAPSHOT_ID_FILE_NAME
    if latest_snapshot_id_file.exists():
        latest_snapshot_id = latest_snapshot_id_file.read_text(encoding="utf-8").strip()
        if latest_snapshot_id == snapshot_id:
            return "workflow", latest_output_dir

    archived_snapshot_dir = workflow_root_dir / "archive" / snapshot_id
    if archived_snapshot_dir.exists():
        return "workflow", archived_snapshot_dir

    session_snapshot_dir = project_output_dir / "sessions" / snapshot_id
    if session_snapshot_dir.exists():
        return "session", session_snapshot_dir

    raise ValueError("Requested log snapshot was not found.")


def resolve_snapshot_dir(
    project_output_dir: Path,
    workspace: str,
    snapshot_id: str,
) -> Path:
    """Resolve the persisted snapshot directory for one workspace and snapshot id."""

    if workspace == "workflow":
        return resolve_workflow_snapshot_dir(project_output_dir, snapshot_id)
    return resolve_session_snapshot_dir(project_output_dir, snapshot_id)


def find_snapshot_file(
    metadata: LogSnapshotMetadata,
    *,
    source_key: str,
) -> LogSnapshotFilePayload:
    """Return one saved source entry from snapshot metadata."""

    file_payload = next(
        (item for item in metadata.files if item.source_key == source_key),
        None,
    )
    if file_payload is None:
        raise ValueError("Requested log snapshot source_key was not found.")
    return file_payload


def resolve_snapshot_file_path(
    snapshot_dir: Path,
    file_payload: LogSnapshotFilePayload,
) -> Path:
    """Resolve one snapshot file entry back into a file under the snapshot directory.

    Persisted metadata is descriptive, not authoritative for file-system scope.
    Follow-up read/grep operations must re-anchor file access to the already
    authorized snapshot directory instead of trusting the stored `output_file`.
    """

    file_name = file_payload.file_name.strip()
    normalized_file_name = Path(file_name)
    if (
        not file_name
        or normalized_file_name.name != file_name
        or normalized_file_name.is_absolute()
    ):
        raise ValueError("Requested log snapshot file metadata is invalid.")

    resolved_snapshot_dir = snapshot_dir.resolve()
    resolved_file_path = (resolved_snapshot_dir / file_name).resolve()
    if resolved_snapshot_dir not in resolved_file_path.parents:
        raise ValueError("Requested log snapshot file escapes the authorized snapshot directory.")
    if not resolved_file_path.exists():
        raise ValueError("Requested log snapshot file was not found on disk.")
    return resolved_file_path


def build_snapshot_not_found_retry_tips(workspace: str | None = None) -> list[str]:
    """Return explicit follow-up guidance when a persisted snapshot is missing."""

    if workspace == "workflow":
        snapshot_hint = (
            'Retry with snapshot_id="latest" after collect_logs creates a workflow snapshot.'
        )
    elif workspace == "session":
        snapshot_hint = "Retry with the exact session snapshot_id returned by collect_logs."
    else:
        snapshot_hint = (
            "Retry with a snapshot_id returned by collect_logs, or use "
            'snapshot_id="latest" for the newest workflow snapshot.'
        )
    return [
        "Run collect_logs first to create a persisted snapshot for this project and window.",
        snapshot_hint,
    ]


def classify_snapshot_tool_error(message: str, *, default_error_code: str) -> str:
    """Map snapshot follow-up failures to clearer agent-facing error codes."""

    if "source_key" in message or "source_keys" in message:
        return "snapshot_source_key_not_found"
    if "snapshot was not found" in message or "snapshot metadata was not found" in message:
        return "snapshot_not_found"
    if "snapshot metadata is invalid" in message:
        return "invalid_snapshot_metadata"
    return default_error_code


def build_snapshot_tool_error_result(
    *,
    error_code: str,
    message: str,
    retry_tips: list[str],
    details: JSONObject | None = None,
) -> ToolResult:
    """Return one shared agent-facing error result for snapshot follow-up tools."""

    return build_agent_tool_error_result(
        error_code=error_code,
        message=message,
        retry_tips=retry_tips,
        details=details,
    )


def resolve_snapshot_context_or_error(
    *,
    access_token: AccessToken,
    project_name: str | None,
    snapshot_id: str,
    default_error_code: str,
    invalid_retry_tips: list[str],
    details: JSONObject,
    logger: logging.Logger,
    tool_name: str,
    log_context: dict[str, Any] | None = None,
) -> tuple[AuthorizedSnapshotContext | None, ToolResult | None]:
    """Resolve one snapshot context or return a fully built MCP error result."""

    from services.log_snapshots import LogSnapshotService

    snapshot_service = LogSnapshotService(settings, access_token)
    try:
        context = snapshot_service.resolve_snapshot_context_by_snapshot_id(
            project_name,
            snapshot_id,
        )
    except ValueError as error:
        message = str(error)
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": tool_name,
                "error_message": message,
                "snapshot_id": snapshot_id,
                "project_name": project_name,
                **(log_context or {}),
            },
        )
        error_code = classify_snapshot_tool_error(
            message,
            default_error_code=default_error_code,
        )
        retry_tips = (
            build_snapshot_not_found_retry_tips()
            if error_code == "snapshot_not_found"
            else invalid_retry_tips
        )
        return None, build_snapshot_tool_error_result(
            error_code=error_code,
            message=message,
            retry_tips=retry_tips,
            details=details,
        )
    return context, None


def parse_followup_timestamp(value: str) -> datetime:
    """Parse one grouped-analysis timestamp into an aware datetime."""

    candidate = value.strip()
    if candidate.endswith("Z"):
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).astimezone(UTC)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = datetime.strptime(candidate, "%d/%b/%Y:%H:%M:%S %z")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_followup_timestamp(value: datetime) -> str:
    """Format one UTC datetime into the MCP-facing ISO-8601 string."""

    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def select_snapshot_read_chunk(
    full_content: str,
    *,
    start_line: int | None,
    line_count: int | None,
) -> tuple[str, int | None, int | None]:
    """Return one requested line-range chunk from a persisted snapshot file."""

    lines = full_content.splitlines(keepends=True)
    if start_line is None and line_count is None:
        return full_content, 1 if lines else None, len(lines) if lines else 0

    effective_start_line = 1 if start_line is None else start_line
    if effective_start_line > len(lines) and lines:
        raise ValueError("Requested snapshot read range starts beyond the end of the file.")

    start_index = effective_start_line - 1
    end_index = None if line_count is None else start_index + line_count
    selected_lines = lines[start_index:end_index]
    return "".join(selected_lines), effective_start_line, len(selected_lines)
