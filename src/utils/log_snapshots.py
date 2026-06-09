"""Pure helpers for persisted log snapshot metadata and filesystem layout.

These helpers intentionally avoid service state. They encode deterministic
rules for:

- loading snapshot metadata from disk
- resolving workflow latest/archive directories
- formatting timestamp windows

`LogSnapshotService` uses them as building blocks while keeping orchestration,
authorization, persistence, and grep execution in the service layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

COLLECTION_DIAGNOSTICS_SOURCE_KEY = "__collection_diagnostics"
COLLECTION_DIAGNOSTICS_FILE_NAME = "collection_diagnostics.json"
COLLECTION_DIAGNOSTICS_DESCRIPTION = "Deterministic collection runtime diagnostics."


def is_collection_diagnostics_source_key(source_key: str) -> bool:
    """Return whether a source key points at the collection diagnostics artifact."""

    return source_key == COLLECTION_DIAGNOSTICS_SOURCE_KEY


def resolve_workflow_snapshot_dir(project_output_dir: Path, archive_name: str | None) -> Path:
    """Resolve a workflow snapshot directory from latest/archive request inputs.

    Passing `archive_name=None` selects the current `latest` directory. Passing
    an archive name selects `archive/<archive_name>` under the project workflow
    directory. This helper only resolves directory existence; it does not read
    inventory metadata or validate source files.

    Raises:
        ValueError: When the requested latest/archive directory does not exist.
    """

    latest_output_dir = project_output_dir / "latest"
    archive_dir = project_output_dir / "archive"

    if archive_name is None:
        if not latest_output_dir.exists():
            raise ValueError("Requested workflow log snapshot was not found.")
        return latest_output_dir

    archived_snapshot_dir = archive_dir / archive_name
    if archived_snapshot_dir.exists():
        return archived_snapshot_dir

    raise ValueError("Requested workflow log snapshot was not found.")


def build_snapshot_not_found_retry_tips(workspace: str | None = None) -> list[str]:
    """Return caller-facing retry guidance for a missing persisted snapshot.

    The advice is workspace-specific because workflow snapshots are addressed
    by `project_name` plus optional `archive_name`, while session snapshots are
    addressed by `session_id` plus `project_name`.
    """

    if workspace == "workflow":
        snapshot_hint = (
            "Retry without archive_name for the newest workflow artifact, "
            "or with a valid archive_name from the workflow archive."
        )
    elif workspace == "session":
        snapshot_hint = "Retry with the exact session_id and project_name used in collect_logs."
    else:
        snapshot_hint = (
            "Retry with session_id plus project_name for session artifacts, "
            "or with project_name plus an optional archive_name for workflow artifacts."
        )
    return [
        "Run collect_logs first to create a persisted snapshot for this project and window.",
        snapshot_hint,
    ]


def classify_snapshot_tool_error(message: str, *, default_error_code: str) -> str:
    """Map low-level snapshot failure text to stable MCP error codes.

    Snapshot services raise `ValueError` with human-readable messages. Tool
    responses need stable `error_code` values so agents can branch reliably.
    This helper keeps that text-to-code mapping centralized for snapshot
    follow-up tools.
    """

    if "source_key" in message or "source_keys" in message:
        return "snapshot_source_key_not_found"
    if "snapshot was not found" in message or "snapshot metadata was not found" in message:
        return "snapshot_not_found"
    if "snapshot metadata is invalid" in message:
        return "invalid_snapshot_metadata"
    return default_error_code


def parse_followup_timestamp(value: str) -> datetime:
    """Parse a timestamp returned by grouped analysis into UTC.

    Supported inputs are ISO-8601 timestamps with optional `Z`, naive ISO
    timestamps, and common nginx-style values such as
    `29/Apr/2026:10:11:00 +0000`. Naive inputs are treated as UTC.

    Raises:
        ValueError: When the value does not match any supported timestamp
            format.
    """

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
    """Format a datetime as the UTC `collect_logs` timestamp string.

    The result is second-granularity ISO-8601 with a trailing `Z`, matching the
    `since`/`until` values returned by `suggest_followup_window`.
    """

    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
