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

from pathlib import Path

from tools.models import CollectedSourcePayload, LogSnapshotFilePayload, LogSnapshotMetadata
from tools.utils import (
    SNAPSHOT_ID_FILE_NAME,
    SNAPSHOT_METADATA_FILE_NAME,
    load_snapshot_metadata_from_json,
)


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
