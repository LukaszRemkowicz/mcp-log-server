"""Shared helpers for deterministic MCP tools."""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastmcp.server.auth import AccessToken

from manifests.loader import load_project_manifest
from manifests.models import SourceDefinition, SourceManifest
from settings import Settings
from tools.models import LogSnapshotFilePayload, LogSnapshotMetadata

RETENTION_DURATION_PATTERN = re.compile(
    r"^(?P<value>\d+)\s*(?P<unit>s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$",
    re.IGNORECASE,
)
SNAPSHOT_METADATA_FILE_NAME = "snapshot_metadata.json"
SNAPSHOT_ID_FILE_NAME = "snapshot_id.txt"
COLLECTED_AT_FILE_NAME = "collected_at.txt"


def load_authorized_project_manifest(
    settings: Settings,
    access_token: AccessToken,
    requested_project_name: str | None,
) -> tuple[SourceManifest, str, str]:
    """Resolve and authorize one project manifest for deterministic project tools."""

    authorized_project_name = str(access_token.claims.get("project_key") or "").strip()
    if not authorized_project_name:
        raise ValueError("Authenticated access token must include a project_key claim.")

    effective_project_name = requested_project_name or authorized_project_name
    if effective_project_name != authorized_project_name:
        raise ValueError(
            "Requested project key does not match the project_key authorized by the access token."
        )

    manifests_dir = settings.MANIFEST_PATH.parent
    try:
        manifest = load_project_manifest(manifests_dir, effective_project_name)
    except FileNotFoundError as error:
        raise ValueError(
            f"Unknown project {effective_project_name!r}. No manifest file was "
            "found for that project."
        ) from error
    if manifest.project_key != effective_project_name:
        raise ValueError("Requested project key does not match the loaded manifest project_key.")

    return manifest, authorized_project_name, effective_project_name


def resolve_container_source_definition(
    manifest: SourceManifest,
    source_key: str,
) -> SourceDefinition:
    """Return one docker source definition enabled for container inspection."""

    definition = next(
        (source for source in manifest.sources if source.source_key == source_key),
        None,
    )
    if definition is None:
        raise ValueError("Requested source_key was not found in the configured manifest.")
    if definition.source_type != "docker":
        raise ValueError("Container file inspection is only available for docker sources.")
    if not definition.inspect_path_prefixes:
        raise ValueError("Container file inspection is not enabled for the requested source.")
    return definition


def generate_snapshot_id(prefix: str) -> str:
    """Return a unique server-owned identifier for persisted workflow snapshots.

    The collection flow writes durable snapshot folders to disk, so workflow
    archives need names that are:

    - unique across repeated runs
    - readable enough for operators and tests to inspect
    - stable enough to return in MCP payloads and reuse later

    The generated identifier combines:

    - a semantic prefix such as `workflow`
    - a UTC timestamp for ordering/debugging
    - a short random suffix to avoid collisions

    This helper is intentionally only for server-owned snapshot naming. Session
    workspaces use the caller-provided `session_id` instead, because the agent
    is expected to decide whether it is continuing an existing session or
    starting a new one.
    """

    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{prefix}_{timestamp}_{secrets.token_hex(4)}"


def parse_snapshot_retention(value: str) -> timedelta:
    """Parse one snapshot retention setting like `30d`, `7days`, `1h`, or `10m`."""

    normalized_value = value.strip()
    duration_match = RETENTION_DURATION_PATTERN.fullmatch(normalized_value)
    if duration_match is None:
        raise ValueError(
            f"Invalid snapshot retention value {value!r}. Use values like 10m, 1h, 7d, or 30days."
        )

    duration_value = int(duration_match.group("value"))
    duration_unit = duration_match.group("unit").lower()
    if duration_unit in {"s", "sec", "secs", "second", "seconds"}:
        return timedelta(seconds=duration_value)
    if duration_unit in {"m", "min", "mins", "minute", "minutes"}:
        return timedelta(minutes=duration_value)
    if duration_unit in {"h", "hr", "hrs", "hour", "hours"}:
        return timedelta(hours=duration_value)
    return timedelta(days=duration_value)


def cleanup_old_snapshot_dirs(root_dir: Path, *, retention: timedelta) -> None:
    """Delete snapshot directories older than the configured retention window.

    This is quiet on missing roots because "nothing has been collected yet" is
    a normal state, not an agent-facing error.
    """

    if not root_dir.exists():
        return

    cutoff = datetime.now(UTC) - retention
    for entry in root_dir.iterdir():
        if not entry.is_dir():
            continue
        entry_modified_at = datetime.fromtimestamp(entry.stat().st_mtime, UTC)
        if entry_modified_at < cutoff:
            shutil.rmtree(entry)


def load_snapshot_metadata_from_json(metadata_json: str) -> LogSnapshotMetadata:
    """Load one current snapshot metadata file into the typed tool contract."""

    return LogSnapshotMetadata.model_validate_json(metadata_json)


def rewrite_snapshot_metadata_output_paths(snapshot_dir: Path) -> None:
    """Rewrite metadata paths so archived snapshots point at archived files.

    Workflow `latest` snapshots are moved into an archive directory on rollover.
    After the move, each file entry must be rewritten so later read/grep calls
    operate on the archived files rather than stale `workflow/latest/...` paths.
    """

    metadata_file = snapshot_dir / SNAPSHOT_METADATA_FILE_NAME
    if not metadata_file.exists():
        return

    metadata = load_snapshot_metadata_from_json(metadata_file.read_text(encoding="utf-8"))
    rewritten_files: list[LogSnapshotFilePayload] = []
    for item in metadata.files:
        rewritten_files.append(
            item.model_copy(
                update={"output_file": str(snapshot_dir / item.file_name)},
            )
        )

    rewritten_metadata = metadata.model_copy(update={"files": rewritten_files})
    metadata_file.write_text(
        json.dumps(rewritten_metadata.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
